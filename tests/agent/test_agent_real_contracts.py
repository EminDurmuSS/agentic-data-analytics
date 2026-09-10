"""Offline runtime integration against the published competition snapshot.

The scripted model selects from actual tool responses; no provider, API key,
network request, or source-database mutation is involved. These are execution
contract tests, not claims about live model accuracy.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import duckdb
import pandas as pd

from agentic_analytics.agent.run_store import AgentRunStore
from agentic_analytics.agent.runtime import AgentRuntime
from agentic_analytics.lakehouse.store import LakehouseStore, file_sha256


DATABASE = Path(__file__).resolve().parents[2] / "data_pipeline/lakehouse/analytics.duckdb"
PROFIT = "bddk_monthly:table02:53:ef40239f1db4:Toplam"
GOLD_TOTAL = "bddk_finturk:table07:AltinDepoToplam"
PER_PERSON = "bddk_finturk:table06:KisiBasiNakdiKredi"
CPI = "evds:TP.TUKFIY2025.GENEL"
INDUSTRY = "evds:TP.TSANAYMT2021.Y1"


def call(name, args, call_id=None):
    return {"content": None, "finish_reason": "stop", "tool_calls": [
        {"id": call_id or name, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def tool_result(messages, call_id):
    return next(json.loads(message["content"]) for message in reversed(messages)
                if message["role"] == "tool" and message["tool_call_id"] == call_id)


def final(message="Hesaplanan tablo ve kaynak referansları kaydedildi."):
    return {"content": message, "tool_calls": [], "finish_reason": "stop"}


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def chat(self, messages, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        if not self.responses:
            raise AssertionError("Runtime made an unexpected additional model request")
        response = self.responses.pop(0)
        return response(messages) if callable(response) else response


@unittest.skipUnless(DATABASE.is_file(), "Published competition snapshot is not available")
class RealSnapshotRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="kkb-real-runtime-")
        cls.root = Path(cls.temp.name)
        cls.source_hash = file_sha256(DATABASE)
        cls.store = LakehouseStore(cls.root / "lakehouse")
        cls.snapshot = cls.store.publish_snapshot(DATABASE)

    @classmethod
    def tearDownClass(cls):
        try:
            if file_sha256(DATABASE) != cls.source_hash:
                raise AssertionError("Integration tests changed the original source database")
        finally:
            cls.temp.cleanup()

    def setUp(self):
        self.workspace = self.store.create_workspace(self.snapshot["snapshot_id"])
        self.workspace_id = self.workspace["workspace_id"]
        self.journal = AgentRunStore(self.root / self.workspace_id)

    def runtime(self, responses, **kwargs):
        client = ScriptedClient(responses)
        return AgentRuntime(self.store, self.workspace_id, client, self.journal, **kwargs), client

    @staticmethod
    def profit_plan(frequency="quarterly", alignment="sum"):
        return {"start": "2026-Q1" if frequency == "quarterly" else "2026-01",
                "end": "2026-Q1" if frequency == "quarterly" else "2026-03",
                "frequency": frequency,
                "columns": [{"name": "profit", "metric_id": PROFIT, "dimensions": {"group_code": 10001}, "alignment": alignment}]}

    def test_total_bank_label_drives_actual_profit_plan_and_source_explanation(self):
        def select_metric(messages):
            candidates = tool_result(messages, "discover")["metrics"]
            selected = next(metric for metric in candidates if metric["metric_id"] == PROFIT)
            self.assertEqual("ready", selected["status"])
            return call("dimension_values", {"metric_id": selected["metric_id"], "dimension": "group_code", "query": "tüm bankalar"})

        def build_plan(messages):
            dimension = tool_result(messages, "dimension_values")
            self.assertEqual(1, dimension["total"])
            group = dimension["values"][0]
            self.assertEqual("Sektör", group["label"])
            self.assertIn("BDDK_MONTHLY", group["scope"])
            self.assertEqual("BDDK_MONTHLY", group["label_evidence"]["namespace"])
            plan = self.profit_plan()
            # The submitted code comes from the actual source label, not a
            # model's prior knowledge that one source happens to use 10001.
            plan["columns"][0]["dimensions"]["group_code"] = group["value"]
            return call("execute", plan)

        def explain(messages):
            executed = tool_result(messages, "execute")
            self.assertEqual(288688, executed["preview"][0]["profit"])
            return call("explain_value", {"analysis_id": executed["analysis_id"], "column": "profit", "period": "2026-Q1"})

        runtime, _ = self.runtime([call("discover", {"query": "bddk_monthly:table02:53", "limit": 10}), select_metric, build_plan, explain, final()])
        result = runtime.run("2026 ilk çeyrekte tüm bankaların dönem net kârını hesapla ve kaynağını göster.")
        self.assertEqual("completed", result["status"])
        proof = result["tool_results"][-1]["result"]
        self.assertEqual(288688, proof["value"])
        self.assertEqual("TRY", proof["schema"]["unit"])
        self.assertEqual(1e6, proof["schema"]["scale"])
        self.assertTrue(proof["source_references_complete"])
        self.assertFalse(proof["source_files_verified"])
        cells = proof["lineage"]["source_cells"]
        self.assertEqual(3, len(cells))
        self.assertTrue(all(len(cell["source_sha256"]) == 64 for cell in cells))
        self.assertEqual(1, len(cells[1]["previous_cumulative_source_cells"]))
        validations = [event for event in self.journal.events(result["run_id"]) if event["kind"] == "plan_validation"]
        self.assertEqual("valid", validations[0]["payload"]["validation"]["status"])

    def test_group_code_labels_retain_their_source_specific_meanings(self):
        runtime, _ = self.runtime([
            call("dimension_values", {"metric_id": PROFIT, "dimension": "group_code"}, "monthly-groups"),
            call("dimension_values", {"metric_id": GOLD_TOTAL, "dimension": "group_code"}, "finturk-groups"), final("Kaynakların grup sözlükleri listelendi.")])
        result = runtime.run("Aylık BDDK ve FinTürk banka gruplarının kodlarını kendi kaynak etiketleriyle göster.")
        self.assertEqual("completed", result["status"])
        monthly, finturk = [{row["value"]: row for row in step["result"]["values"]} for step in result["tool_results"]]
        self.assertEqual("Katılım", monthly[10003]["label"])
        self.assertEqual("KALKINMA VE YATIRIM", finturk[10003]["label"])
        self.assertEqual("Kalkınma ve Yatırım", monthly[10004]["label"])
        self.assertEqual("KATILIM", finturk[10004]["label"])
        self.assertNotEqual(monthly[10003]["scope"], finturk[10003]["scope"])

    def test_real_targeted_revision_preserves_other_cells_and_original_analysis(self):
        plan = self.profit_plan("monthly", "native")
        plan["columns"].append({"name": "industry", "metric_id": INDUSTRY})
        first_runtime, _ = self.runtime([call("execute", plan), final()])
        initial = first_runtime.run("2026 ilk üç ayın kârını ve sanayi endeksini aynı tabloda göster.")
        self.assertEqual("completed", initial["status"])
        before, before_manifest = self.store.load_analysis(initial["analysis_id"])
        revision = {"analysis_id": initial["analysis_id"], "add_columns": [{"name": "cpi", "metric_id": CPI}],
                    "operations": [{"op": "deflate", "column": "profit", "index": "cpi", "base_period": "2026-01", "output": "profit"}]}
        runtime, client = self.runtime([call("revise_analysis", revision), final()])
        revised = runtime.run("Aynı tabloda yalnız kârı Ocak 2026 TÜFE bazında reel göster, diğer hücreleri koru.", conversation_id=initial["conversation_id"])
        self.assertEqual("completed", revised["status"])
        after, manifest = self.store.load_analysis(revised["analysis_id"])
        original_again, original_manifest = self.store.load_analysis(initial["analysis_id"])
        pd.testing.assert_frame_equal(before, original_again)
        self.assertEqual(before_manifest["data_sha256"], original_manifest["data_sha256"])
        pd.testing.assert_frame_equal(before[["period", "industry"]], after[["period", "industry"]])
        self.assertEqual(initial["analysis_id"], manifest["parent_analysis_id"])
        self.assertEqual("2026-01", manifest["schema"]["profit"]["price_basis"])
        expected = before["profit"] / after["cpi"] * after.loc[0, "cpi"]
        pd.testing.assert_series_equal(expected, after["profit"], check_names=False)
        self.assertIn(initial["analysis_id"], client.requests[0][0]["content"])
        self.assertEqual(initial["conversation_id"], revised["conversation_id"])

    def test_grouped_total_gold_ranking_and_explanation_use_the_same_city(self):
        request = {"metric_id": GOLD_TOTAL, "group_by": "city", "dimensions": {"group_code": 10001},
                   "start": "2026-Q2", "end": "2026-Q2", "frequency": "quarterly", "limit": 10}

        def explain_top(messages):
            table = tool_result(messages, "query_grouped")
            top = table["preview"][0]
            return call("explain_value", {"analysis_id": table["analysis_id"], "column": "value", "period": top["period"], "dimensions": {"city": top["city"]}})

        runtime, _ = self.runtime([call("query_grouped", request), explain_top, final()])
        result = runtime.run("2026 ikinci çeyrekte toplam altın mevduatı en yüksek 10 kaynak coğrafyasını sırala, ilk satırın kaynağını göster.")
        self.assertEqual("completed", result["status"])
        table, proof = [item["result"] for item in result["tool_results"]]
        self.assertEqual(82, table["group_count"])
        self.assertEqual(10, table["row_count"])
        self.assertEqual("İSTANBUL", table["preview"][0]["city"])
        self.assertEqual("İSTANBUL", proof["lineage"]["dimensions"]["city"])
        self.assertEqual(GOLD_TOTAL, proof["lineage"]["metric_id"])
        self.assertTrue(proof["source_references_complete"])
        with duckdb.connect(str(DATABASE), read_only=True) as connection:
            expected = connection.execute("SELECT usable_value FROM bddk.finturk_measurements WHERE table_no=7 AND measure_code='AltinDepoToplam' AND group_code=10001 AND city='İSTANBUL' AND quarter='2026-06'").fetchone()[0]
        self.assertEqual(expected, proof["value"])
        self.assertTrue(any(note["code"] == "group_populations_not_summed" for note in table["warnings"]))

    def test_per_person_units_work_but_imported_matching_codes_do_not_share_population(self):
        source = self.root / f"{self.workspace_id}-external.csv"
        source.write_text("quarter,city,group_code,amount\n2026-Q1,ANKARA,10001,500000\n2026-Q2,ANKARA,10001,600000\n", encoding="utf-8")
        contract = {"name": "external_per_person", "source_namespace": "external:example.test:source_fixture",
                    "columns": {"quarter": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                                "city": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False},
                                "group_code": {"dtype": "integer", "unit": "code", "kind": "dimension", "nullable": False},
                                "amount": {"dtype": "float", "unit": "TRY/person", "kind": "ratio", "currency": "TRY", "nullable": False}},
                    "key": ["quarter", "city", "group_code"], "grain": ["quarter", "city", "group_code"], "date_column": "quarter", "frequency": "quarterly"}
        workspace = self.store.ingest_csv(self.workspace_id, source, contract, expected_version=0)
        columns = [{"name": "official", "metric_id": PER_PERSON, "dimensions": {"city": "ANKARA", "group_code": 10001}},
                   {"name": "external", "metric_id": f"overlay:{workspace['datasets'][0]}:amount", "dimensions": {"city": "ANKARA", "group_code": 10001}}]
        plan = {"start": "2026-Q1", "end": "2026-Q2", "frequency": "quarterly", "columns": columns,
                "operations": [{"op": "difference", "column": "official", "output": "change"}, {"op": "growth", "column": "official", "output": "growth"}]}
        runtime, _ = self.runtime([call("execute", plan), final()])
        result = runtime.run("Ankara kişi başı kredinin değişimini göster; dış kaynağı ayrı sütunda tut.")
        self.assertEqual("completed", result["status"])
        table = result["tool_results"][0]["result"]
        self.assertEqual("TRY/person", table["schema"]["change"]["unit"])
        self.assertEqual("percent", table["schema"]["growth"]["unit"])
        self.assertNotEqual(table["schema"]["official"]["scope"]["namespace"], table["schema"]["external"]["scope"]["namespace"])
        self.assertAlmostEqual(49484.422, table["preview"][1]["change"])
        plan["operations"] = [{"op": "ratio", "column": "official", "denominator": "external", "output": "share"}]
        runtime, _ = self.runtime([call("execute", plan)], max_repairs=0)
        rejected = runtime.run("Kodlar aynı diye bu iki kaynağın eşdeğer nüfusu kapsadığını varsay.")
        self.assertEqual("blocked", rejected["status"])
        self.assertEqual("SCOPE_MISMATCH", rejected["errors"][0]["code"])
        self.assertEqual(2, self.store.workspace(self.workspace_id)["version"])

    def test_invalid_current_plan_cannot_be_reported_as_success_with_previous_artifact(self):
        initial_runtime, _ = self.runtime([call("execute", self.profit_plan()), final()])
        initial = initial_runtime.run("Doğru çeyrek kârını hesapla.")
        runtime, _ = self.runtime([call("execute", self.profit_plan(alignment="last")), final("Yeni çeyrek kârı başarıyla hesaplandı.")])
        rejected = runtime.run("Son aylık kârı çeyrek kârı gibi kaydet.", conversation_id=initial["conversation_id"])
        self.assertEqual("blocked", rejected["status"])
        self.assertEqual("INVALID_TEMPORAL_AGGREGATION", rejected["tool_results"][0]["result"]["errors"][0]["code"])
        self.assertEqual(1, self.store.workspace(self.workspace_id)["version"])
        self.assertEqual(initial["analysis_id"], self.store.workspace(self.workspace_id)["analysis_head"])


class GenericRuntimeIntegrationTests(unittest.TestCase):
    def test_blank_snapshot_plus_health_overlay_runs_the_same_typed_agent_tools(self):
        with tempfile.TemporaryDirectory(prefix="kkb-generic-runtime-") as temporary:
            root = Path(temporary)
            database = root / "blank.duckdb"
            with duckdb.connect(str(database)) as connection:
                connection.execute("CREATE TABLE platform_metadata(name VARCHAR)")
                self.assertEqual(0, connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('bddk','evds','catalog')").fetchone()[0])
            store = LakehouseStore(root / "lakehouse")
            snapshot = store.publish_snapshot(database)
            workspace = store.create_workspace(snapshot["snapshot_id"])
            source = root / "clinic.csv"
            source.write_text("month,city,visits\n2026-01,Ankara,100\n2026-02,Ankara,120\n", encoding="utf-8")
            contract = {"name": "clinic_visits", "source_namespace": "external:upload:source_clinic",
                        "document_provenance": {"source_id": "source_clinic", "raw_sha256": file_sha256(source)},
                        "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False}, "city": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False}, "visits": {"dtype": "integer", "unit": "person", "kind": "count_flow", "nullable": False}},
                        "key": ["month", "city"], "grain": ["month", "city"], "date_column": "month", "frequency": "monthly"}
            workspace = store.ingest_csv(workspace["workspace_id"], source, contract, expected_version=0)

            def select_dimension(messages):
                found = tool_result(messages, "discover")
                self.assertEqual(1, found["total"])
                return call("dimension_values", {"metric_id": found["metrics"][0]["metric_id"], "dimension": "city"})

            def execute(messages):
                dimension = tool_result(messages, "dimension_values")
                return call("execute", {"start": "2026-01", "end": "2026-02", "frequency": "monthly", "columns": [{"name": "visits", "metric_id": dimension["metric_id"], "dimensions": {"city": dimension["values"][0]["value"]}}], "operations": [{"op": "growth", "column": "visits", "output": "growth"}]})

            def explain(messages):
                return call("explain_value", {"analysis_id": tool_result(messages, "execute")["analysis_id"], "column": "growth", "period": "2026-02"})

            client = ScriptedClient([call("discover", {"query": "clinic visits"}), select_dimension, execute, explain, final()])
            runtime = AgentRuntime(store, workspace["workspace_id"], client, AgentRunStore(root / "runs"))
            result = runtime.run("Klinik ziyaretlerinde Ankara için aylık büyümeyi hesapla ve kaynağını açıkla.")
            self.assertEqual("completed", result["status"])
            proof = result["tool_results"][-1]["result"]
            self.assertAlmostEqual(20, proof["value"])
            self.assertEqual("percent", proof["schema"]["unit"])
            self.assertTrue(proof["source_references_complete"])
            self.assertFalse(proof["source_files_verified"])
            self.assertEqual(2, len(proof["lineage"]["inputs"]))
            self.assertEqual(contract["source_namespace"], proof["lineage"]["inputs"][0]["source_namespace"])
            self.assertEqual(contract["document_provenance"], proof["lineage"]["inputs"][0]["document_provenance"])
            executed = next(item["result"] for item in result["tool_results"] if item["tool"] == "execute")
            self.assertEqual(workspace["datasets"][0], executed["schema"]["visits"]["scope"]["namespace"])


if __name__ == "__main__":
    unittest.main()
