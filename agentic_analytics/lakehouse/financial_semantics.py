"""Source-declared financial semantics shared by imported-data consumers."""

import re
import unicodedata


def cumulative_evidence(spec):
    """Return explicit cumulative declarations; never infer semantics from values.

Call this on imported column definitions. Curated sources may already have an
independently verified deaccumulation pipeline and must keep that authority.
"""
    evidence = {}
    for key in ("temporal_semantics", "source_semantics", "analysis_semantics", "temporal_basis", "flow_basis", "cumulative", "is_cumulative"):
        value = spec.get(key)
        if key in {"cumulative", "is_cumulative"} and value is True:
            evidence[key] = value
        elif isinstance(value, str):
            text = "".join(char for char in unicodedata.normalize("NFKD", value.casefold().replace("ı", "i"))
                           if not unicodedata.combining(char))
            text = re.sub(r"[_\s-]+", " ", text)
            if re.search(r"\bytd\b|\byear to date\b|(?<!non )(?<!not )\bcumulative\b|\b(?:kumulatif|birikimli)\b", text):
                evidence[key] = value
    return evidence
