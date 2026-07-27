"""V7 learning-cadence and challenger-selection repair.

The production pipeline was waiting for 250 additional settled games between
optimization rounds. That delayed feedback for weeks and made the system appear
stalled even while ingestion advanced. This patch lowers only the prospective
fresh-audit increment; it does not weaken chronological splits, the untouched
holdout, data-integrity checks, or the 80%-every-slate promotion gate.

It also changes inner-model ranking so mean chronological accuracy and
calibration decide among candidates when all candidates fail the very coarse
80%-every-day pass-rate signal. The final production promotion gate is unchanged.
"""
from __future__ import annotations

from typing import Any, Mapping

VERSION = "MLB-HISTORICAL-V7-LEARNING-CADENCE-v1"
DEFAULT_INCREMENT_GAMES = 50


def install(handler: Any, learner: Any) -> None:
    if getattr(handler, "_INQSI_MLB_V7_LEARNING_CADENCE_INSTALLED", False):
        return

    current_increment = int(
        getattr(handler, "FRESH_AUDIT_INCREMENT_GAMES", DEFAULT_INCREMENT_GAMES)
        or DEFAULT_INCREMENT_GAMES
    )
    handler.FRESH_AUDIT_INCREMENT_GAMES = min(
        current_increment, DEFAULT_INCREMENT_GAMES
    )

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
    handler.V7_LEARNING_CADENCE_VERSION = VERSION
    handler._INQSI_MLB_V7_LEARNING_CADENCE_INSTALLED = True
