"""Favorite/underdog grading and ablation for NBA shadow rows.

Does not retrain or promote. Callers supply already-graded predictions.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _segment(rows: Sequence[Mapping], favorite: bool) -> dict:
    picked = [row for row in rows if bool(row.get("favorite_selected")) is favorite]
    hits = [int(bool(row.get("hit"))) for row in picked]
    briers = [float(row["brier"]) for row in picked if row.get("brier") is not None]
    losses = [float(row["log_loss"]) for row in picked if row.get("log_loss") is not None]
    probs = [float(row["p_pred"]) for row in picked if row.get("p_pred") is not None]
    implied = [float(row["p_market"]) for row in picked if row.get("p_market") is not None]
    return {
        "count": len(picked),
        "wins": sum(hits),
        "losses": len(picked) - sum(hits),
        "accuracy": _mean(hits),
        "brier": _mean(briers),
        "log_loss": _mean(losses),
        "avg_predicted": _mean(probs),
        "avg_market_implied": _mean(implied),
    }


def _flip_ledger(rows: Sequence[Mapping]) -> dict:
    flips = [row for row in rows if row.get("market_flip")]
    saved = sum(1 for row in flips if row.get("market_flip_saved"))
    hurt = sum(1 for row in flips if row.get("fundamentals_would_have_won") and not row.get("hit"))
    opposed = sum(1 for row in rows if row.get("fundamentals_correct_vs_market"))
    return {
        "market_flip_count": len(flips),
        "market_flip_saved": saved,
        "losses_after_market_flip": hurt,
        "fundamentals_correct_vs_market": opposed,
    }


def rolling_audit(rows: Sequence[Mapping], *, windows: Sequence[int] = (7, 30, 100)) -> dict:
    ordered = list(rows)
    payload = {
        "sample": len(ordered),
        "favorites": _segment(ordered, True),
        "underdogs": _segment(ordered, False),
        "flips": _flip_ledger(ordered),
        "windows": {},
        "authority_changed": False,
        "quota_applied": False,
    }
    for size in windows:
        chunk = ordered[-int(size) :] if size != 100 or len(ordered) >= 100 else ordered[-int(size) :]
        if size == 100:
            chunk = ordered[-100:]
        payload["windows"][str(size)] = {
            "sample": len(chunk),
            "favorites": _segment(chunk, True),
            "underdogs": _segment(chunk, False),
            "flips": _flip_ledger(chunk),
        }
    return payload


def ablation_compare(fundamentals: Sequence[Mapping], market_aware: Sequence[Mapping]) -> dict:
    def pack(name: str, rows: Sequence[Mapping]) -> dict:
        hits = [int(bool(row.get("hit"))) for row in rows]
        return {
            "name": name,
            "count": len(rows),
            "accuracy": _mean(hits),
            "brier": _mean([float(row["brier"]) for row in rows if row.get("brier") is not None]),
            "log_loss": _mean([float(row["log_loss"]) for row in rows if row.get("log_loss") is not None]),
            "favorites": _segment(rows, True),
            "underdogs": _segment(rows, False),
        }

    return {
        "model_a_fundamentals_only": pack("fundamentals_only", fundamentals),
        "model_b_fundamentals_plus_market": pack("fundamentals_plus_market", market_aware),
        "authority_changed": False,
        "assumed_market_helps": False,
    }
