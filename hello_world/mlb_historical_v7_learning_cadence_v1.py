"""V7 challenger-ranking repair with separated learning cadence.

Shadow refits are scheduled independently every 20 new eligible games. The
canonical optimizer still requires at least 200 genuinely untouched games before
promotion; this module accelerates research feedback without weakening the audit.
"""
from __future__ import annotations

from typing import Any, Mapping

VERSION = "MLB-HISTORICAL-V7-LEARNING-CADENCE-v3-fast-shadow-safe-canonical"
SHADOW_REFIT_INCREMENT_GAMES = 20
LIGHTWEIGHT_EVALUATION_INCREMENT_GAMES = 10


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
    learner.V7_LIGHTWEIGHT_EVALUATION_INCREMENT_GAMES = LIGHTWEIGHT_EVALUATION_INCREMENT_GAMES
    handler.V7_LEARNING_CADENCE_VERSION = VERSION
    handler.V7_SHADOW_REFIT_INCREMENT_GAMES = SHADOW_REFIT_INCREMENT_GAMES
    handler.V7_LIGHTWEIGHT_EVALUATION_INCREMENT_GAMES = LIGHTWEIGHT_EVALUATION_INCREMENT_GAMES
    handler._INQSI_MLB_V7_LEARNING_CADENCE_INSTALLED = True
