from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import inqsi_pull_history as history

SLATE_TZ = ZoneInfo("America/New_York")
ENGINE = "MLB-GAME-WINNER-v1.1"
MODEL_VERSION = "INQSI-MLB-GAME-WINNER-v1.1-2026-07-risk-calibrated"

# Learned from July 2026 audits:
# - stable book agreement is useful
# - raw run-line confirmation/movement can be a false positive when paired with
#   reversals, compressed markets, or weak moneyline consensus
# - market consensus must stay the anchor for winner selection
MIN_CONFIRMED_MARKET_PROB = 0.52
STRONG_MARKET_PROB = 0.54
MAX_CONFIRMATION_DIVERGENCE = 0.025


def _today_et() -> str:
    return datetime.now(SLATE_TZ).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _game_day(game: Dict[str, Any]) -> Optional[str]:
    dt = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return dt.astimezone(SLATE_TZ).date().isoformat() if dt else None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _book_price(game: Dict[str, Any], side: str) -> Optional[float]:
    vals = []
    for payload in (game.get("books") or {}).values():
        if not isinstance(payload, dict):
            continue
        ml = payload.get("ml") or payload.get("moneyline") or {}
        if ml.get(side) is not None:
            vals.append(_safe_float(ml.get(side)))
    return mean(vals) if vals else None


def _spread_price(game: Dict[str, Any], side: str) -> Optional[float]:
    vals = []
    key = f"{side}_price"
    for payload in (game.get("books") or {}).values():
        spread = (payload or {}).get("spread") or {}
        if spread.get(key) is not None:
            vals.append(_safe_float(spread.get(key)))
    return mean(vals) if vals else None


def _series_for_game(pulls: List[Dict[str, Any]], latest_game: Dict[str, Any]) -> List[Dict[str, Any]]:
    key = latest_game.get("game_key") or latest_game.get("game_id") or latest_game.get("id")
    game_id = latest_game.get("game_id") or latest_game.get("id") or key
    rows = []
    for pull in pulls:
        pulled_at = pull.get("pulled_at")
        for game in pull.get("games") or []:
            if (game.get("game_key") == key) or (game.get("game_id") == game_id) or (game.get("id") == game_id):
                probs = history.book_probs(game)
                if probs:
                    rows.append({"pulled_at": pulled_at, "game": game, "probs": probs})
                break
    return rows


def _reversals(values: List[float]) -> int:
    signs = []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        signs.append(1 if diff > 0.0005 else -1 if diff < -0.0005 else 0)
    return sum(1 for i in range(1, len(signs)) if signs[i] and signs[i - 1] and signs[i] != signs[i - 1])


def _confidence_tier(prob: float, score: float, tags: List[str]) -> str:
    edge = abs(prob - 0.5)
    if "INSUFFICIENT_HISTORY" in tags:
        return "Baseline"
    if score >= 72 and edge >= 0.12:
        return "Premium"
    if score >= 64 and edge >= 0.08:
        return "Solid"
    if score >= 56 and edge >= 0.04:
        return "Lean"
    if score >= 50:
        return "Coin Flip"
    return "Pass"


def _clamp_probability(prob: float, min_prob: float = 0.05, max_prob: float = 0.95) -> float:
    return max(min_prob, min(max_prob, prob))


def _confirmed_run_line(tags: List[str], latest_prob: float, delta: float, spread_move: Optional[float], div: float, rev: int) -> bool:
    if spread_move is None:
        return False
    if not (delta >= 0.012 and spread_move < -8):
        return False
    if latest_prob < MIN_CONFIRMED_MARKET_PROB:
        return False
    if div > MAX_CONFIRMATION_DIVERGENCE:
        return False
    if rev > 1:
        return False
    return True


def _side_score(series: List[Dict[str, Any]], side: str) -> Dict[str, Any]:
    latest = series[-1]
    game = latest["game"]
    probs = [float(row["probs"][side]) for row in series if row.get("probs") and row["probs"].get(side) is not None]
    latest_prob = probs[-1] if probs else 0.5
    start_prob = probs[0] if probs else latest_prob
    delta = latest_prob - start_prob
    rev = _reversals(probs)
    latest_gap = abs(float(latest["probs"].get("home") or 0.5) - float(latest["probs"].get("away") or 0.5))
    div = float(latest["probs"].get("book_divergence") or 0)
    book_count = int(latest["probs"].get("book_count") or 0)
    spread_start = _spread_price(series[0]["game"], side) if series else None
    spread_latest = _spread_price(game, side)
    spread_move = None if spread_start is None or spread_latest is None else spread_latest - spread_start
    market_price = _book_price(game, side)

    tags: List[str] = []
    if len(series) < 4:
        tags.append("LOW_PULL_DEPTH")
    if len(series) < 2:
        tags.append("SINGLE_PULL_BASELINE")
    if delta >= 0.012:
        tags.append("STEAM")
    if delta <= -0.012:
        tags.append("RESISTANCE")
    if div <= 0.020:
        tags.append("BOOK_AGREEMENT")
    if div >= 0.040:
        tags.append("BOOK_DIVERGENCE")
    if rev:
        tags.append("REVERSAL")
    if spread_move is not None and abs(spread_move) >= 8:
        tags.append("RUN_LINE_MOVEMENT")
    if _confirmed_run_line(tags, latest_prob, delta, spread_move, div, rev):
        tags.append("RUN_LINE_CONFIRMATION")
    elif spread_move is not None and delta > 0 and spread_move < -8:
        tags.append("UNCONFIRMED_RUN_LINE_MOVE")
    if latest_gap < 0.05:
        tags.append("COMPRESSED_MARKET")

    raw_score = 50 + (latest_prob - 0.5) * 95 + delta * 575 - div * 300 - rev * 6.5
    raw_score += min(book_count, 10) * 0.65

    tagset = set(tags)
    market_edge = latest_prob - 0.5

    # Market sanity: do not let movement dominate if the market consensus itself
    # is still not on this side.
    if latest_prob < 0.50:
        raw_score -= 7.0
    elif latest_prob >= STRONG_MARKET_PROB and "BOOK_AGREEMENT" in tagset:
        raw_score += 1.5

    # Confirmation is only valuable when market + books + movement agree.
    if "RUN_LINE_CONFIRMATION" in tagset:
        raw_score += 2.0
    elif "RUN_LINE_MOVEMENT" in tagset:
        raw_score -= 1.25
        if rev >= 2:
            raw_score -= 2.0

    if "STEAM" in tagset:
        if "BOOK_AGREEMENT" in tagset and rev <= 1 and market_edge >= 0.02:
            raw_score += 1.0
        elif rev >= 3 or "BOOK_DIVERGENCE" in tagset:
            raw_score -= 2.0

    if "BOOK_DIVERGENCE" in tagset:
        raw_score -= 7.0
    if "COMPRESSED_MARKET" in tagset:
        raw_score -= 3.0
        if "RUN_LINE_MOVEMENT" in tagset:
            raw_score -= 1.5
    if "UNCONFIRMED_RUN_LINE_MOVE" in tagset:
        raw_score -= 2.0
    if "LOW_PULL_DEPTH" in tagset:
        raw_score -= 5.0
    if "SINGLE_PULL_BASELINE" in tagset:
        raw_score -= 8.0

    score = round(max(0.0, min(100.0, raw_score)), 2)
    adjusted_prob = _clamp_probability(1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0)))
    return {
        "side": side,
        "team": game.get("home_team") if side == "home" else game.get("away_team"),
        "score": score,
        "winProbability": round(adjusted_prob, 4),
        "winProbabilityPct": round(adjusted_prob * 100.0, 2),
        "marketConsensusProbability": round(latest_prob, 5),
        "probStart": round(start_prob, 5),
        "probLatest": round(latest_prob, 5),
        "delta": round(delta, 5),
        "bookCount": book_count,
        "bookDivergence": round(div, 5),
        "latestGap": round(latest_gap, 5),
        "reversalCount": rev,
        "runLineMovement": round(spread_move, 3) if spread_move is not None else None,
        "averageAmericanOdds": round(market_price, 2) if market_price is not None else None,
        "tags": sorted(set(tags)),
    }


def _prediction_for_game(pulls: List[Dict[str, Any]], latest_game: Dict[str, Any], slate_date: str) -> Optional[Dict[str, Any]]:
    series = _series_for_game(pulls, latest_game)
    if not series:
        return None
    home = _side_score(series, "home")
    away = _side_score(series, "away")
    pick = home if home["score"] >= away["score"] else away
    opponent = away if pick["side"] == "home" else home
    tags = sorted(set(pick.get("tags") or []))
    confidence = _confidence_tier(float(pick["winProbability"]), float(pick["score"]), tags)
    return {
        "ok": True,
        "sport": "mlb",
        "modelVersion": MODEL_VERSION,
        "engine": ENGINE,
        "slate_date": slate_date,
        "gameId": latest_game.get("game_id") or latest_game.get("id") or latest_game.get("game_key"),
        "gameKey": latest_game.get("game_key"),
        "homeTeam": latest_game.get("home_team"),
        "awayTeam": latest_game.get("away_team"),
        "commenceTime": latest_game.get("commence_time"),
        "providerSportKey": latest_game.get("provider_sport_key"),
        "predictedWinner": pick.get("team"),
        "predictedSide": pick.get("side"),
        "opponent": opponent.get("team"),
        "winProbability": pick.get("winProbability"),
        "winProbabilityPct": pick.get("winProbabilityPct"),
        "score": pick.get("score"),
        "confidenceTier": confidence,
        "pickQuality": "GAME_WINNER_BASELINE" if "LOW_PULL_DEPTH" in tags else "GAME_WINNER_SIGNAL",
        "tags": tags,
        "pullCountForGame": len(series),
        "homeSignal": home,
        "awaySignal": away,
        "reason": "Market consensus plus risk-calibrated movement, book agreement, divergence, reversals, run-line confirmation quality, and pull depth.",
        "createdAt": _now(),
    }


def _store_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
    if history.PULLS is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    item = history.ddb_safe({
        "PK": f"GAME_WINNERS#mlb#{row.get('slate_date')}",
        "SK": f"GAME#{row.get('commenceTime') or 'unknown'}#{row.get('gameId')}",
        "record_type": "mlb_game_winner_prediction",
        "sport": "mlb",
        "slate_date": row.get("slate_date"),
        "game_id": row.get("gameId"),
        "game_key": row.get("gameKey"),
        "predicted_winner": row.get("predictedWinner"),
        "confidence_tier": row.get("confidenceTier"),
        "score": row.get("score"),
        "win_probability": row.get("winProbability"),
        "created_at": row.get("createdAt"),
        "data": row,
    })
    history.PULLS.put_item(Item=item)
    return {"ok": True, "pk": item["PK"], "sk": item["SK"]}


def predict_all(slate_date: Optional[str] = None, store: bool = False, limit: int = 500) -> Dict[str, Any]:
    slate = slate_date or _today_et()
    pulls = history.query_pulls("mlb", slate, limit)
    if not pulls:
        return {"ok": True, "sport": "mlb", "slate_date": slate, "engine": ENGINE, "count": 0, "predictions": [], "message": "No MLB pull history found for this slate."}
    latest_pull = pulls[-1]
    latest_games = [g for g in latest_pull.get("games") or [] if _game_day(g) == slate]
    predictions: List[Dict[str, Any]] = []
    stored = []
    for game in latest_games:
        row = _prediction_for_game(pulls, game, slate)
        if not row:
            continue
        if store:
            row["stored"] = _store_prediction(row)
            stored.append(row.get("stored"))
        predictions.append(row)
    predictions.sort(key=lambda r: (float(r.get("score") or 0), float(r.get("winProbability") or 0)), reverse=True)
    for idx, row in enumerate(predictions, 1):
        row["rank"] = idx
    return {
        "ok": True,
        "sport": "mlb",
        "slate_date": slate,
        "engine": ENGINE,
        "modelVersion": MODEL_VERSION,
        "pullCount": len(pulls),
        "latestPullAt": latest_pull.get("pulled_at"),
        "gameCount": len(latest_games),
        "count": len(predictions),
        "allGamesPredicted": len(predictions) == len(latest_games),
        "stored": store,
        "storedCount": len([x for x in stored if x and x.get("ok")]),
        "predictions": predictions,
    }
