"""Chronological gate evaluation. No random splits. No promotion switch."""
from __future__ import annotations

import math

from stack2mlb.calibrate import brier, ece, reliability, wilson_lower
from stack2mlb.elo_history import book_from_grades
from stack2mlb.stack import decide


def log_loss(y, p) -> float:
    acc = n = 0.0
    for yi, pi in zip(y, p):
        pi = min(1 - 1e-12, max(1e-12, float(pi)))
        acc += -(yi * math.log(pi) + (1 - yi) * math.log(1 - pi))
        n += 1
    return acc / n if n else 0.0


def evaluate(games: list[dict], grades: list[dict] | None = None) -> dict:
    ordered = sorted(games, key=lambda g: (str(g.get("commence_time") or ""), str(g.get("game_id") or "")))
    y, p_official, p_stack, y_bet, statuses = [], [], [], [], []
    for i, game in enumerate(ordered):
        if game.get("home_win") is None:
            continue
        prior = (grades or ordered[:i])
        cutoff = game.get("commence_time")
        book = book_from_grades(prior, before=cutoff) if cutoff else None
        elo = game.get("p_elo")
        if elo is None and book is not None and game.get("home_id") and game.get("away_id"):
            elo = book.p_home(str(game["home_id"]), str(game["away_id"]),
                              game.get("home_starter_id"), game.get("away_starter_id"))
        if elo is None:
            elo = 0.5
        decision = decide(
            p_lgb=float(game["p_lgb"]),
            p_poisson=float(game["p_poisson"]),
            p_market=float(game["p_market"]),
            p_elo=float(elo),
            p_home_official=float(game["p_lgb"]),
            starter_unverified=bool(game.get("starter_unverified", False)),
        )
        y.append(int(game["home_win"]))
        p_official.append(float(game["p_lgb"]))
        p_stack.append(decision.p_stack)
        statuses.append(decision.pick_status)
        if decision.pick_status == "bet":
            pick_home = decision.p_stack >= 0.5
            y_bet.append(int(game["home_win"]) if pick_home else 1 - int(game["home_win"]))
    return {
        "system": "2stackMLB",
        "promoted": False,
        "games": len(y),
        "bet_n": len(y_bet),
        "pass_or_shrink_n": len(y) - len(y_bet),
        "official_brier": brier(y, p_official) if y else None,
        "stack_brier": brier(y, p_stack) if y else None,
        "official_log_loss": log_loss(y, p_official) if y else None,
        "stack_log_loss": log_loss(y, p_stack) if y else None,
        "official_ece": ece(reliability(y, p_official)) if y else None,
        "stack_ece": ece(reliability(y, p_stack)) if y else None,
        "bet_hits": int(sum(y_bet)) if y_bet else 0,
        "bet_accuracy": (sum(y_bet) / len(y_bet)) if y_bet else None,
        "bet_wilson_lower": wilson_lower(int(sum(y_bet)), len(y_bet)) if y_bet else 0.0,
        "statuses": {"bet": statuses.count("bet"), "shrink": statuses.count("shrink"), "pass": statuses.count("pass")},
        "promotion_allowed": False,
        "note": "Evaluation only. Champion remains KS1 p_home.",
    }
