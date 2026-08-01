from __future__ import annotations

from typing import Any, Mapping

import backfill
import handler


def _american(probability: float) -> float:
    p = max(0.01, min(0.99, float(probability)))
    return -100.0 * p / (1.0 - p) if p >= 0.5 else 100.0 * (1.0 - p) / p


def _settle_compatible(payload: Mapping[str, Any]):
    row = dict(payload)
    signals = dict(row["signals"])
    fair = float(signals.pop("market_fair_prob", 0.5))
    signals["player_odds"] = _american(fair)
    signals["opponent_odds"] = _american(1.0 - fair)
    row["signals"] = signals
    return handler.settle(row)


backfill.settle = _settle_compatible


def lambda_handler(event, context):
    return backfill.lambda_handler(event, context)
