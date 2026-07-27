"""V7 challenger-ranking repair with separated learning cadence.

Shadow refits are scheduled independently every 50 new eligible games.  This
module does not modify FRESH_AUDIT_INCREMENT_GAMES: the canonical optimizer must
retain the policy minimum of 200 genuinely untouched games before promotion.
"""
from __future__ import annotations

from typing import Any, Mapping

VERSION = "MLB-HISTORICAL-V7-LEARNING-CADENCE-v2-separated-shadow-canonical"
SHADOW_REFIT_INCREMENT_GAMES = 50


def install(handler: Any, learner: Any) -> None:
    if getattr(handler, "_INQSI_MLB_V7_LEARNING_CADENCE_INSTALLED", False):
        return

    if int(getattr(handler, "FRESH_AUDIT_INCREMENT_GAMES", 0) or 0) < 200:
        raise RuntimeError("canonical untouched-audit increment cannot be below 200")

    def chronological_rank(metrics: Mapping[str, Any]):
        f = learner._f
        return (
            f(metrics.get("meanDailyAccuracy")),
            f(metrics.get("minimumDailyAccuracy")),
            f(metrics.get("overallAccuracy")),
            -f(metrics.get("brierScore"), 1.0),
            -f(metrics.get("logLoss"), 10.0),
            f(metrics.get("dailyPassRate")),
        )

    learner._rank = chronological_rank
    learner.V7_LEARNING_CADENCE_VERSION = VERSION
    learner.V7_SHADOW_REFIT_INCREMENT_GAMES = SHADOW_REFIT_INCREMENT_GAMES
    handler.V7_LEARNING_CADENCE_VERSION = VERSION
    handler.V7_SHADOW_REFIT_INCREMENT_GAMES = SHADOW_REFIT_INCREMENT_GAMES
    handler._INQSI_MLB_V7_LEARNING_CADENCE_INSTALLED = True
