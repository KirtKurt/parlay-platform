from __future__ import annotations

from typing import Any


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    # A fractional JSON number is not an integer count. int() alone truncates it.
    if isinstance(value, float) and value != parsed:
        return None
    return parsed if parsed >= 0 else None


def fundamentals_feature_pipeline_gap(report: dict[str, Any]) -> dict[str, int] | None:
    """Return source-honest fundamentals inactivity evidence when numerically proven.

    This helper is intentionally read-only. A field name alone is not task authority:
    the report must contain a positive official-game count, fundamentals must be
    inactive for every official game, and zero games may have reached shadow
    evaluation. The returned values are evidence only and grant no scoring,
    promotion, or production authority.
    """

    if not isinstance(report, dict):
        return None
    summary = report.get("scoringSummary")
    if not isinstance(summary, dict):
        return None

    official = _nonnegative_int(summary.get("officialGameCount"))
    inactive = _nonnegative_int(summary.get("fundamentalsNotActiveCount"))
    shadow_evaluated = _nonnegative_int(summary.get("fundamentalsShadowEvaluatedCount"))
    if official is None or inactive is None or shadow_evaluated is None:
        return None
    if official <= 0 or inactive != official or shadow_evaluated != 0:
        return None

    return {
        "officialGameCount": official,
        "fundamentalsNotActiveCount": inactive,
        "fundamentalsShadowEvaluatedCount": shadow_evaluated,
    }
