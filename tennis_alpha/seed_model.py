from __future__ import annotations

from decimal import Decimal
from typing import Any

from engine import TennisEngine, backtest
from handler import write_model_state
from sackmann import iter_years


def _ordered(tour: str, start: int, end: int) -> list[dict]:
    rows = [
        row
        for row in iter_years(tour, start, end)
        if str(row.get("winner_name") or "") and str(row.get("loser_name") or "")
    ]
    rows.sort(key=lambda r: (str(r.get("tourney_date") or ""), str(r.get("match_num") or "")))
    return rows


def seed_tour(tour: str, start: int = 2014, end: int = 2026, train_until: int = 20250101) -> dict[str, Any]:
    rows = _ordered(tour, start, end)
    metrics = backtest(rows, tour, train_until)
    engine = TennisEngine(tour)
    for row in rows:
        engine.observe(row, train=True)
    write_model_state(tour, engine.weights, engine.bias, engine.samples, source="tennis_engine_full_history")
    return {
        "stack": "tennis-alpha",
        "tour": tour,
        "rows": len(rows),
        "training_samples": engine.samples,
        "weights": [float(Decimal(str(w))) for w in engine.weights],
        "bias": float(engine.bias),
        "holdout": metrics,
        "eligible": engine.samples >= 200,
    }
