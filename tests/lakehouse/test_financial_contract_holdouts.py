"""Adversarial imported contracts: correct cells cannot authorize wrong financial math."""

from pathlib import Path
import tempfile
import unittest

import duckdb

from agentic_analytics.agent.tools.charts import ChartTools
from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.agent.tools.statistics import StatisticsError, StatisticsTools
from agentic_analytics.lakehouse.financial_semantics import cumulative_evidence
from agentic_analytics.lakehouse.service import LakehouseService, PlanError
from agentic_analytics.lakehouse.store import LakehouseStore


class FinancialContractHoldouts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with duckdb.connect(str(self.root / "source.duckdb")) as db:
            db.execute("CREATE TABLE seed(value INTEGER)")
        self.store = LakehouseStore(self.root / "store")
        snapshot = self.store.publish_snapshot(self.root / "source.duckdb")
        self.store.create_workspace(snapshot["snapshot_id"], "financial_holdouts")
        self.docs = DocumentTools(self.store, "financial_holdouts")
        self.service = LakehouseService(self.store, "financial_holdouts")

    def publish(self, content, definitions):
        path = self.docs.upload_root / "source.csv"
        path.write_text(content)
        source = self.docs.register_upload(path)
        inspection = self.docs.inspect_source(source_id=source["source_id"])
        contract = {"name": "Synthetic financial holdout", "frequency": "monthly", "date_column": "month", "key": ["month"], "grain": ["month"],
                    "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False}, **definitions}}
        return self.docs.publish_selected_table(source["source_id"], "table_001", contract,
            self.store.workspace("financial_holdouts")["version"],
            column_mapping=dict(zip(inspection["tables"][0]["columns"], contract["columns"])),
            unit_evidence={name: "TRY" for name in definitions})

    @staticmethod
    def money(kind="stock", **extra):
        return {"dtype": "integer", "unit": "TRY", "currency": "TRY", "scale": 1, "kind": kind, "nullable": False, **extra}

    def test_nominal_and_declared_constant_price_values_cannot_be_divided(self):
        publication = self.publish("month,nominal (TRY),constant (TRY)\n2026-01,200,100\n", {
            "nominal": self.money(), "constant": self.money(price_basis="2021-01", price_scope="consumer_prices")})
        dataset = publication["dataset_id"]
        plan = {"start": "2026-01", "end": "2026-01", "frequency": "monthly", "columns": [
            {"name": name, "metric_id": f"overlay:{dataset}:{name}"} for name in ("nominal", "constant")]}
        native = self.service.execute(plan)
        self.assertEqual("2021-01", native["schema"]["constant"]["price_basis"])
        self.assertIsNone(native["schema"]["nominal"]["price_basis"])
        chart = ChartTools(self.store, "financial_holdouts").get_chart(native["analysis_id"])
        self.assertEqual("panels", chart["spec"]["layout"])
        plan["operations"] = [{"op": "ratio", "column": "constant", "denominator": "nominal", "output": "invalid_share",
                               "scope_policy": "explicit_comparison", "scope_reason": "Even an intentional comparison cannot erase incompatible price bases."}]
        with self.assertRaises(PlanError) as error:
            self.service.execute(plan)
        self.assertEqual("UNIT_MISMATCH", error.exception.code)

    def test_ytd_values_are_readable_but_cannot_silently_be_summed_as_monthly_flows(self):
        publication = self.publish("month,profit (TRY)\n2026-01,100\n2026-02,135\n2026-03,160\n", {
            "profit": self.money("flow", temporal_semantics="year_to_date_flow")})
        selection = {"name": "profit", "metric_id": f"overlay:{publication['dataset_id']}:profit"}
        native = self.service.execute({"start": "2026-01", "end": "2026-03", "frequency": "monthly", "columns": [selection]})
        self.assertEqual([100, 135, 160], [row["profit"] for row in native["preview"]])
        self.assertEqual("review_required", native["schema"]["profit"]["status"])
        self.assertFalse(native["schema"]["profit"]["additive_over_time"])
        self.assertTrue(native["schema"]["profit"]["cumulative_evidence"])
        with self.assertRaises(StatisticsError) as statistical_error:
            StatisticsTools(self.store, "financial_holdouts").rolling_anomalies(native["analysis_id"], "profit", window=3, min_history=3)
        self.assertEqual("SEMANTICS_REVIEW_REQUIRED", statistical_error.exception.code)
        with self.assertRaises(PlanError) as error:
            self.service.execute({"start": "2026-Q1", "end": "2026-Q1", "frequency": "quarterly", "columns": [{**selection, "alignment": "sum"}]})
        self.assertEqual("SEMANTICS_REVIEW_REQUIRED", error.exception.code)

    def test_declared_semantics_do_not_infer_ytd_from_an_increasing_flow(self):
        self.assertFalse(cumulative_evidence({"source_semantics": "non_cumulative_flow"}))
        self.assertTrue(cumulative_evidence({"source_semantics": "year-to-date profit"}))
        self.assertTrue(cumulative_evidence({"temporal_basis": "year_to_date"}))
        publication = self.publish("month,profit (TRY)\n2026-01,100\n2026-02,135\n2026-03,160\n", {"profit": self.money("flow")})
        result = self.service.execute({"start": "2026-Q1", "end": "2026-Q1", "frequency": "quarterly", "columns": [
            {"name": "profit", "metric_id": f"overlay:{publication['dataset_id']}:profit", "alignment": "sum"}]})
        self.assertEqual(395, result["preview"][0]["profit"])

    def test_unresolved_source_interval_preserves_review_without_claiming_cumulative(self):
        publication = self.publish("month,profit (TRY)\n2026-01,100\n2026-02,135\n2026-03,160\n", {
            "profit": self.money("flow", status="review_required", temporal_semantics="reported_interval_unresolved", additive_over_time=False)})
        selection = {"name": "profit", "metric_id": f"overlay:{publication['dataset_id']}:profit"}
        plan = {"start": "2026-01", "end": "2026-03", "frequency": "monthly", "columns": [selection]}
        result = self.service.execute(plan)
        self.assertEqual([100, 135, 160], [row["profit"] for row in result["preview"]])
        self.assertEqual("flow", result["schema"]["profit"]["kind"])
        self.assertEqual("review_required", result["schema"]["profit"]["status"])
        self.assertFalse(result["schema"]["profit"]["cumulative_evidence"])
        with self.assertRaises(PlanError):
            self.service.execute({**plan, "operations": [{"op": "difference", "column": "profit", "periods": 1, "output": "change"}]})

    def test_sampled_monthly_flows_declared_nonadditive_cannot_become_quarter_totals(self):
        publication = self.publish("month,sampled (TRY)\n2026-01,100\n2026-02,135\n2026-03,160\n", {
            "sampled": self.money("flow", additive_over_time=False)})
        with self.assertRaises(PlanError) as error:
            self.service.execute({"start": "2026-Q1", "end": "2026-Q1", "frequency": "quarterly", "columns": [
                {"name": "sampled", "metric_id": f"overlay:{publication['dataset_id']}:sampled", "alignment": "sum"}]})
        self.assertEqual("INVALID_TEMPORAL_AGGREGATION", error.exception.code)

    def test_same_base_date_with_different_price_scopes_cannot_be_compared_as_equivalent(self):
        publication = self.publish("month,consumer (TRY),producer (TRY)\n2026-01,100,200\n", {
            "consumer": self.money(price_basis="2021-01", price_scope="consumer_prices"),
            "producer": self.money(price_basis="2021-01", price_scope="producer_prices")})
        dataset = publication["dataset_id"]
        with self.assertRaises(PlanError) as error:
            self.service.execute({"start": "2026-01", "end": "2026-01", "frequency": "monthly", "columns": [
                {"name": name, "metric_id": f"overlay:{dataset}:{name}"} for name in ("consumer", "producer")],
                "operations": [{"op": "ratio", "column": "consumer", "denominator": "producer", "output": "invalid"}]})
        self.assertEqual("UNIT_MISMATCH", error.exception.code)

    def test_constant_price_source_cannot_be_deflated_a_second_time(self):
        publication = self.publish("month,constant (TRY)\n2026-01,100\n", {"constant": self.money(price_basis="2021-01")})
        with self.service._context() as (_, bindings, _):
            # The index is a synthetic, explicitly reviewed contract. _validate
            # must reject before it needs observations or evaluates any arithmetic.
            bindings["synthetic_cpi"] = {"metric_id": "synthetic_cpi", "title": "Synthetic CPI", "status": "ready", "table": "seed",
                "time_column": "period", "value_column": "value", "dimensions": {}, "native_frequency": "monthly", "kind": "index",
                "unit": "index", "scale": 1, "index_role": "price_deflator", "deflator_currency": "TRY", "price_scope": "consumer_prices"}
            plan = {"start": "2026-01", "end": "2026-01", "frequency": "monthly", "columns": [
                {"name": "constant", "metric_id": f"overlay:{publication['dataset_id']}:constant"}, {"name": "cpi", "metric_id": "synthetic_cpi"}],
                "operations": [{"op": "deflate", "column": "constant", "index": "cpi", "base_period": "2026-01", "output": "invalid_real"}]}
            with self.assertRaises(PlanError) as error:
                self.service._validate(plan, bindings)
            self.assertEqual("UNIT_MISMATCH", error.exception.code)
