from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import inqsi_pull_history as history

SLATE_TZ = ZoneInfo("America/New_York")
ENGINE = "MLB-GAME-WINNER-v1.2"
MODEL_VERSION = "INQSI-MLB-GAME-WINNER-v1.2-2026-07-single-game-ev"

PRIMARY_BOOK = os.environ.get("MLB_PRIMARY_BOOK", "fanduel").strip().lower()
PROMOTION_EDGE_THRESHOLD = float(os.environ.get("MLB_PROMOTION_EDGE_THRESHOLD", "0.0015"))
MIN_PROMOTION_EV = float(os.environ.get("MLB_MIN_PROMOTION_EV", "0.0"))
MAX_PROMOTED_DOG_PRICE = int(os.environ.get("MLB_MAX_PROMOTED_DOG_PRICE", "170"))
MAX_HEAVY_FAVORITE_PRICE = int(os.environ.get("MLB_MAX_HEAVY_FAVORITE_PRICE", "185"))
MIN_PROMOTION_PROB = float(os.environ.get("MLB_MIN_PROMOTION_PROB", "0.35"))
MIN_PROMOTION_PULLS = int(os.environ.get("MLB_MIN_PULLS_FOR_LOCK", "4"))

MIN_CONFIRMED_MARKET_PROB = 0.52
STRONG_MARKET_PROB = 0.54
MAX_CONFIRMATION_DIVERGENCE = 0.025


def _today_et() -> str:
    return datetime.now(SLATE_TZ).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
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


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(float(value))
    except Exception:
        return None


def _team_key(name: Any) -> str:
    return " ".join(str(name or "").lower().strip().split())


def _game_signature(game: Dict[str, Any]) -> str:
    commence = str(game.get("commence_time") or game.get("commenceTime") or "")
    return "|".join([
        _team_key(game.get("away_team") or game.get("awayTeam") or game.get("away")),
        _team_key(game.get("home_team") or game.get("homeTeam") or game.get("home")),
        commence,
    ])


def _provider_game_id(game: Dict[str, Any]) -> str:
    return str(game.get("game_id") or game.get("id") or "").strip()


def _book_payloads(game: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(k).lower().strip(): v for k, v in (game.get("books") or {}).items() if isinstance(v, dict)}


def _american_prob(v: Any) -> Optional[float]:
    try:
        a = int(float(v))
    except Exception:
        return None
    if a == 0:
        return None
    return abs(a) / (abs(a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def _american_profit_multiple(v: Any) -> Optional[float]:
    try:
        a = int(float(v))
    except Exception:
        return None
    if a == 0:
        return None
    return a / 100.0 if a > 0 else 100.0 / abs(a)


def _book_prices(game: Dict[str, Any], side: str) -> List[Tuple[str, int]]:
    prices: List[Tuple[str, int]] = []
    for book, payload in _book_payloads(game).items():
        ml = payload.get("ml") or payload.get("moneyline") or payload.get("h2h") or {}
        price = _safe_int(ml.get(side) or ml.get(f"{side}_price") or ml.get(f"{side}Price"))
        if price is not None:
            prices.append((book, price))
    return prices


def _selected_price(game: Dict[str, Any], side: str) -> Dict[str, Any]:
    prices = _book_prices(game, side)
    if not prices:
        return {"bookKey": None, "americanOdds": None, "priceSource": "missing"}
    for book, price in prices:
        if book == PRIMARY_BOOK:
            return {"bookKey": book, "americanOdds": price, "priceSource": "primary_book"}
    best_book, best_price = max(prices, key=lambda item: item[1])
    return {"bookKey": best_book, "americanOdds": best_price, "priceSource": "best_available_book"}


def _average_book_price(game: Dict[str, Any], side: str) -> Optional[float]:
    vals = [float(price) for _, price in _book_prices(game, side)]
    return mean(vals) if vals else None


def _spread_price(game: Dict[str, Any], side: str) -> Optional[float]:
    vals = []
    key = f"{side}_price"
    for payload in _book_payloads(game).values():
        spread = payload.get("spread") or {}
        if spread.get(key) is not None:
            vals.append(_safe_float(spread.get(key)))
    return mean(vals) if vals else None


def _series_for_game(pulls: List[Dict[str, Any]], latest_game: Dict[str, Any]) -> List[Dict[str, Any]]:
    latest_provider_id = _provider_game_id(latest_game)
    latest_key = latest_game.get("game_key") or latest_provider_id
    latest_sig = _game_signature(latest_game)
    rows: List[Dict[str, Any]] = []
    for pull in pulls:
        pulled_at = pull.get("pulled_at")
        selected = None
        for game in pull.get("games") or []:
            provider_id = _provider_game_id(game)
            if latest_provider_id and provider_id == latest_provider_id:
                selected = game
                break
        if selected is None and not latest_provider_id:
            for game in pull.get("games") or []:
                if (game.get("game_key") == latest_key) or (_game_signature(game) == latest_sig):
                    selected = game
                    break
        if selected is not None:
            probs = history.book_probs(selected)
            if probs:
                rows.append({"pulled_at": pulled_at, "game": selected, "probs": probs})
    return rows


def _reversals(values: List[float]) -> int:
    signs = []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        signs.append(1 if diff > 0.0005 else -1 if diff < -0.0005 else 0)
    return sum(1 for i in range(1, len(signs)) if signs[i] and signs[i - 1] and signs[i] != signs[i - 1])


def _confidence_tier(prob: float, score: float, tags: List[str], promotion_status: str) -> str:
    edge = abs(prob - 0.5)
    if promotion_status == "PROMOTED" and score >= 64 and edge >= 0.045:
        return "Promoted"
    if "INSUFFICIENT_PULL_DEPTH" in tags or "SINGLE_PULL_BASELINE" in tags:
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


def _confirmed_run_line(latest_prob: float, delta: float, spread_move: Optional[float], div: float, rev: int) -> bool:
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


def _side_classification(price: Optional[int]) -> str:
    if price is None:
        return "unknown"
    if price <= -108:
        return "favorite"
    if price >= 108:
        return "underdog"
    return "pickem"


def _promotion_status(*, adjusted_prob: float, selected_price: Optional[int], edge_vs_book: float, ev: float, pull_count: int, tags: List[str]) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    if selected_price is None:
        return "NO_PLAY", ["NO_BETTABLE_PRICE"]
    if pull_count < MIN_PROMOTION_PULLS:
        reasons.append("INSUFFICIENT_PULL_DEPTH")
    if adjusted_prob < MIN_PROMOTION_PROB:
        reasons.append("LOW_MODEL_PROBABILITY")
    if edge_vs_book < PROMOTION_EDGE_THRESHOLD:
        reasons.append("EDGE_BELOW_THRESHOLD")
    if ev < MIN_PROMOTION_EV:
        reasons.append("NEGATIVE_OR_THIN_EV")
    if selected_price > MAX_PROMOTED_DOG_PRICE:
        reasons.append("LONG_DOG_GUARD")
    if selected_price < -MAX_HEAVY_FAVORITE_PRICE and edge_vs_book < PROMOTION_EDGE_THRESHOLD * 2:
        reasons.append("HEAVY_FAVORITE_PRICE_GUARD")
    if "BOOK_DIVERGENCE" in tags and edge_vs_book < PROMOTION_EDGE_THRESHOLD * 2:
        reasons.append("BOOK_DIVERGENCE_GUARD")
    if reasons:
        if any(r in reasons for r in ["INSUFFICIENT_PULL_DEPTH", "EDGE_BELOW_THRESHOLD", "NEGATIVE_OR_THIN_EV"]):
            return "WATCHLIST", reasons
        return "NO_PLAY", reasons
    return "PROMOTED", ["POSITIVE_EV_EDGE"]


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
    selected = _selected_price(game, side)
    selected_price = selected.get("americanOdds")
    avg_market_price = _average_book_price(game, side)
    implied_prob = _american_prob(selected_price) if selected_price is not None else None
    profit_multiple = _american_profit_multiple(selected_price) if selected_price is not None else None

    tags: List[str] = []
    if len(series) < MIN_PROMOTION_PULLS:
        tags.append("INSUFFICIENT_PULL_DEPTH")
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
    if _confirmed_run_line(latest_prob, delta, spread_move, div, rev):
        tags.append("RUN_LINE_CONFIRMATION")
    elif spread_move is not None and delta > 0 and spread_move < -8:
        tags.append("UNCONFIRMED_RUN_LINE_MOVE")
    if latest_gap < 0.05:
        tags.append("COMPRESSED_MARKET")

    raw_score = 50 + (latest_prob - 0.5) * 78 + delta * 650 - div * 300 - rev * 6.5
    raw_score += min(book_count, 10) * 0.65

    tagset = set(tags)
    market_edge = latest_prob - 0.5
    if latest_prob < 0.50:
        raw_score -= 4.0
        if delta >= 0.018 and div <= 0.025 and rev <= 1:
            raw_score += 5.5
    elif latest_prob >= STRONG_MARKET_PROB and "BOOK_AGREEMENT" in tagset:
        raw_score += 1.5

    if "RUN_LINE_CONFIRMATION" in tagset:
        raw_score += 2.0
    elif "RUN_LINE_MOVEMENT" in tagset:
        raw_score -= 1.25
        if rev >= 2:
            raw_score -= 2.0

    if "STEAM" in tagset:
        if "BOOK_AGREEMENT" in tagset and rev <= 1 and market_edge >= -0.015:
            raw_score += 1.75
        elif rev >= 3 or "BOOK_DIVERGENCE" in tagset:
            raw_score -= 2.0

    if "BOOK_DIVERGENCE" in tagset:
        raw_score -= 7.0
    if "COMPRESSED_MARKET" in tagset:
        raw_score -= 1.0
        if "RUN_LINE_MOVEMENT" in tagset:
            raw_score -= 1.5
    if "UNCONFIRMED_RUN_LINE_MOVE" in tagset:
        raw_score -= 2.0
    if "INSUFFICIENT_PULL_DEPTH" in tagset:
        raw_score -= 4.0
    if "SINGLE_PULL_BASELINE" in tagset:
        raw_score -= 8.0

    score = round(max(0.0, min(100.0, raw_score)), 2)
    adjusted_prob = _clamp_probability(1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0)))
    adjusted_prob = _clamp_probability((adjusted_prob * 0.45) + (latest_prob * 0.55), 0.05, 0.95)
    edge_vs_book = adjusted_prob - implied_prob if implied_prob is not None else 0.0
    ev = adjusted_prob * profit_multiple - (1.0 - adjusted_prob) if profit_multiple is not None else -1.0
    status, reasons = _promotion_status(
        adjusted_prob=adjusted_prob,
        selected_price=selected_price,
        edge_vs_book=edge_vs_book,
        ev=ev,
        pull_count=len(series),
        tags=tags,
    )
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
        "averageAmericanOdds": round(avg_market_price, 2) if avg_market_price is not None else None,
        "americanOdds": selected_price,
        "bookKey": selected.get("bookKey"),
        "priceSource": selected.get("priceSource"),
        "bookImpliedProbability": round(implied_prob, 5) if implied_prob is not None else None,
        "edgeVsBook": round(edge_vs_book, 5),
        "edgeVsBookPct": round(edge_vs_book * 100.0, 2),
        "expectedValue": round(ev, 5),
        "expectedValuePct": round(ev * 100.0, 2),
        "marketSide": _side_classification(selected_price),
        "promotionStatus": status,
        "promotionReasons": reasons,
        "tags": sorted(set(tags)),
    }


def _pick_score(side: Dict[str, Any]) -> float:
    status_bonus = {"PROMOTED": 8.0, "WATCHLIST": 2.0, "NO_PLAY": -10.0}.get(str(side.get("promotionStatus")), 0.0)
    ev = float(side.get("expectedValue") or -1.0)
    edge = float(side.get("edgeVsBook") or 0.0)
    score = float(side.get("score") or 0.0)
    return score + ev * 80.0 + edge * 180.0 + status_bonus


def _prediction_for_game(pulls: List[Dict[str, Any]], latest_game: Dict[str, Any], slate_date: str) -> Optional[Dict[str, Any]]:
    series = _series_for_game(pulls, latest_game)
    if not series:
        return None
    home = _side_score(series, "home")
    away = _side_score(series, "away")
    pick = home if _pick_score(home) >= _pick_score(away) else away
    opponent = away if pick["side"] == "home" else home
    tags = sorted(set(pick.get("tags") or []))
    confidence = _confidence_tier(float(pick["winProbability"]), float(pick["score"]), tags, str(pick.get("promotionStatus")))
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
        "americanOdds": pick.get("americanOdds"),
        "bookKey": pick.get("bookKey"),
        "priceSource": pick.get("priceSource"),
        "marketSide": pick.get("marketSide"),
        "winProbability": pick.get("winProbability"),
        "winProbabilityPct": pick.get("winProbabilityPct"),
        "bookImpliedProbability": pick.get("bookImpliedProbability"),
        "edgeVsBook": pick.get("edgeVsBook"),
        "edgeVsBookPct": pick.get("edgeVsBookPct"),
        "expectedValue": pick.get("expectedValue"),
        "expectedValuePct": pick.get("expectedValuePct"),
        "promotionStatus": pick.get("promotionStatus"),
        "promotionReasons": pick.get("promotionReasons") or [],
        "score": pick.get("score"),
        "confidenceTier": confidence,
        "pickQuality": "GAME_WINNER_BASELINE" if "INSUFFICIENT_PULL_DEPTH" in tags else "GAME_WINNER_SIGNAL",
        "tags": tags,
        "pullCountForGame": len(series),
        "homeSignal": home,
        "awaySignal": away,
        "reason": "Single-game MLB moneyline pick using de-vigged market consensus, 15-minute line movement, real book price, EV, pull depth, and promotion guardrails.",
        "createdAt": _now(),
    }


def _store_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
    if history.PULLS is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    item = history.ddb_safe({
        "PK": f"GAME_WINNERS#mlb#{row.get('slate_date')}",
        "SK": f"GAME#{row.get('commenceTime') or 'unknown'}#{row.get('gameId')}",
        "record_type": "mlb_single_game_moneyline_prediction",
        "sport": "mlb",
        "slate_date": row.get("slate_date"),
        "game_id": row.get("gameId"),
        "game_key": row.get("gameKey"),
        "predicted_winner": row.get("predictedWinner"),
        "confidence_tier": row.get("confidenceTier"),
        "promotion_status": row.get("promotionStatus"),
        "score": row.get("score"),
        "win_probability": row.get("winProbability"),
        "expected_value": row.get("expectedValue"),
        "edge_vs_book": row.get("edgeVsBook"),
        "american_odds": row.get("americanOdds"),
        "book_key": row.get("bookKey"),
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
    predictions.sort(key=lambda r: (str(r.get("promotionStatus") == "PROMOTED"), float(r.get("expectedValue") or -1), float(r.get("score") or 0)), reverse=True)
    for idx, row in enumerate(predictions, 1):
        row["rank"] = idx
    return {
        "ok": True,
        "sport": "mlb",
        "slate_date": slate,
        "engine": ENGINE,
        "modelVersion": MODEL_VERSION,
        "primaryBook": PRIMARY_BOOK,
        "promotionPolicy": {
            "edgeThreshold": PROMOTION_EDGE_THRESHOLD,
            "minEV": MIN_PROMOTION_EV,
            "minPulls": MIN_PROMOTION_PULLS,
            "maxPromotedDogPrice": MAX_PROMOTED_DOG_PRICE,
        },
        "pullCount": len(pulls),
        "latestPullAt": latest_pull.get("pulled_at"),
        "latestPullId": latest_pull.get("pull_id"),
        "gameCount": len(latest_games),
        "count": len(predictions),
        "promotedCount": len([x for x in predictions if x.get("promotionStatus") == "PROMOTED"]),
        "allGamesPredicted": len(predictions) == len(latest_games),
        "stored": store,
        "storedCount": len([x for x in stored if x and x.get("ok")]),
        "predictions": predictions,
    }
