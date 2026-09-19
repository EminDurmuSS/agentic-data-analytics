"""Bounded descriptive statistics over immutable, workspace-owned analyses."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import numpy as np
import pandas as pd

from agentic_analytics.lakehouse.store import StoreError


class StatisticsError(ValueError):
    def __init__(self, message, code="INVALID_STATISTICS_REQUEST"):
        super().__init__(message)
        self.code = code


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise StatisticsError(f"{name} must be an integer between {low} and {high}.")
    return value


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not low <= value <= high:
        raise StatisticsError(f"{name} must be finite and between {low} and {high}.")
    return float(value)


class StatisticsTools:
    """No model-controlled paths; methods return JSON plus a content-addressed artifact."""

    def __init__(self, store, workspace_id, *, max_rows=10000):
        self.store, self.workspace_id = store, workspace_id
        self.store.workspace(workspace_id)
        self.max_rows = max_rows
        self.root = store.root / "statistics" / workspace_id
        self.root.mkdir(parents=True, exist_ok=True)

    def _load(self, analysis_id, columns, time_column):
        frame, manifest = self.store.load_analysis(analysis_id)
        if manifest["workspace_id"] != self.workspace_id:
            raise StatisticsError("Analysis belongs to another workspace.", "WORKSPACE_MISMATCH")
        if not 1 <= len(frame) <= self.max_rows:
            raise StatisticsError("Analysis exceeds the statistics row budget.", "ROW_LIMIT")
        if time_column not in frame or any(column not in frame or column == time_column for column in columns):
            raise StatisticsError("Select existing numeric columns and a distinct time column.")
        if frame[time_column].isna().any() or frame[time_column].duplicated().any():
            raise StatisticsError("A unique nonmissing time axis is required.", "AMBIGUOUS_GRAIN")
        frame = frame.sort_values(time_column, kind="stable").reset_index(drop=True)
        labels = frame[time_column].astype(str)
        frequency = manifest.get("plan", {}).get("frequency") or manifest.get("lineage", {}).get("frequency")
        aliases = {"monthly": "M", "quarterly": "Q", "annual": "Y", "yearly": "Y",
                   "daily": "D", "weekly_observed": "D", "weekly": "W-FRI", "weekly_friday": "W-FRI",
                   "weekly_wednesday": "W-WED", "business_daily": "B"}
        # Infer only for legacy analyses without metadata. An explicit event or
        # twice-monthly calendar must never become daily just because it uses dates.
        if frequency is None:
            if labels.str.fullmatch(r"\d{4}-\d{2}").all():
                frequency = "monthly"
            elif labels.str.fullmatch(r"\d{4}-Q[1-4]").all():
                frequency = "quarterly"
            elif labels.str.fullmatch(r"\d{4}").all():
                frequency = "annual"
            elif labels.str.fullmatch(r"\d{4}-\d{2}-\d{2}").all():
                frequency = "daily"
        if frequency not in aliases and frequency != "half_yearly":
            raise StatisticsError("A supported regular calendar frequency is required; event and twice-monthly publication calendars are not assumed regular.", "UNKNOWN_FREQUENCY")
        pattern = (r"\d{4}-H[12]" if frequency == "half_yearly" else r"\d{4}-\d{2}" if frequency == "monthly"
                   else r"\d{4}-Q[1-4]" if frequency == "quarterly" else r"\d{4}" if frequency in {"annual", "yearly"}
                   else r"\d{4}-\d{2}-\d{2}")
        if not labels.str.fullmatch(pattern).all():
            raise StatisticsError("Time labels do not match the saved frequency.", "INVALID_TIME_LABEL")
        try:
            if frequency == "half_yearly":
                # Calendar semesters have ordinal spacing independent of their
                # unequal number of days. Do not infer a six-month date offset.
                ordinals = labels.str[:4].astype(int).to_numpy() * 2 + labels.str[-1].astype(int).to_numpy() - 1
            else:
                periods = pd.PeriodIndex(labels, freq=aliases[frequency])
                ordinals = periods.asi8
                if frequency in {"weekly", "weekly_friday", "weekly_wednesday"} and list(periods.end_time.strftime("%Y-%m-%d")) != labels.tolist():
                    raise StatisticsError("Weekly labels must be native week-ending dates.", "INVALID_TIME_LABEL")
                if frequency == "business_daily" and (pd.DatetimeIndex(labels).dayofweek > 4).any():
                    raise StatisticsError("Business-day labels cannot include weekends.", "INVALID_TIME_LABEL")
        except (ValueError, TypeError) as exc:
            if isinstance(exc, StatisticsError):
                raise
            raise StatisticsError("Time labels do not match the saved frequency.") from exc
        if frequency == "weekly_observed" and len(ordinals) > 1:
            gaps = np.diff(ordinals)
            if not ((gaps >= 4) & (gaps <= 10)).all():
                raise StatisticsError("Observed weekly dates must be strictly ordered and 4 to 10 days apart.", "IRREGULAR_TIME_AXIS")
        elif len(ordinals) > 1 and not (np.diff(ordinals) == 1).all():
            raise StatisticsError("Missing calendar periods must be represented explicitly as null rows.", "IRREGULAR_TIME_AXIS")
        for column in columns:
            semantics = manifest.get("schema", {}).get(column, {})
            if semantics.get("status") == "review_required" or semantics.get("kind") == "unknown" or semantics.get("cumulative_evidence"):
                raise StatisticsError("Statistics require reviewed metric semantics; unconverted cumulative source values remain available for raw inspection.", "SEMANTICS_REVIEW_REQUIRED")
            if not pd.api.types.is_numeric_dtype(frame[column]) or pd.api.types.is_bool_dtype(frame[column]):
                raise StatisticsError("Statistics require stored numeric columns.", "NON_NUMERIC_COLUMN")
            values = frame[column].to_numpy(dtype=float, na_value=np.nan)
            if np.isinf(values).any():
                raise StatisticsError("Infinite numeric values are not supported.")
            frame[column] = values
        return frame, manifest, frequency

    def _persist(self, manifest, method, parameters, results, warnings):
        payload = {"status": "ok", "method": method, "parameters": parameters,
                   "analysis_id": manifest["analysis_id"], "workspace_id": self.workspace_id,
                   "provenance": {"snapshot_id": manifest["snapshot_id"],
                                  "data_sha256": manifest["data_sha256"],
                                  "schema": manifest.get("schema", {}),
                                  "lineage_ref": manifest["analysis_id"]},
                   "results": results, "warnings": warnings, "causal_claim": False}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        artifact_id = "statistic_" + hashlib.sha256(encoded).hexdigest()
        target = self.root / (artifact_id + ".json")
        if not target.exists():
            fd, name = tempfile.mkstemp(dir=self.root, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)
        preview = dict(results)
        truncated = False
        if isinstance(preview.get("rows"), list) and len(preview["rows"]) > 120:
            rows = preview["rows"]
            preview.update(rows=rows[:10] + rows[-10:], row_count=len(rows),
                           flagged_rows=[row for row in rows if row.get("anomaly") is True][:40])
            truncated = True
        if isinstance(preview.get("changes"), list) and len(preview["changes"]) > 100:
            preview.update(change_count=len(preview["changes"]), changes=preview["changes"][:100])
            truncated = True
        if isinstance(preview.get("excluded_missing_periods"), list) and len(preview["excluded_missing_periods"]) > 120:
            periods = preview["excluded_missing_periods"]
            preview.update(excluded_missing_periods=periods[:10] + periods[-10:],
                           excluded_missing_period_count=len(periods))
            truncated = True
        return {**payload, "results": preview, "preview_truncated": truncated,
                "artifact_id": artifact_id, "artifact_ref": artifact_id}

    def load_artifact(self, artifact_id):
        if not isinstance(artifact_id, str) or not re.fullmatch(r"statistic_[a-f0-9]{64}", artifact_id):
            raise StatisticsError("Invalid statistics artifact identifier.")
        encoded = (self.root / (artifact_id + ".json")).read_bytes()
        if hashlib.sha256(encoded).hexdigest() != artifact_id.removeprefix("statistic_"):
            raise StatisticsError("Statistics artifact hash mismatch.")
        return json.loads(encoded)

    def rolling_anomalies(self, analysis_id, column, *, time_column="period", window=12,
                          min_history=6, threshold=3.5, max_missing_fraction=0.2):
        window = _integer(window, 3, 240, "window")
        min_history = _integer(min_history, 3, window, "min_history")
        threshold = _number(threshold, 0.1, 100, "threshold")
        missing = _number(max_missing_fraction, 0, 0.5, "max_missing_fraction")
        frame, manifest, frequency = self._load(analysis_id, [column], time_column)
        values = frame[column].to_numpy()
        rows = []
        for index, value in enumerate(values):
            history = values[max(0, index - window):index]
            observed = history[np.isfinite(history)]
            row = {"period": str(frame.iloc[index][time_column]), "value": float(value) if np.isfinite(value) else None,
                   "history_count": len(observed), "score": None, "anomaly": None}
            if not np.isfinite(value):
                row["reason"] = "missing_value"
            elif len(observed) < min_history or len(observed) < len(history) * (1 - missing):
                row["reason"] = "insufficient_past_observations"
            else:
                median = float(np.median(observed))
                mad = float(np.median(np.abs(observed - median)))
                row.update(baseline_median=median, baseline_mad=mad)
                if mad == 0:
                    row.update(anomaly=bool(value != median), reason="zero_mad_baseline" if value == median else "deviation_from_zero_mad_baseline")
                else:
                    score = float(0.6744897501960817 * (value - median) / mad)
                    row.update(score=score, anomaly=bool(abs(score) >= threshold), reason="scored")
            rows.append(row)
        return self._persist(manifest, "past_only_rolling_mad", {
            "column": column, "time_column": time_column, "frequency": frequency, "window": window,
            "min_history": min_history, "threshold": threshold, "max_missing_fraction": missing},
            {"rows": rows, "anomaly_count": sum(row["anomaly"] is True for row in rows)},
            ["Descriptive point anomalies; zero-MAD deviations are flagged without a finite standardized score."])

    def detect_changes(self, analysis_id, column, *, time_column="period", window=6, threshold=3.5):
        window = _integer(window, 3, 120, "window")
        threshold = _number(threshold, 0.1, 100, "threshold")
        frame, manifest, frequency = self._load(analysis_id, [column], time_column)
        values = frame[column].to_numpy()
        if len(values) < window * 2:
            raise StatisticsError("At least two complete windows are required.", "INSUFFICIENT_SAMPLE")
        if not np.isfinite(values).all():
            raise StatisticsError("Change detection requires complete observations; no filling is applied.", "MISSING_OBSERVATIONS")
        candidates = []
        for index in range(window, len(values) - window + 1):
            left, right = values[index - window:index], values[index:index + window]
            before, after = float(np.median(left)), float(np.median(right))
            scale = 1.482602218505602 * float(np.median(np.r_[np.abs(left - before), np.abs(right - after)]))
            shift = after - before
            score = abs(shift) / scale if scale > 0 else None
            if shift and (score is None or score >= threshold):
                candidates.append({"period": str(frame.iloc[index][time_column]), "index": index,
                                   "median_before": before, "median_after": after, "shift": shift,
                                   "score": score, "evidence": "zero_within_window_mad" if score is None else "standardized_median_shift"})
        selected = []
        for candidate in sorted(candidates, key=lambda row: (-(row["score"] if row["score"] is not None else 1e300), -abs(row["shift"]), row["index"])):
            if all(abs(candidate["index"] - item["index"]) >= window for item in selected):
                selected.append(candidate)
        selected.sort(key=lambda row: row["index"])
        return self._persist(manifest, "adjacent_window_median_shift", {
            "column": column, "time_column": time_column, "frequency": frequency, "window": window, "threshold": threshold},
            {"changes": selected, "sample_size": len(values)},
            ["Retrospective descriptive window scan; requires a full following window and does not provide change-point significance or causal attribution."])

    def analyze_relationship(self, analysis_id, x, y, *, time_column="period", lag=0,
                             method="pearson", transform="none", min_samples=12, max_missing_fraction=0.2):
        lag = _integer(lag, 0, 24, "lag")
        min_samples = _integer(min_samples, 6, 10000, "min_samples")
        missing = _number(max_missing_fraction, 0, 0.5, "max_missing_fraction")
        if x == y or method not in {"pearson", "spearman", "granger"} or transform not in {"none", "difference"}:
            raise StatisticsError("Choose distinct series, pearson/spearman/granger, and none/difference transformation.")
        frame, manifest, frequency = self._load(analysis_id, [x, y], time_column)
        series = frame[[time_column, x, y]].copy()
        source_start, source_end = str(series.iloc[0][time_column]), str(series.iloc[-1][time_column])
        transform_dropped_periods = []
        if transform == "difference":
            transform_dropped_periods = [str(series.iloc[0][time_column])]
            series.loc[:, [x, y]] = series[[x, y]].diff()
            series = series.iloc[1:].copy()
        params = {"x": x, "y": y, "time_column": time_column, "frequency": frequency, "lag": lag,
                  "method": method, "transform": transform, "min_samples": min_samples, "max_missing_fraction": missing}
        if method in {"pearson", "spearman"}:
            from scipy.stats import pearsonr, spearmanr
            lag_dropped_periods = series[time_column].iloc[:lag].astype(str).tolist() if lag else []
            paired = pd.DataFrame({"period": series[time_column].astype(str),
                                   "x": series[x].shift(lag), "y": series[y]}).iloc[lag:].copy()
            missing_mask = paired[["x", "y"]].isna().any(axis=1)
            excluded_missing_periods = paired.loc[missing_mask, "period"].tolist()
            complete = paired.dropna()
            if len(complete) < min_samples or len(complete) < len(paired) * (1 - missing):
                raise StatisticsError("Too few complete aligned pairs or excessive missingness.", "INSUFFICIENT_SAMPLE")
            if (complete.nunique() < 2).any():
                raise StatisticsError("Correlation requires variation in both series.", "CONSTANT_SERIES")
            correlation = pearsonr if method == "pearson" else spearmanr
            coefficient, pvalue = correlation(complete["x"], complete["y"])
            result = {"correlation": float(coefficient), "p_value_iid_assumption": float(pvalue),
                      "sample_size": len(complete), "excluded_missing_pairs": len(paired) - len(complete),
                      "candidate_pair_count": len(paired),
                      "sample_start_period": str(complete.iloc[0]["period"]),
                      "sample_end_period": str(complete.iloc[-1]["period"]),
                      "source_start_period": source_start, "source_end_period": source_end,
                      "excluded_missing_periods": excluded_missing_periods,
                      "transform_dropped_periods": transform_dropped_periods,
                      "lag_dropped_periods": lag_dropped_periods,
                      "sample_period_basis": f"{y}(t)",
                      "lag_interpretation": f"{x}(t-{lag}) paired with {y}(t)"}
            warnings = [
                "Association is not causation. The p-value assumes independent observations; autocorrelated time series may violate that assumption.",
                "Only complete aligned pairs are used; missing pairs are not filled or treated as zero.",
            ]
        else:
            from statsmodels.tools.sm_exceptions import InfeasibleTestError
            from statsmodels.tsa.stattools import adfuller, grangercausalitytests
            if lag < 1:
                raise StatisticsError("Granger lag must be at least one.")
            if series.isna().any().any():
                raise StatisticsError("Granger requires a contiguous complete sample; rows are never silently dropped.", "MISSING_OBSERVATIONS")
            if len(series) < max(min_samples, 30, 5 * lag + 5):
                raise StatisticsError("Insufficient observations for the requested Granger lag.", "INSUFFICIENT_SAMPLE")
            if (series.nunique() < 3).any():
                raise StatisticsError("Granger requires nonconstant varying series.", "CONSTANT_SERIES")
            stationarity = {column: float(adfuller(series[column], autolag="AIC", result_object=False)[1]) for column in (x, y)}
            if any(value >= 0.05 for value in stationarity.values()):
                raise StatisticsError("ADF did not support stationarity for both inputs; consider a justified difference transformation.", "STATIONARITY_GATE")
            try:
                fit = grangercausalitytests(series[[y, x]], maxlag=[lag])[lag][0]["ssr_ftest"]
            except (InfeasibleTestError, np.linalg.LinAlgError) as exc:
                raise StatisticsError("The selected series produce a singular or infeasible Granger model.", "INFEASIBLE_STATISTICAL_MODEL") from exc
            result = {"f_statistic": float(fit[0]), "p_value": float(fit[1]), "df_denominator": float(fit[2]),
                      "df_numerator": float(fit[3]), "sample_size": len(series), "adf_p_values": stationarity,
                      "sample_start_period": str(series.iloc[0][time_column]),
                      "sample_end_period": str(series.iloc[-1][time_column]),
                      "source_start_period": source_start, "source_end_period": source_end,
                      "transform_dropped_periods": transform_dropped_periods,
                      "test": "ssr_ftest",
                      "null_hypothesis": f"past {x} values through lag {lag} do not add predictive information for {y}",
                      "direction": f"past {x} adds predictive information for {y}"}
            warnings = ["Granger tests conditional predictive association, not a causal effect. ADF screening does not establish model adequacy or remove omitted-variable bias."]
        return self._persist(manifest, "lagged_" + method, params, result, warnings)

    def extra_tools(self):
        common = {"analysis_id": {"type": "string"}, "time_column": {"type": "string"}}
        definitions = {
            "rolling_anomalies": (self.rolling_anomalies, "Find point anomalies using only earlier observations in a saved analysis.",
                {**common, "column": {"type": "string"}, "window": {"type": "integer", "minimum": 3, "maximum": 240},
                 "min_history": {"type": "integer", "minimum": 3}, "threshold": {"type": "number", "minimum": 0.1}}, ["analysis_id", "column"]),
            "detect_changes": (self.detect_changes, "Describe persistent median shifts with complete adjacent windows; no causal claim.",
                {**common, "column": {"type": "string"}, "window": {"type": "integer", "minimum": 3, "maximum": 120},
                 "threshold": {"type": "number", "minimum": 0.1}}, ["analysis_id", "column"]),
            "analyze_relationship": (self.analyze_relationship, "Measure lagged Pearson/Spearman association or gated Granger predictive association in a saved analysis.",
                {**common, "x": {"type": "string"}, "y": {"type": "string"}, "lag": {"type": "integer", "minimum": 0, "maximum": 24},
                 "method": {"type": "string", "enum": ["pearson", "spearman", "granger"]}, "transform": {"type": "string", "enum": ["none", "difference"]},
                 "min_samples": {"type": "integer", "minimum": 6}, "max_missing_fraction": {"type": "number", "minimum": 0, "maximum": 0.5}}, ["analysis_id", "x", "y"]),
        }
        registry = {}
        for name, (function, description, properties, required) in definitions.items():
            def handler(arguments, function=function):
                try:
                    return function(**arguments)
                except (StatisticsError, StoreError, ValueError, TypeError, KeyError, OSError) as exc:
                    return {"status": "blocked", "code": getattr(exc, "code", "STATISTICS_ERROR"), "message": str(exc)}
                except ImportError:
                    return {"status": "blocked", "code": "DEPENDENCY_UNAVAILABLE", "message": "Required statistics dependency is unavailable."}
            registry[name] = {"schema": {"type": "function", "function": {"name": name, "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}},
                "handler": handler, "mutating": False}
        return registry
