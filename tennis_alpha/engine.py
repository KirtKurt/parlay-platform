from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from features import FEATURE_NAMES, build, vector
from model import initial_state, sigmoid
from ratings import RatingStore

LR = 0.035
L2 = 0.0005


def _orient(winner: str, loser: str, date: int) -> Tuple[str, str, int]:
    raw = f"{winner}|{loser}|{date}".encode()
    if int(hashlib.sha1(raw).hexdigest()[:8], 16) % 2 == 0:
        return winner, loser, 1
    return loser, winner, 0


def _num(value: Any) -> float:
    try:
        if value in (None, "", "NA"):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class TennisEngine:
    def __init__(self, tour: str) -> None:
        self.tour = tour.lower()
        seed = initial_state()
        self.weights = [0.4] + [float(w) for w in seed["weights"][1:]]
        self.bias = 0.0
        self.ratings = RatingStore()
        self.samples = 0
        self.version = 1

    def features_for(self, row: Mapping[str, Any], player: str, opponent: str) -> Dict[str, float]:
        try:
            date = int(row.get("tourney_date") or 0)
        except ValueError:
            date = 0
        surface = str(row.get("surface") or "hard")
        pre = self.ratings.pre_match(player, opponent, surface, date)
        winner = str(row.get("winner_name") or "")
        if player == winner:
            rank_edge = _num(row.get("winner_rank_points")) - _num(row.get("loser_rank_points"))
        else:
            rank_edge = _num(row.get("loser_rank_points")) - _num(row.get("winner_rank_points"))
        market = row.get("market_fair_prob")
        signals = {
            "market_fair_prob": float(market) if market not in (None, "") else 0.5,
            "elo_diff": pre["elo_diff"],
            "surface_elo_diff": pre["surface_elo_diff"],
            "rank_points_edge": rank_edge,
            "recent_surface_wr_diff": pre["recent_surface_wr_diff"],
            "h2h_edge": pre["h2h_edge"],
            "serve_points_won_diff": 0.0,
            "return_points_won_diff": 0.0,
            "break_points_saved_diff": 0.0,
            "rest_days_diff": 0.0,
            "best_of_five": str(row.get("best_of") or "") == "5",
        }
        return build(signals)

    def predict_row(self, row: Mapping[str, Any], player: str, opponent: str) -> float:
        feat = self.features_for(row, player, opponent)
        z = self.bias + sum(w * x for w, x in zip(self.weights, vector(feat)))
        return sigmoid(z)

    def observe(self, row: Mapping[str, Any], train: bool = True) -> Tuple[float, int]:
        winner = str(row.get("winner_name") or "")
        loser = str(row.get("loser_name") or "")
        try:
            date = int(row.get("tourney_date") or 0)
        except ValueError:
            date = 0
        player, opponent, y = _orient(winner, loser, date)
        p = self.predict_row(row, player, opponent)
        if train:
            feat = self.features_for(row, player, opponent)
            err = y - p
            xs = vector(feat)
            self.weights = [w + LR * (err * x - L2 * w) for w, x in zip(self.weights, xs)]
            self.bias += LR * err
            self.samples += 1
            self.version += 1
        self.ratings.observe(winner, loser, str(row.get("surface") or "hard"), date)
        return p, y


def logloss(p: float, y: int) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def backtest(rows: Iterable[dict], tour: str, train_until: int) -> Dict[str, Any]:
    engine = TennisEngine(tour)
    preds: List[float] = []
    labels: List[int] = []
    used = 0
    ordered = sorted(
        list(rows),
        key=lambda r: (str(r.get("tourney_date") or ""), str(r.get("match_num") or "")),
    )
    for row in ordered:
        winner = str(row.get("winner_name") or "")
        loser = str(row.get("loser_name") or "")
        if not winner or not loser:
            continue
        try:
            date = int(row.get("tourney_date") or 0)
        except ValueError:
            continue
        in_test = date >= train_until
        p, y = engine.observe(row, train=not in_test)
        used += 1
        if in_test:
            preds.append(p)
            labels.append(y)
    n = len(preds)
    if not n:
        return {"tour": tour, "train_until": train_until, "test_matches": 0}
    acc = sum(1 for p, y in zip(preds, labels) if (p >= 0.5) == (y == 1)) / n
    ll = sum(logloss(p, y) for p, y in zip(preds, labels)) / n
    brier = sum((p - y) ** 2 for p, y in zip(preds, labels)) / n
    return {
        "tour": tour,
        "train_until": train_until,
        "matches_seen": used,
        "test_matches": n,
        "accuracy": round(acc, 4),
        "logloss": round(ll, 4),
        "brier": round(brier, 4),
        "mean_pred": round(sum(preds) / n, 4),
        "base_rate": round(sum(labels) / n, 4),
        "training_samples": engine.samples,
        "elo_players": len(engine.ratings.elo.overall),
        "features": list(FEATURE_NAMES),
    }
