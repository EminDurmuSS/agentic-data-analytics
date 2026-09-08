import json
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data_pipeline" / "bddk" / "processed" / "monthly_semantic"


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
