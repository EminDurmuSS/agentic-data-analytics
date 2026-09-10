"""Operator diagnostics containing identifiers and code locations, never exception text."""
from __future__ import annotations

from collections import deque
import logging
import re

from agentic_analytics.lakehouse.service import PlanError
from agentic_analytics.lakehouse.store import StoreError
from agentic_analytics.providers.mia import MiaError


LOGGER = logging.getLogger(__name__)
# Exception codes are metadata only when both their producer and value are known.
# Unknown codes fall back to their error family instead of echoing arbitrary text.
PLAN_CODES = frozenset({
    "INVALID_PLAN", "INVALID_CHART_REQUEST", "UNIT_MISMATCH", "SCOPE_MISMATCH",
    "WORKSPACE_MISMATCH", "METRIC_NOT_FOUND", "METADATA_ONLY", "NO_NUMERIC_VALUES",
    "NO_PHYSICAL_BINDING", "MISSING_OBSERVATIONS", "INVALID_DIMENSION_VALUE",
    "DIMENSION_NOT_FOUND", "AMBIGUOUS_GRAIN", "SEMANTICS_REVIEW_REQUIRED",
    "INVALID_TEMPORAL_AGGREGATION", "REDUNDANT_ALIGNMENT", "ROW_LIMIT",
    "CONTEXT_BUDGET_EXCEEDED", "UNKNOWN_MUTATION_OUTCOME", "ARTIFACT_HASH_MISMATCH",
    "NUMERIC_PRECISION_UNSUPPORTED", "UNSAFE_INTEGER", "NON_FINITE_VALUE",
})
PROVIDER_CODES = frozenset({
    "MISSING_API_KEY", "REQUEST_TOO_LARGE", "RESPONSE_TOO_LARGE",
    "PROVIDER_AUTH_ERROR", "PROVIDER_RATE_LIMIT", "PROVIDER_HTTP_ERROR",
    "PROVIDER_UNAVAILABLE", "INVALID_PROVIDER_RESPONSE", "INVALID_EMBEDDING_RESPONSE",
})


def _token(value, pattern, fallback):
    return value if type(value) is str and re.fullmatch(pattern, value) else fallback


def _error_code(error):
    if isinstance(error, PlanError):
        allowed, fallback = PLAN_CODES, "PLAN_ERROR"
    elif isinstance(error, MiaError):
        allowed, fallback = PROVIDER_CODES, "PROVIDER_ERROR"
    elif isinstance(error, StoreError):
        return "STORE_CONTRACT_ERROR"
    else:
        return "UNEXPECTED_ERROR"
    code = _token(getattr(error, "code", None), r"[A-Z][A-Z0-9_]{0,63}", fallback)
    return code if code in allowed else fallback


def _frame_locations(error):
    # Walk only this exception's stack. Do not format source lines, locals,
    # __cause__, __context__, or the exception itself.
    frames = deque(maxlen=12)
    traceback = error.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        filename = _token(code.co_filename.replace("\\", "/").rsplit("/", 1)[-1],
                          r"[A-Za-z0-9_.-]{1,100}", "module")
        function = _token(code.co_name, r"[A-Za-z_][A-Za-z0-9_]{0,100}", "function")
        frames.append(f"{filename}:{traceback.tb_lineno}:{function}")
        traceback = traceback.tb_next
    return ";".join(frames) or "none"


def log_job_failure(error, *, job_id, workspace_id):
    """Best-effort safe logging must not prevent the durable failed-job result."""
    try:
        LOGGER.error(
            "Agent job failed: job_id=%s workspace_id=%s error_type=%s error_code=%s frames=%s",
            _token(job_id, r"job_[a-f0-9]{32}", "unknown_job"),
            _token(workspace_id, r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,160}", "unknown_workspace"),
            _token(type(error).__name__, r"[A-Za-z_][A-Za-z0-9_]{0,100}", "Exception"),
            _error_code(error), _frame_locations(error),
            exc_info=False, stack_info=False,
        )
    except Exception:
        # A broken operator log sink must not lose the persisted job failure.
        pass
