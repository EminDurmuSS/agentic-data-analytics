import json
import unittest
from pathlib import Path

import pandas as pd

from data_pipeline.bddk.build_monthly_semantic_dataset import (
    apply_transformations,
    metric_semantics,
    source_unit,
    to_long,
    validate_recomposition,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data_pipeline" / "bddk" / "processed" / "monthly_semantic"
RAW = DATA.parent / "monthly_all_groups"
POLICY = json.loads((ROOT / "data_pipeline/bddk/monthly_semantics_v1.json").read_text())


class MonthlySemanticRegressionTests(unittest.TestCase):
    def test_all_60_known_count_and_ratio_variants_override_monetary_caption(self):
        variants = 0
        affected_rows = 0
        for table in (6, 9, 11, 12, 13):
            data = pd.read_parquet(RAW / f"table_{table:02d}.parquet")
            label = data["Ad"].str.strip().str.casefold()
            mask = label.str.contains(r"\(adet\)|mudi sayısı|\(yüzde\)", regex=True) | label.eq("likidite yeterlilik oranı")
            sample = data.loc[mask]
            value_columns = [column for column in data if column not in {
                "month", "table_no", "table_name", "group_code", "source_row_index", "source_caption",
                "source_unit", "source_file", "source_sha256", "source_request_info_file",
                "source_request_info_sha256", "period_validation_source", "BankaAdi", "BasitSira", "Ad", "BasitFont",
            }]
            affected_rows += len(sample) * len(value_columns)
            for _, row in sample.drop_duplicates(["Ad", "BasitSira"]).iterrows():
                for column in value_columns:
                    expected = "count" if table in (6, 9) else "percent"
                    self.assertEqual(expected, source_unit(row, column), (table, row["Ad"], column))
                    variants += 1
        self.assertEqual(60, variants)
        self.assertEqual(39600, affected_rows)

    def test_ratio_table_preserves_all_eight_non_percent_unit_definitions(self):
        data = pd.read_parquet(RAW / "table_15.parquet").drop_duplicates("BasitSira")
        observed = {}
        for _, row in data.iterrows():
            row["value_dimension"] = "Rasyo"
            result = metric_semantics(row, POLICY["tables"]["15"])
            sequence = int(row["BasitSira"])
            observed[sequence] = result
            self.assertNotEqual("percent_or_ratio_source_defined", result["unit"])
        for sequence in range(18, 23):
            self.assertEqual("bin TL", observed[sequence]["unit"])
            self.assertEqual("monetary_per_entity", observed[sequence]["measure_kind"])
            self.assertEqual("person" if sequence <= 20 else "branch", observed[sequence]["denominator_dimension"])
        self.assertEqual("person", observed[23]["unit"])
        self.assertEqual("branch", observed[23]["denominator_dimension"])
        for sequence in (24, 25):
            self.assertEqual("day", observed[sequence]["unit"])
            self.assertEqual("duration", observed[sequence]["measure_kind"])

    def test_customer_count_keeps_raw_unit_and_deduplication_warning(self):
        data = to_long(RAW / "table_06.parquet", POLICY["tables"]["6"])
        count = data.loc[data["source_sequence"].eq("5") & data["value_dimension"].eq("NetMusteri")]
        self.assertTrue(count["source_unit"].eq("milyon TL").all())
        self.assertTrue(count["unit"].eq("count").all())
        self.assertTrue(count["analysis_semantics"].eq("count").all())
        self.assertTrue(count["deduplication_status"].eq("not_deduplicated_across_banks").all())
        source = pd.read_parquet(RAW / "table_06.parquet")
        expected = source.loc[source["BasitSira"].eq(5), "NetMusteri"].tolist()
        self.assertEqual(expected, count["source_value"].tolist())
        placeholders = data.loc[data["source_sequence"].isin(["1", "2", "3", "4"]) & data["value_dimension"].eq("NetMusteri")]
        self.assertTrue(placeholders["semantic_confidence"].eq("not_applicable").all())

    def test_rasyo_column_does_not_imply_percent(self):
        row = pd.Series({"table_no": 17, "Ad": "Yurt dışı payı", "source_unit": "milyon TL"})
        self.assertEqual("percent_or_ratio_source_defined", source_unit(row, "Rasyo"))

    @staticmethod
    def cumulative(months, values, versions=None):
        frame = pd.DataFrame({
            "month": months, "source_value": values, "table_no": 2,
            "metric_code": "table02:53:definition:Toplam", "group_code": 10001,
            "calendar_year": [int(month[:4]) for month in months],
            "transformation": "difference_within_calendar_year",
        })
        if versions is not None:
            frame["source_definition_version"] = versions
        return apply_transformations(frame)

    def test_missing_february_is_not_reported_as_march_flow(self):
        data = self.cumulative(["2025-01", "2025-03", "2025-04"], [10, 50, 70])
        self.assertEqual(10, data.loc[0, "analysis_value"])
        self.assertTrue(pd.isna(data.loc[1, "analysis_value"]))
        self.assertEqual("unavailable_previous_calendar_month", data.loc[1, "transformation_status"])
        self.assertEqual(20, data.loc[2, "analysis_value"])
        validation = validate_recomposition(data)
        self.assertEqual(1, validation["recomposition_comparisons"])
        self.assertEqual(2, validation["recomposition_unavailable_rows"])
        self.assertEqual(1, validation["incomplete_cumulative_chains"])

    def test_missing_january_never_creates_an_annual_recomposition_anchor(self):
        data = self.cumulative(["2025-02", "2025-03"], [30, 50])
        self.assertTrue(pd.isna(data.loc[0, "analysis_value"]))
        self.assertEqual(20, data.loc[1, "analysis_value"])
        self.assertEqual(0, validate_recomposition(data)["recomposition_comparisons"])

    def test_null_previous_value_and_definition_change_block_difference(self):
        data = self.cumulative(["2025-01", "2025-02", "2025-03", "2025-04"], [10, None, 50, 70])
        self.assertEqual("unavailable_previous_source_value", data.loc[2, "transformation_status"])
        self.assertTrue(pd.isna(data.loc[2, "analysis_value"]))
        self.assertEqual(20, data.loc[3, "analysis_value"])
        changed = self.cumulative(["2025-01", "2025-02", "2025-03"], [10, 30, 50], ["v1", "v1", "v2"])
        self.assertTrue(pd.isna(changed.loc[2, "analysis_value"]))

    def test_full_year_reset_and_recomposition_detect_wrong_calculation(self):
        data = self.cumulative(["2024-12", "2025-01", "2025-02"], [1000, 10, 30])
        self.assertEqual(10, data.loc[1, "analysis_value"])
        self.assertEqual(20, data.loc[2, "analysis_value"])
        self.assertEqual(2, validate_recomposition(data)["recomposition_comparisons"])
        data.loc[2, "analysis_value"] = 21
        with self.assertRaisesRegex(ValueError, "yeniden bileşim"):
            validate_recomposition(data)


class BddkMonthlySemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = json.loads(
            (DATA / "validation.json").read_text(encoding="utf-8")
        )
        cls.measurements = pd.read_parquet(DATA / "measurements_long.parquet")
        cls.dictionary = pd.read_parquet(DATA / "metric_dictionary.parquet")

    def test_all_tables_and_metrics_are_present(self):
        self.assertEqual("passed", self.validation["status"])
        self.assertEqual(17, self.validation["table_count"])
        self.assertEqual(66, self.validation["period_count"])
        self.assertEqual(10, self.measurements["group_code"].nunique())
        self.assertEqual(len(self.dictionary), self.validation["metric_count"])
        self.assertGreater(self.validation["metric_count"], 2000)
        self.assertFalse(
            self.measurements.duplicated(
                ["month", "table_no", "group_code", "metric_code"]
            ).any()
        )

    def test_income_statement_is_differenced_only_within_year(self):
        metric = self.measurements.loc[
            (self.measurements["table_no"] == 2)
            & (self.measurements["group_code"] == 10001)
            & (self.measurements["source_sequence"] == "53")
            & (self.measurements["value_dimension"] == "Toplam")
        ].sort_values("month")
        january_2022 = metric.loc[metric["month"].eq("2022-01")].iloc[0]
        december_2021 = metric.loc[metric["month"].eq("2021-12")].iloc[0]
        self.assertEqual(january_2022["source_value"], january_2022["analysis_value"])
        self.assertNotEqual(
            january_2022["analysis_value"],
            january_2022["source_value"] - december_2021["source_value"],
        )
        february_2022 = metric.loc[metric["month"].eq("2022-02")].iloc[0]
        self.assertEqual(
            february_2022["source_value"] - january_2022["source_value"],
            february_2022["analysis_value"],
        )

    def test_monthly_flows_recompose_to_source_ytd_values(self):
        self.assertEqual(
            0.0,
            self.validation["recomposition"]["maximum_recomposition_difference"],
        )
        cumulative = self.measurements.loc[self.measurements["table_no"].eq(2)].copy()
        recomposed = cumulative.groupby(
            ["metric_code", "group_code", "calendar_year"], sort=False
        )["analysis_value"].cumsum()
        comparable = cumulative["source_value"].notna() & recomposed.notna()
        self.assertTrue(
            cumulative.loc[comparable, "source_value"].equals(recomposed.loc[comparable])
        )

    def test_housing_credit_remains_a_stock(self):
        housing = self.dictionary.loc[
            (self.dictionary["table_no"] == 4)
            & self.dictionary["metric_label"].str.contains("Konut", case=False, na=False)
        ]
        self.assertFalse(housing.empty)
        self.assertTrue(housing["analysis_semantics"].eq("period_end_stock").all())
        self.assertTrue(housing["transformation"].eq("identity").all())
        self.assertTrue(housing["quarterly_aggregation"].eq("last").all())

    def test_missing_values_are_not_filled(self):
        self.assertEqual(
            self.validation["source_missing_value_count"],
            int(self.measurements["source_value"].isna().sum()),
        )
        identity = self.measurements["transformation"].eq("identity")
        self.assertTrue(
            self.measurements.loc[identity, "source_value"].isna().equals(
                self.measurements.loc[identity, "analysis_value"].isna()
            )
        )


if __name__ == "__main__":
    unittest.main()
