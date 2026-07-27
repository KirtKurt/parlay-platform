"""V7 shadow-refit cadence and challenger-selection repair.

Canonical promotion still requires a genuinely untouched 200-game audit. This
module does not change that threshold. It defines a separate 50-game cadence for
read-only shadow refits so model diagnostics can advance without granting any
production authority.

It also changes inner-model ranking so chronological mean accuracy, worst-day
accuracy and calibration decide among shadow candidates when the coarse
80%-every-day pass-rate signal is tied. The final production gate is unchanged.
"""
from __future__ import annotations

from typing import Any, Mapping

VERSION = "MLB-HISTORICAL-V7-LEARNING-CADENCE-v2-shadow-separated"
SHADOW_REFIT_INCREMENT_GAMES = 50


def install(handler: Any, learner: Any) -> None:
    if getattr(handler, "_INQSI_MLB_V7_LEARNING_CADENCE_INSTALLED", False):
        return

    # Do not lower FRESH_AUDIT_INCREMENT_GAMES. The canonical handler clamps that
    # value to the 200-game untouched-audit floor by design. Shadow refits are
    # read-only and use their own cadence outside the promotion state machine.
    handler.V7_SHADOW_REFIT_INCREMENT_GAMES = SHADOW_REFIT_INCREMENT_GAMES

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
    handler._INQSI_MLB_V7_LEARNING_CADENCE_INSTALLED = True
