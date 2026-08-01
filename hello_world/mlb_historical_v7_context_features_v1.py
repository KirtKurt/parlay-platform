"""Leakage-safe prior-game and run-environment features for V7 shadow learning."""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Sequence

VERSION = "MLB-HISTORICAL-V7-CONTEXT-FEATURES-v1"

EXTRA_FEATURES = (
    "bbsPriorWinRate5Diff",
    "bbsPriorWinRate10Diff",
    "bbsPriorWinRate30Diff",
    "bbsPriorRunDiff5Diff",
    "bbsPriorRunDiff10Diff",
    "bbsPriorRunsFor10Diff",
    "bbsPriorRunsAgainst10Diff",
    "bbsPriorStreakDiff",
    "bbsPriorRestDiff",
    "bbsPriorVenueWinRate10Diff",
    "bbsPriorHistoryGamesDiff",
    "bbsPriorHistoryCoverageMin",
    "bbsPriorAvailable",
    "parkRunFactorCentered",
    "weatherRunFactor",
    "starterRunEnvironmentInteraction",
    "bullpenRunEnvironmentInteraction",
    "lineupRunEnvironmentInteraction",
)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _value(learner: Any, signal: Mapping[str, Any], names: Sequence[str]):
    return learner._fundamental(signal, names)


def _diff(
    learner: Any,
    home: Mapping[str, Any],
    away: Mapping[str, Any],
    names: Sequence[str],
    scale: float = 1.0,
) -> float:
    home_value = _value(learner, home, names)
    away_value = _value(learner, away, names)
    if home_value is None or away_value is None:
        return 0.0
    return (_f(home_value) - _f(away_value)) / scale


def install(learner: Any) -> Any:
    if getattr(learner, "_INQSI_V7_CONTEXT_FEATURES_INSTALLED", False):
        return learner
    original_pair = learner.pair_features
    learner.FEATURES = tuple(dict.fromkeys(tuple(learner.FEATURES) + EXTRA_FEATURES))

    def pair_features(
        home: Mapping[str, Any],
        away: Mapping[str, Any],
        policy: Mapping[str, Any],
    ) -> Dict[str, float]:
        values = dict(original_pair(home, away, policy))
        home_games = _value(learner, home, ("bbsHistoryGames",))
        away_games = _value(learner, away, ("bbsHistoryGames",))
        prior_available = bool(
            home.get("historicalBbsPriorContextApplied") is True
            and away.get("historicalBbsPriorContextApplied") is True
            and home_games is not None
            and away_games is not None
            and _f(home_games) >= 5.0
            and _f(away_games) >= 5.0
        )

        park = _value(learner, home, ("parkRunFactor",))
        if park is None:
            park = _value(learner, away, ("parkRunFactor",))
        weather = _value(learner, home, ("weatherRunFactor",))
        if weather is None:
            weather = _value(learner, away, ("weatherRunFactor",))
        park_centered = _f(park, 1.0) - 1.0 if park is not None else 0.0
        weather_value = _f(weather) if weather is not None else 0.0
        run_environment = park_centered + weather_value

        values.update(
            {
                "bbsPriorWinRate5Diff": _diff(
                    learner, home, away, ("bbsWinRate5",)
                ),
                "bbsPriorWinRate10Diff": _diff(
                    learner, home, away, ("bbsWinRate10",)
                ),
                "bbsPriorWinRate30Diff": _diff(
                    learner, home, away, ("bbsWinRate30",)
                ),
                "bbsPriorRunDiff5Diff": _diff(
                    learner, home, away, ("bbsRunDiffPerGame5",), 10.0
                ),
                "bbsPriorRunDiff10Diff": _diff(
                    learner, home, away, ("bbsRunDiffPerGame10",), 10.0
                ),
                "bbsPriorRunsFor10Diff": _diff(
                    learner, home, away, ("bbsRunsForPerGame10",), 10.0
                ),
                "bbsPriorRunsAgainst10Diff": _diff(
                    learner, home, away, ("bbsRunsAgainstPerGame10",), 10.0
                ),
                "bbsPriorStreakDiff": _diff(
                    learner, home, away, ("bbsStreakNormalized",)
                ),
                "bbsPriorRestDiff": _diff(
                    learner, home, away, ("bbsRestDaysNormalized",)
                ),
                "bbsPriorVenueWinRate10Diff": _diff(
                    learner, home, away, ("bbsVenueWinRate10",)
                ),
                "bbsPriorHistoryGamesDiff": (
                    (_f(home_games) - _f(away_games)) / 30.0
                    if home_games is not None and away_games is not None
                    else 0.0
                ),
                "bbsPriorHistoryCoverageMin": min(
                    _f(_value(learner, home, ("bbsHistoryCoverage",))),
                    _f(_value(learner, away, ("bbsHistoryCoverage",))),
                )
                if prior_available
                else 0.0,
                "bbsPriorAvailable": float(prior_available),
                "parkRunFactorCentered": park_centered,
                "weatherRunFactor": weather_value,
                "starterRunEnvironmentInteraction": _f(
                    values.get("starterDiff")
                )
                * run_environment,
                "bullpenRunEnvironmentInteraction": _f(
                    values.get("bullpenDiff")
                )
                * run_environment,
                "lineupRunEnvironmentInteraction": _f(values.get("lineupDiff"))
                * run_environment,
            }
        )
        if not prior_available:
            for name in EXTRA_FEATURES[:12]:
                values[name] = 0.0
        return values

    learner.pair_features = pair_features
    learner.FEATURE_VERSION = (
        f"{learner.FEATURE_VERSION}+MLB-V7-CONTEXT-FEATURES-v1"
    )
    learner.V7_CONTEXT_FEATURES_VERSION = VERSION
    learner._INQSI_V7_CONTEXT_FEATURES_INSTALLED = True
    return learner
