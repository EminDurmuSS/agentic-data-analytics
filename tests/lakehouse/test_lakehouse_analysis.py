import unittest

import numpy as np
import pandas as pd

from agentic_analytics.lakehouse.analysis import residualize_fixed_effects


class FixedEffectProjectionTests(unittest.TestCase):
    def setUp(self):
        # Different observation windows expose the failure of one-pass double
        # demeaning; a balanced entity-period grid would hide the regression.
        pairs = [(entity, period) for entity in range(5) for period in range(6)
                 if not (entity == 0 and period < 3)
                 and not (entity == 1 and period > 3)
                 and not (entity == 3 and period == 2)]
        rng = np.random.default_rng(42)
        self.frame = pd.DataFrame(pairs, columns=["entity", "period"])
        self.frame[["x1", "x2"]] = rng.normal(size=(len(pairs), 2))
        self.beta = np.array([2.5, -0.7])
        self.frame["y"] = (
            self.frame[["x1", "x2"]].to_numpy() @ self.beta
            + self.frame["entity"].map({0: 10, 1: -7, 2: 4, 3: 13, 4: -3})
            + self.frame["period"].map({0: -5, 1: 2, 2: 7, 3: -4, 4: 9, 5: 3})
        )

    def test_recovers_known_coefficients_in_unbalanced_panel(self):
        residuals = residualize_fixed_effects(
            self.frame, ["y", "x1", "x2"], ["entity", "period"]
        )
        beta = np.linalg.lstsq(
            residuals[["x1", "x2"]], residuals["y"], rcond=None
        )[0]
        np.testing.assert_allclose(beta, self.beta, atol=1e-12)

        columns = ["y", "x1", "x2"]
        one_pass = (
            self.frame[columns]
            - self.frame.groupby("entity")[columns].transform("mean")
            - self.frame.groupby("period")[columns].transform("mean")
            + self.frame[columns].mean()
        )
        wrong_beta = np.linalg.lstsq(
            one_pass[["x1", "x2"]], one_pass["y"], rcond=None
        )[0]
        self.assertGreater(np.max(np.abs(wrong_beta - self.beta)), 0.01)

    def test_projection_removes_both_effects_and_preserves_row_alignment(self):
        shuffled = self.frame.sample(frac=1, random_state=7)
        residuals = residualize_fixed_effects(
            shuffled, ["y", "x1", "x2"], ["entity", "period"]
        )
        self.assertTrue(residuals.index.equals(shuffled.index))
        for effect in ["entity", "period"]:
            sums = residuals.groupby(shuffled[effect]).sum()
            np.testing.assert_allclose(sums, 0, atol=1e-11)
        ordered = residualize_fixed_effects(
            self.frame, ["y", "x1", "x2"], ["entity", "period"]
        )
        np.testing.assert_allclose(residuals.sort_index(), ordered, atol=1e-11)

    def test_requires_explicit_complete_case_selection(self):
        for column, value in [("x1", np.nan), ("x1", np.inf), ("entity", np.nan)]:
            with self.subTest(column=column, value=value):
                incomplete = self.frame.copy()
                incomplete.loc[0, column] = value
                with self.assertRaises(ValueError):
                    residualize_fixed_effects(
                        incomplete, ["y", "x1", "x2"], ["entity", "period"]
                    )


if __name__ == "__main__":
    unittest.main()
