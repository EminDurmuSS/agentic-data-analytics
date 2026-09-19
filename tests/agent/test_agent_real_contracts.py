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
from agentic_analytics.agent.tools.bundles import AnalysisBundleTools
from agentic_analytics.agent.tools.selection import AnalysisSelectionTools
from agentic_analytics.lakehouse.store import LakehouseStore, file_sha256


DATABASE = Path(__file__).resolve().parents[2] / "data_pipeline/lakehouse/analytics.duckdb"
PROFIT = "bddk_monthly:table02:53:ef40239f1db4:Toplam"
GOLD_TOTAL = "bddk_finturk:table07:AltinDepoToplam"
PER_PERSON = "bddk_finturk:table06:KisiBasiNakdiKredi"
CPI = "evds:TP.TUKFIY2025.GENEL"
INDUSTRY = "evds:TP.TSANAYMT2021.Y1"
WEEKLY_HOUSING = "bddk_weekly:table289_289_4_total"
MONTHLY_HOUSING = "bddk_monthly:table04:2:fffae80eca08:Toplam"
WEEKLY_HOUSING_RATE = "evds:TP.KTF12"
FINTURK_ISTANBUL_HOUSING = "bddk_finturk:table03:KonutKredisi"
ISTANBUL_HOUSING_SALES_FIRST_PUBLISHED = (
    "tuik_province_housing_sales_first_published:housing_sales_total_count"
)


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

    def test_w005_w006_istanbul_sales_extension_preserves_2024_cells_and_lineage(self):
        expected_2023 = [17415, 14980, 18166, 13944, 18435, 13578,
                         15724, 17408, 15247, 14941, 15187, 23714]
        expected_2024 = [13423, 16344, 19040, 12406, 18814, 13025,
                         19047, 19467, 21314, 24812, 26320, 35201]
        plan = {"start": "2024-01", "end": "2024-12", "frequency": "monthly",
                "columns": [{"name": "housing_sales", "metric_id": ISTANBUL_HOUSING_SALES_FIRST_PUBLISHED,
                             "dimensions": {"province_key": "istanbul"}, "alignment": "native"}]}

        first_runtime, first_client = self.runtime([call("execute", plan), final()])
        first = first_runtime.run(
            "2024 yılında İstanbul'da aylık toplam konut satışlarını göster.",
            request_id="w005-istanbul-sales",
        )
        self.assertEqual("completed", first["status"])
        before, before_manifest = self.store.load_analysis(first["analysis_id"])
        self.assertEqual([f"2024-{month:02d}" for month in range(1, 13)], before.period.tolist())
        self.assertEqual(expected_2024, before.housing_sales.tolist())
        self.assertEqual(239213, int(before.housing_sales.sum()))
        self.assertEqual("count", before_manifest["schema"]["housing_sales"]["unit"])
        self.assertEqual("flow", before_manifest["schema"]["housing_sales"]["kind"])
        self.assertEqual("monthly", before_manifest["plan"]["frequency"])
        self.assertEqual(
            ISTANBUL_HOUSING_SALES_FIRST_PUBLISHED,
            before_manifest["plan"]["columns"][0]["metric_id"],
        )
        self.assertEqual({"province_key": "istanbul"}, before_manifest["plan"]["columns"][0]["dimensions"])
        self.assertIn(
            ISTANBUL_HOUSING_SALES_FIRST_PUBLISHED,
            first_client.requests[0][0]["content"],
        )

        proof_before = self.store.load_analysis(first["analysis_id"])[1]["lineage"]["sources"]["housing_sales"]
        revision = {"analysis_id": first["analysis_id"], "start": "2023-01"}
        second_runtime, second_client = self.runtime([call("revise_analysis", revision), final()])
        second = second_runtime.run(
            "Pekâlâ, şimdi bu tabloya 2023 yılı İstanbul toplam konut satışlarını da ekle.",
            conversation_id=first["conversation_id"],
            request_id="w006-istanbul-sales",
        )
        self.assertEqual("completed", second["status"])
        after, after_manifest = self.store.load_analysis(second["analysis_id"])
        self.assertEqual([f"2023-{month:02d}" for month in range(1, 13)] +
                         [f"2024-{month:02d}" for month in range(1, 13)], after.period.tolist())
        self.assertEqual(expected_2023, after.loc[after.period.str.startswith("2023"), "housing_sales"].tolist())
        self.assertEqual(expected_2024, after.loc[after.period.str.startswith("2024"), "housing_sales"].tolist())
        pd.testing.assert_frame_equal(before, after.loc[after.period.str.startswith("2024")].reset_index(drop=True))
        self.assertEqual(first["analysis_id"], after_manifest["parent_analysis_id"])
        self.assertEqual(plan["columns"], after_manifest["plan"]["columns"])
        self.assertEqual("2023-01", after_manifest["plan"]["start"])
        self.assertEqual("2024-12", after_manifest["plan"]["end"])
        source_after = after_manifest["lineage"]["sources"]["housing_sales"]
        self.assertEqual(
            proof_before["binding"]["metric_id"],
            source_after["binding"]["metric_id"],
        )
        self.assertEqual(
            proof_before["binding"]["binding_sha256"],
            source_after["binding"]["binding_sha256"],
        )
        self.assertEqual(
            "first_official_publication_for_each_reference_month",
            after_manifest["schema"]["housing_sales"]["vintage_policy"],
        )
        cells_2024 = [
            cell
            for period, cells in source_after["cells"].items()
            if period.startswith("2024-")
            for cell in cells
        ]
        self.assertEqual(12, len(cells_2024))
        self.assertTrue(all(cell["source_press_id"] for cell in cells_2024))
        self.assertTrue(all(cell["source_cell"].startswith("t2!") for cell in cells_2024))
        self.assertTrue(all(len(cell["source_sha256"]) == 64 for cell in cells_2024))
        self.assertIn(first["analysis_id"], second_client.requests[0][0]["content"])
        self.assertIn('"start":"2024-01"', second_client.requests[0][0]["content"])

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

    def test_q031_q035_weekly_housing_flow_preserves_observed_dates_and_deterministic_peak(self):
        expected_dates = [
            "2026-01-02", "2026-01-09", "2026-01-16", "2026-01-23", "2026-01-30",
            "2026-02-06", "2026-02-13", "2026-02-20", "2026-02-27", "2026-03-06",
            "2026-03-13", "2026-03-19", "2026-03-27",
        ]
        expected_sector = [
            679216.0, 682408.0, 684418.0, 687704.0, 691443.0, 695613.0, 701611.0,
            707545.0, 715973.0, 723545.0, 731311.0, 734860.0, 740789.0,
        ]

        def describe_selected(messages):
            discovery = tool_result(messages, "weekly-discover")
            selected = next(card for card in discovery["metrics"] if card["metric_id"] == WEEKLY_HOUSING)
            self.assertEqual("ready", selected["status"])
            self.assertEqual("weekly_observed", selected["native_frequency"])
            return call("describe", {"metric_id": selected["metric_id"]}, "weekly-describe")

        runtime, _ = self.runtime([
            call("discover", {"query": "BDDK haftalık konut kredisi sektör toplamı", "limit": 10}, "weekly-discover"),
            describe_selected,
            final("Haftalık konut kredisi adayları ve kaynak sözleşmesi doğrulandı."),
        ])
        q031 = runtime.run(
            "BDDK haftalık kataloğunda konut kredisiyle ilgili metrikleri ara. Kod, ad, kaynak tablo, kapsam, birim ve doğal frekansı listele.",
            request_id="q031-weekly-housing",
        )
        self.assertEqual("completed", q031["status"])
        description = q031["tool_results"][-1]["result"]["metric"]
        self.assertEqual("stock", description["kind"])
        self.assertEqual("TRY", description["unit"])
        self.assertEqual(1_000_000, description["scale"])
        self.assertEqual("source_date_stock", description["temporal_semantics"])
        self.assertEqual("provisional_revisable", description["revision_status"])
        self.assertEqual(
            "https://www.bddk.org.tr/BultenHaftalik/tr/Home/Aciklama",
            description["source_metadata_url"],
        )
        self.assertEqual(
            {"provisional_revisable_weekly_bulletin", "domestic_and_foreign_branches"},
            {warning["code"] for warning in description["scope_caveats"]},
        )

        sector_plan = {
            "start": expected_dates[0],
            "end": expected_dates[-1],
            "frequency": "weekly_observed",
            "columns": [{
                "name": "housing_credit",
                "metric_id": WEEKLY_HOUSING,
                "dimensions": {"group_code": 10001},
                "alignment": "native",
            }],
        }
        runtime, _ = self.runtime([call("execute", sector_plan, "weekly-sector"), final()])
        q032 = runtime.run(
            "Bu adaylardan sektör toplamını seçip 2026'nın ilk 13 haftasını getir; nedenini de söyle.",
            conversation_id=q031["conversation_id"],
            request_id="q032-weekly-housing",
        )
        self.assertEqual("completed", q032["status"])
        sector, sector_manifest = self.store.load_analysis(q032["analysis_id"])
        self.assertEqual(expected_dates, sector["period"].tolist())
        self.assertEqual(expected_sector, sector["housing_credit"].tolist())
        self.assertNotIn("2026-03-20", sector["period"].tolist())
        self.assertEqual("weekly_observed", sector_manifest["plan"]["frequency"])
        self.assertEqual({"group_code": 10001}, sector_manifest["plan"]["columns"][0]["dimensions"])

        difference = {
            "analysis_id": q032["analysis_id"],
            "operations": [{
                "op": "difference",
                "column": "housing_credit",
                "output": "weekly_change",
                "periods": 1,
                "prior_scope": "selected_window",
            }],
        }
        runtime, _ = self.runtime([call("revise_analysis", difference, "weekly-difference"), final()])
        q033 = runtime.run(
            "Şimdi haftalık ardışık farkları ekle. İlk hafta boş kalsın ve gerçek gözlem tarihlerini koru.",
            conversation_id=q031["conversation_id"],
            request_id="q033-weekly-housing",
        )
        self.assertEqual("completed", q033["status"])
        changed, changed_manifest = self.store.load_analysis(q033["analysis_id"])
        self.assertTrue(pd.isna(changed.loc[0, "weekly_change"]))
        self.assertEqual(3192.0, changed.loc[1, "weekly_change"])
        self.assertEqual(8428.0, changed.loc[8, "weekly_change"])
        self.assertEqual("selected_window", changed_manifest["plan"]["operations"][-1]["prior_scope"])
        pd.testing.assert_frame_equal(sector, changed[["period", "housing_credit"]])

        grouped_request = {
            "metric_id": WEEKLY_HOUSING,
            "group_by": "group_code",
            "dimensions": {},
            "start": expected_dates[0],
            "end": expected_dates[-1],
            "frequency": "weekly_observed",
            "alignment": "native",
            "order": "desc",
            "limit": 100,
            "operations": [difference["operations"][0] | {"column": "value"}],
        }
        runtime, _ = self.runtime([call("query_grouped", grouped_request, "weekly-groups"), final()])
        q034 = runtime.run(
            "Aynı metriği banka grupları için de karşılaştır; tarihler aynı olsun ve farklı metrikleri karıştırma.",
            conversation_id=q031["conversation_id"],
            request_id="q034-weekly-housing",
        )
        self.assertEqual("completed", q034["status"])
        grouped, grouped_manifest = self.store.load_analysis(q034["analysis_id"])
        self.assertEqual(7, grouped["group_code"].nunique())
        self.assertEqual(91, len(grouped))
        for _, rows in grouped.groupby("group_code"):
            self.assertEqual(expected_dates, rows.sort_values("period")["period"].tolist())
            self.assertTrue(pd.isna(rows.sort_values("period").iloc[0]["weekly_change"]))
        self.assertEqual("group_code", grouped_manifest["lineage"]["group_by"])
        self.assertEqual(WEEKLY_HOUSING, grouped_manifest["plan"]["request"]["metric_id"])

        selection_tools = AnalysisSelectionTools(self.store, self.workspace_id)
        selection = {
            "analysis_id": q034["analysis_id"],
            "filters": [{"column": "weekly_change", "op": "not_null"}],
            "columns": ["period", "group_code", "value", "weekly_change"],
            "sort": {"column": "weekly_change", "direction": "desc", "absolute": True},
            "limit": 1,
        }
        runtime, _ = self.runtime(
            [call("select_analysis_rows", selection, "weekly-peak"), final()],
            extra_tools=selection_tools.extra_tools(),
        )
        q035 = runtime.run(
            "En büyük mutlak haftalık değişim ne zaman olmuş? İki tarih, iki değer ve metrik kodunu göster.",
            conversation_id=q031["conversation_id"],
            request_id="q035-weekly-housing",
        )
        self.assertEqual("completed", q035["status"])
        peak = q035["tool_results"][0]["result"]
        self.assertEqual(84, peak["total_match_count"])
        self.assertEqual(1, peak["row_count"])
        self.assertEqual({
            "period": "2026-02-27",
            "group_code": 10001,
            "value": 715973.0,
            "weekly_change": 8428.0,
        }, peak["rows"][0])
        previous = grouped[(grouped["group_code"] == 10001) & (grouped["period"] == "2026-02-20")].iloc[0]
        self.assertEqual(707545.0, previous["value"])
        self.assertEqual(
            grouped_manifest["data_sha256"],
            peak["provenance"]["data_sha256"],
        )

    def test_q046_q050_mixed_frequencies_remain_separate_and_survive_followups(self):
        bundle_tools = AnalysisBundleTools(self.store, self.workspace_id)
        monthly_plan = {
            "start": "2025-01", "end": "2025-12", "frequency": "monthly",
            "columns": [{
                "name": "housing_credit",
                "metric_id": MONTHLY_HOUSING,
                "dimensions": {"group_code": 10001},
                "alignment": "native",
            }],
        }
        weekly_plan = {
            "start": "2025-01-03", "end": "2025-12-26", "frequency": "weekly_friday",
            "columns": [{
                "name": "housing_rate",
                "metric_id": WEEKLY_HOUSING_RATE,
                "alignment": "native",
            }],
        }
        quarterly_plan = {
            "start": "2025-Q1", "end": "2025-Q4", "frequency": "quarterly",
            "columns": [{
                "name": "istanbul_housing_credit",
                "metric_id": FINTURK_ISTANBUL_HOUSING,
                "dimensions": {"group_code": 10001, "city": "İSTANBUL"},
                "alignment": "native",
            }],
        }

        def save_native_bundle(messages):
            return call("save_analysis_bundle", {
                "components": [
                    {"analysis_id": tool_result(messages, "q046-monthly")["analysis_id"],
                     "role": "native_monthly_credit", "label": "BDDK aylık konut kredisi stoku"},
                    {"analysis_id": tool_result(messages, "q046-weekly")["analysis_id"],
                     "role": "native_weekly_rate", "label": "TCMB haftalık konut kredisi faizi"},
                    {"analysis_id": tool_result(messages, "q046-quarterly")["analysis_id"],
                     "role": "quarterly_istanbul_credit", "label": "FinTürk İstanbul çeyreklik konut kredisi"},
                ],
                "title": "2025 konut finansmanı doğal frekans görünümü",
                "purpose": "Kaynak frekanslarını, birimleri ve stok veya oran niteliklerini birlikte korumak",
            }, "q046-bundle")

        runtime, _ = self.runtime([
            call("execute", monthly_plan, "q046-monthly"),
            call("execute", weekly_plan, "q046-weekly"),
            call("execute", quarterly_plan, "q046-quarterly"),
            save_native_bundle,
            final(),
        ], extra_tools=bundle_tools.extra_tools(), max_decisions=7)
        q046 = runtime.run(
            "2025 yılı için BDDK toplam konut kredisi stoku, TCMB konut kredisi faiz oranı ve FinTürk İstanbul konut kredisi değerini bul. Bu üç serinin doğal frekansını, birimini ve stok/akım/oran niteliğini metadata'ya dayanarak açıkla.",
            request_id="q046-mixed-frequency",
        )
        self.assertEqual("completed", q046["status"])
        native_bundle = bundle_tools.load_bundle(q046["analysis_bundle_id"])
        self.assertEqual(["monthly", "quarterly", "weekly_friday"], native_bundle["frequencies"])
        native_roles = {component["role"]: component for component in native_bundle["components"]}
        self.assertEqual({
            "native_monthly_credit", "native_weekly_rate", "quarterly_istanbul_credit",
        }, set(native_roles))
        monthly, monthly_manifest = self.store.load_analysis(native_roles["native_monthly_credit"]["analysis_id"])
        weekly, weekly_manifest = self.store.load_analysis(native_roles["native_weekly_rate"]["analysis_id"])
        quarterly, quarterly_manifest = self.store.load_analysis(native_roles["quarterly_istanbul_credit"]["analysis_id"])
        self.assertEqual(12, len(monthly))
        self.assertEqual(52, len(weekly))
        self.assertEqual(4, len(quarterly))
        self.assertEqual(["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"], quarterly["period"].tolist())
        self.assertEqual([164592149.0, 173567113.0, 183625709.0, 197406766.0], quarterly["istanbul_housing_credit"].tolist())
        self.assertEqual("stock", monthly_manifest["schema"]["housing_credit"]["kind"])
        self.assertEqual(1_000_000, monthly_manifest["schema"]["housing_credit"]["scale"])
        self.assertEqual("rate", weekly_manifest["schema"]["housing_rate"]["kind"])
        self.assertEqual("percent", weekly_manifest["schema"]["housing_rate"]["unit"])
        self.assertEqual("stock", quarterly_manifest["schema"]["istanbul_housing_credit"]["kind"])
        self.assertEqual(1_000, quarterly_manifest["schema"]["istanbul_housing_credit"]["scale"])

        aligned_plan = {
            "start": "2025-01", "end": "2025-12", "frequency": "monthly",
            "columns": [
                monthly_plan["columns"][0],
                {"name": "monthly_housing_rate", "metric_id": WEEKLY_HOUSING_RATE, "alignment": "mean"},
            ],
        }

        def save_aligned_bundle(messages):
            return call("save_analysis_bundle", {
                "parent_bundle_id": q046["analysis_bundle_id"],
                "components": [{
                    "analysis_id": tool_result(messages, "q047-aligned")["analysis_id"],
                    "role": "monthly_aligned_view",
                    "label": "Aylık kredi stoku ve gözlenen haftalık faizlerin aylık ortalaması",
                }],
                "title": "2025 konut finansmanı hizalanmış ve doğal frekans görünümleri",
            }, "q047-bundle")

        runtime, _ = self.runtime([
            call("execute", aligned_plan, "q047-aligned"),
            save_aligned_bundle,
            final(),
        ], extra_tools=bundle_tools.extra_tools(), max_decisions=5)
        q047 = runtime.run(
            "Peki aylık krediyle haftalık faizi aylık bir görünümde birleştirir misin? Kullandığın agregasyon kuralını ve nedenini yaz.",
            conversation_id=q046["conversation_id"],
            request_id="q047-mixed-frequency",
        )
        self.assertEqual("completed", q047["status"])
        aligned_bundle = bundle_tools.load_bundle(q047["analysis_bundle_id"])
        aligned_component = next(component for component in aligned_bundle["components"]
                                 if component["role"] == "monthly_aligned_view")
        aligned, aligned_manifest = self.store.load_analysis(aligned_component["analysis_id"])
        self.assertEqual(12, len(aligned))
        self.assertAlmostEqual(40.536, aligned.loc[0, "monthly_housing_rate"])
        self.assertAlmostEqual(37.29, aligned.loc[11, "monthly_housing_rate"])
        self.assertEqual("mean", aligned_manifest["plan"]["columns"][1]["alignment"])
        self.assertIn("observed_sample_mean", {warning["code"] for warning in aligned_manifest["lineage"]["warnings"]})
        aligned_source = next(source for source in aligned_component["sources"]
                              if source["metric_id"] == WEEKLY_HOUSING_RATE)
        self.assertEqual("weekly_friday", aligned_source["native_frequency"])
        self.assertEqual("monthly", aligned_source["output_frequency"])
        self.assertEqual("mean", aligned_source["alignment"])

        runtime, _ = self.runtime([
            call("save_analysis_bundle", {
                "parent_bundle_id": q047["analysis_bundle_id"],
                "components": [{
                    "analysis_id": native_roles["quarterly_istanbul_credit"]["analysis_id"],
                    "role": "quarterly_istanbul_credit",
                    "label": "FinTürk İstanbul çeyreklik konut kredisi, doğal çeyrek etiketi",
                }],
                "title": "2025 aylık görünüm ve ayrı çeyreklik İstanbul serisi",
            }, "q048-bundle"),
            final(),
        ], extra_tools=bundle_tools.extra_tools(), max_decisions=3)
        q048 = runtime.run(
            "Buna çeyreklik il değerini de ekle; fakat üç aya kopyalama, kendi çeyrek etiketiyle kalsın.",
            conversation_id=q046["conversation_id"],
            request_id="q048-mixed-frequency",
        )
        self.assertEqual("completed", q048["status"])
        quarterly_bundle = bundle_tools.load_bundle(q048["analysis_bundle_id"])
        retained_quarterly = next(component for component in quarterly_bundle["components"]
                                  if component["role"] == "quarterly_istanbul_credit")
        self.assertEqual(["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"], retained_quarterly["period_labels"])
        self.assertEqual(4, retained_quarterly["row_count"])
        self.assertFalse(quarterly_bundle["frequency_policy"]["quarterly_values_copied_to_months"])
        self.assertNotIn("istanbul_housing_credit", aligned.columns)

        q049_text = (
            "Stok serileri zaman boyunca toplanmaz; faiz oranı da tutar gibi toplanmaz. "
            "Yalnız metadata'da additive_over_time=true olan dönem akımları, aynı tanım ve ayrık dönemler altında toplanabilir."
        )
        runtime, q049_client = self.runtime([final(q049_text)], extra_tools=bundle_tools.extra_tools())
        q049 = runtime.run(
            "Şimdi hangilerini zaman boyunca toplayabileceğimizi açıkla. Stokla akımı tek toplam yapma ve geçerli koşulları belirt.",
            conversation_id=q046["conversation_id"],
            request_id="q049-mixed-frequency",
        )
        self.assertEqual("completed", q049["status"])
        self.assertEqual(q049_text, q049["message"])
        self.assertIn(q048["analysis_bundle_id"], q049_client.requests[0][0]["content"])
        self.assertIn("sum_forbidden_stock_rate_ratio_or_unverified", q049_client.requests[0][0]["content"])

        runtime, _ = self.runtime([
            call("plan_task", {"deliverables": ["bundle"]}, "q050-plan"),
            call("save_analysis_bundle", {
                "parent_bundle_id": q048["analysis_bundle_id"],
                "components": [{
                    "analysis_id": aligned_component["analysis_id"],
                    "role": "monthly_aligned_view",
                    "label": "Aylık kredi stoku ve haftalık faizlerin gözlenen aylık ortalaması",
                }],
                "title": "2025 konut finansmanı kaynak ve frekans sözleşmesi",
                "purpose": "Kaynak kimlikleri, frekans dönüşümleri, dönemler ve eksik değer politikasını kaydetmek",
            }, "q050-bundle"),
            final(),
        ], extra_tools=bundle_tools.extra_tools(), max_decisions=5)
        q050 = runtime.run(
            "Son olarak bu analizi kaynak kimlikleri, frekans dönüşümleri, dönem ve eksik değer politikasıyla kaydeder misin?",
            conversation_id=q046["conversation_id"],
            request_id="q050-mixed-frequency",
        )
        self.assertEqual("completed", q050["status"])
        final_bundle = bundle_tools.load_bundle(q050["analysis_bundle_id"])
        self.assertEqual(q048["analysis_bundle_id"], final_bundle["parent_bundle_id"])
        self.assertEqual("preserve_nulls", final_bundle["missing_value_policy"]["mode"])
        self.assertFalse(final_bundle["missing_value_policy"]["zero_fill"])
        self.assertFalse(final_bundle["missing_value_policy"]["forward_fill"])
        self.assertEqual(["monthly", "quarterly", "weekly_friday"], final_bundle["frequencies"])
        metric_ids = {source["metric_id"] for component in final_bundle["components"] for source in component["sources"]}
        self.assertTrue({MONTHLY_HOUSING, WEEKLY_HOUSING_RATE, FINTURK_ISTANBUL_HOUSING} <= metric_ids)
        self.assertIn("Frekans politikası", q050["message"])
        self.assertIn("çeyreklik değer aylara kopyalanmaz", q050["message"])

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
            provenance = proof["lineage"]["inputs"][0]["document_provenance"]
            self.assertEqual(contract["document_provenance"],
                             {key: provenance[key] for key in contract["document_provenance"]})
            self.assertEqual([1, 2], provenance["row_order"]["stored_row_to_source_csv_row"])
            self.assertEqual(file_sha256(source), provenance["row_order"]["source_csv_sha256"])
            executed = next(item["result"] for item in result["tool_results"] if item["tool"] == "execute")
            self.assertEqual(workspace["datasets"][0], executed["schema"]["visits"]["scope"]["namespace"])


if __name__ == "__main__":
    unittest.main()
