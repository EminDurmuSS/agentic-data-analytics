"""Small numerical helpers for exploratory lakehouse notebooks."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def residualize_fixed_effects(
    frame: pd.DataFrame,
    value_columns: Sequence[str],
    effect_columns: Sequence[str],
) -> pd.DataFrame:
    """Project numeric columns off categorical effects, including unbalanced panels.

    Least squares on the full indicator matrix handles overlapping effects
    without assuming every entity has observations in every period. Redundant
    indicator columns are intentional: their column space includes an intercept,
    and NumPy's least-squares solution gives the unique fitted projection.
    """
    if frame.empty or not value_columns or not effect_columns:
        raise ValueError("Nonempty observations, values and fixed effects are required.")
    if frame[list(effect_columns)].isna().any().any():
        raise ValueError("Fixed-effect labels must not be missing.")
    values = frame[list(value_columns)].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Values must be finite; select the complete-case sample first.")

    indicators = pd.get_dummies(
        frame[list(effect_columns)].astype("category"), dtype=float
    ).to_numpy()
    fitted = indicators @ np.linalg.lstsq(indicators, values, rcond=None)[0]
    return pd.DataFrame(
        values - fitted, index=frame.index, columns=list(value_columns)
    )
