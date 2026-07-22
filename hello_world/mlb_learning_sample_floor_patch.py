"""Conservatively gate audit-derived MLB score adjustments by sample size.

Deterministic signal and integrity gates remain active at every sample size.  This
patch only prevents the rolling-audit learning overlay from changing live winner
scores when the clean cohort is too small to support a stable estimate.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable


VERSION = "MLB-LEARNING-SAMPLE-FLOOR-v1-clean-30-current-10"
MIN_HISTORICAL_ROWS = max(
    1,
    int(os.environ.get("INQSI_MLB_MIN_LEARNING_HISTORY_ROWS", "30")),
)
MIN_CURRENT_ROWS = max(
    1,
    int(os.environ.get("INQSI_MLB_MIN_LEARNING_CURRENT_ROWS", "10")),
)


def _counts(learning: Dict[str, Any]) -> Dict[str, int]:
    historical = learning.get("historicalStats") or {}
    windows = learning.get("multiWindowStats") or {}
    current = windows.get("current24h") or {}
    return {
        "historicalRows": int(historical.get("historicalRowsUsed") or 0),
        "current24hRows": int(current.get("rowCount") or 0),
    }


def status(module: Any) -> Dict[str, Any]:
    try:
        learning = module._latest_learning() or {}
    except Exception:
        learning = {}
    counts = _counts(learning)
    current = counts["current24hRows"]
    eligible = bool(
        counts["historicalRows"] >= MIN_HISTORICAL_ROWS
        and (current == 0 or current >= MIN_CURRENT_ROWS)
    )
    reasons = []
    if counts["historicalRows"] < MIN_HISTORICAL_ROWS:
        reasons.append("insufficient_clean_historical_rows")
    if 0 < current < MIN_CURRENT_ROWS:
        reasons.append("insufficient_current_24h_rows")
    return {
        "ok": True,
        "version": VERSION,
        "eligible": eligible,
        "minimumHistoricalRows": MIN_HISTORICAL_ROWS,
        "minimumCurrent24hRowsWhenPresent": MIN_CURRENT_ROWS,
        **counts,
        "reasons": reasons,
        "policy": (
            "Audit-derived score weights remain zero until at least 30 clean "
            "historical rows exist and any non-empty current 24-hour cohort has "
            "at least 10 rows. Deterministic risk gates remain active."
        ),
    }


def apply(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_LEARNING_SAMPLE_FLOOR_APPLIED", False):
        return module

    original = module._learning_adjustment

    def guarded_learning_adjustment(tags: Iterable[str]) -> float:
        gate = status(module)
        if gate.get("eligible") is not True:
            return 0.0
        return float(original(tags))

    module._learning_adjustment = guarded_learning_adjustment
    module.mlbLearningSampleFloorStatus = lambda: status(module)
    module.MLB_LEARNING_SAMPLE_FLOOR_VERSION = VERSION
    module._INQSI_MLB_LEARNING_SAMPLE_FLOOR_APPLIED = True
    return module
