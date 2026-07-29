"""Install leakage-safe historical BBD prior-game features into MLB V8."""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, Mapping, Optional

VERSION = "MLB-V8-HISTORICAL-BBS-PRIOR-GAME-FEATURES-v3-separated-snapshot-role"
BBS_PRIOR_SUPPORT_START_DATE = "2026-03-01"
PRIOR_ROLE = "BBD_STRICTLY_PRIOR_COMPLETED_GAME_FEATURES_AT_T_MINUS_45"

FEATURES = (
    "bbs_prior_supported",
    "bbs_prior_win_rate5_diff",
    "bbs_prior_win_rate10_diff",
    "bbs_prior_win_rate30_diff",
    "bbs_prior_run_diff5_diff",
    "bbs_prior_run_diff10_diff",
    "bbs_prior_runs_for10_diff",
    "bbs_prior_runs_against10_diff",
    "bbs_prior_streak_diff",
    "bbs_prior_rest_diff",
    "bbs_prior_venue_win_rate10_diff",
    "bbs_prior_history_games_diff",
    "bbs_prior_history_coverage_min",
    "bbs_prior_available",
)


def _number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Select only a prior-game snapshot; never consume target fundamentals."""
    for value in (
        record.get("historicalBbsPriorGameSnapshot"),
        record.get("historicalBbsFundamentalsSnapshot"),
    ):
        if isinstance(value, Mapping) and value.get("snapshotRole") in (None, PRIOR_ROLE):
            if value.get("priorCompletedGamesUsed") is True:
                return value
    # Backward-compatible read of manifests published before pointer separation.
    legacy = record.get("frozenFundamentalsSnapshot")
    if (
        isinstance(legacy, Mapping)
        and legacy.get("snapshotRole") in (None, PRIOR_ROLE)
        and legacy.get("priorCompletedGamesUsed") is True
    ):
        return legacy
    return {}


def _side(payload: Mapping[str, Any], side: str) -> Mapping[str, Any]:
    value = payload.get(side)
    return value if isinstance(value, Mapping) else {}


def _supported(record: Mapping[str, Any]) -> bool:
    try:
        game_day = date.fromisoformat(str(record.get("slateDateEt") or ""))
        return game_day >= date.fromisoformat(BBS_PRIOR_SUPPORT_START_DATE)
    except Exception:
        return False


def _diff(
    home: Mapping[str, Any],
    away: Mapping[str, Any],
    key: str,
    scale: float = 1.0,
) -> float:
    h = _number(home.get(key))
    a = _number(away.get(key))
    if h is None or a is None:
        return 0.0
    return (h - a) / scale


def feature_map(record: Mapping[str, Any]) -> Dict[str, float]:
    supported = 1.0 if _supported(record) else 0.0
    payload = _payload(record)
    home = _side(payload, "home")
    away = _side(payload, "away")
    home_games = _number(home.get("bbsHistoryGames"))
    away_games = _number(away.get("bbsHistoryGames"))
    available = bool(
        supported > 0.5
        and payload.get("priorCompletedGamesUsed") is True
        and payload.get("sameDayResultsExcluded") is True
        and payload.get("targetGameOutcomeUsed") is False
        and home_games is not None
        and away_games is not None
        and home_games >= 5
        and away_games >= 5
    )
    if not available:
        values = {name: 0.0 for name in FEATURES}
        values["bbs_prior_supported"] = supported
        return values
    return {
        "bbs_prior_supported": supported,
        "bbs_prior_win_rate5_diff": _diff(home, away, "bbsWinRate5"),
        "bbs_prior_win_rate10_diff": _diff(home, away, "bbsWinRate10"),
        "bbs_prior_win_rate30_diff": _diff(home, away, "bbsWinRate30"),
        "bbs_prior_run_diff5_diff": _diff(home, away, "bbsRunDiffPerGame5", 10.0),
        "bbs_prior_run_diff10_diff": _diff(home, away, "bbsRunDiffPerGame10", 10.0),
        "bbs_prior_runs_for10_diff": _diff(home, away, "bbsRunsForPerGame10", 10.0),
        "bbs_prior_runs_against10_diff": _diff(
            home, away, "bbsRunsAgainstPerGame10", 10.0
        ),
        "bbs_prior_streak_diff": _diff(home, away, "bbsStreakNormalized"),
        "bbs_prior_rest_diff": _diff(home, away, "bbsRestDaysNormalized"),
        "bbs_prior_venue_win_rate10_diff": _diff(home, away, "bbsVenueWinRate10"),
        "bbs_prior_history_games_diff": (
            float(home_games) - float(away_games)
        )
        / 30.0,
        "bbs_prior_history_coverage_min": min(
            _number(home.get("bbsHistoryCoverage")) or 0.0,
            _number(away.get("bbsHistoryCoverage")) or 0.0,
        ),
        "bbs_prior_available": 1.0,
    }


def install(feature_module: Any) -> Any:
    if getattr(
        feature_module, "_INQSI_MLB_BBS_PRIOR_GAME_FEATURES_V3_INSTALLED", False
    ):
        return feature_module
    original = feature_module.feature_map

    def wrapped(record: Mapping[str, Any]) -> Dict[str, float]:
        values = dict(original(record))
        values.update(feature_map(record))
        names = set().union(*feature_module.FEATURE_GROUPS.values())
        return {name: float(values.get(name, 0.0) or 0.0) for name in names}

    feature_module.feature_map = wrapped
    feature_module.VERSION = f"{feature_module.VERSION}+{VERSION}"
    feature_module._INQSI_MLB_BBS_PRIOR_GAME_FEATURES_INSTALLED = True
    feature_module._INQSI_MLB_BBS_PRIOR_GAME_FEATURES_V2_INSTALLED = True
    feature_module._INQSI_MLB_BBS_PRIOR_GAME_FEATURES_V3_INSTALLED = True
    return feature_module
