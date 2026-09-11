from __future__ import annotations

from collections import Counter
from typing import Any


FUNDAMENTALS_EXPECTED_GROUPS = (
    "confirmed_probable_pitchers",
    "starter_quality",
    "offense_quality",
    "starter_handedness_splits",
    "bullpen_availability",
    "confirmed_lineups",
    "weather_roof",
    "ballpark_factors",
    "injuries_late_scratches",
    "travel_rest",
)


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


def fundamentals_capture_gap(report: dict[str, Any]) -> dict[str, Any] | None:
    """Return a fail-closed live fundamentals capture gap from immutable proof rows.

    Chronology/persistence safety is a prerequisite. If any game is contract-blocked,
    if the report is authority-bearing or mutable, or if counts are malformed, this
    helper refuses to turn source completeness into task authority. Missing groups are
    recorded as absent from the diagnostic proof rather than guessed from postgame data.
    """

    if not isinstance(report, dict):
        return None
    if report.get("reportType") != "MLB_FUNDAMENTALS_PROVENANCE_READ_ONLY_DIAGNOSTIC":
        return None
    if report.get("readOnly") is not True:
        return None
    for field in (
        "mutatedPersistence",
        "productionAuthorityChanged",
        "modelPromotionAllowed",
        "automaticWagerAllowed",
        "immutablePredictionRewriteAllowed",
    ):
        if report.get(field) is not False:
            return None

    game_count = _nonnegative_int(report.get("gameCount"))
    safe_count = _nonnegative_int(report.get("contractSafeGameCount"))
    blocked_count = _nonnegative_int(report.get("contractBlockedGameCount"))
    games = report.get("games")
    if (
        game_count is None
        or safe_count is None
        or blocked_count is None
        or game_count <= 0
        or safe_count + blocked_count != game_count
        or blocked_count != 0
        or safe_count != game_count
        or not isinstance(games, list)
        or len(games) != game_count
    ):
        return None

    group_status_counts = {group: Counter() for group in FUNDAMENTALS_EXPECTED_GROUPS}
    incomplete_games = 0
    for game in games:
        if not isinstance(game, dict) or game.get("contractSafe") is not True:
            return None
        groups = game.get("groups")
        if not isinstance(groups, list):
            return None
        statuses: dict[str, str] = {}
        for item in groups:
            if not isinstance(item, dict):
                return None
            group = item.get("group")
            if group in (None, ""):
                return None
            name = str(group)
            if name in statuses:
                return None
            statuses[name] = str(item.get("status") or "UNKNOWN")

        incomplete = False
        for group in FUNDAMENTALS_EXPECTED_GROUPS:
            status = statuses.get(group, "MISSING_FROM_DIAGNOSTIC_PROOF")
            group_status_counts[group][status] += 1
            if status != "CONNECTED":
                incomplete = True
        if incomplete:
            incomplete_games += 1

    if incomplete_games <= 0:
        return None

    return {
        "gameCount": game_count,
        "incompleteGameCount": incomplete_games,
        "contractSafeGameCount": safe_count,
        "contractBlockedGameCount": blocked_count,
        "automaticWagerAllowed": False,
        "groupStatusCounts": {
            group: dict(sorted(counts.items()))
            for group, counts in group_status_counts.items()
        },
    }
