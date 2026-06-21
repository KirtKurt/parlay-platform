import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import product
from typing import Any, Dict, Iterable, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Key


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


ODDS_API_KEY = _env("ODDS_API_KEY")
ODDS_REGIONS = _env("ODDS_REGIONS", "us")
ODDS_MARKETS = _env("ODDS_MARKETS", "h2h,spreads,totals")
ODDS_FORMAT = _env("ODDS_FORMAT", "american")
INQSI_SPORTS = [s.strip() for s in _env("INQSI_SPORTS", "").split(",") if s.strip()]

SNAPSHOTS_TABLE = _env("SNAPSHOTS_TABLE")
SIGNAL_LEDGER_TABLE = _env("SIGNAL_LEDGER_TABLE")
PREDICTIONS_TABLE = _env("PREDICTIONS_TABLE")

_dynamodb = boto3.resource("dynamodb")
_snapshots = _dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
_signal_ledger = _dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
_predictions = _dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None


class InqsiError(Exception):
    pass


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def _decimal(value: Any) -> Decimal:
    return Decimal(str(round(float(value), 6)))


def _http_get_json(url: str, timeout: int = 25) -> Any:
    request = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _odds_api_url(path: str, params: Dict[str, Any]) -> str:
    if not ODDS_API_KEY:
        raise InqsiError("ODDS_API_KEY missing")
    merged = {"apiKey": ODDS_API_KEY, **params}
    return "https://api.the-odds-api.com/v4" + path + "?" + urllib.parse.urlencode(merged)


def discover_sports() -> List[Dict[str, Any]]:
    data = _http_get_json(_odds_api_url("/sports/", {}))
    if not isinstance(data, list):
        return []
    return [s for s in data if s.get("key")]


def active_sport_keys() -> List[str]:
    if INQSI_SPORTS:
        return INQSI_SPORTS
    sports = discover_sports()
    return [s["key"] for s in sports if s.get("active", True) and not s.get("has_outrights", False)]


def pull_odds_for_sport(sport_key: str) -> List[Dict[str, Any]]:
    url = _odds_api_url(
        f"/sports/{sport_key}/odds/",
        {
            "regions": ODDS_REGIONS,
            "markets": ODDS_MARKETS,
            "oddsFormat": ODDS_FORMAT,
            "dateFormat": "iso",
        },
    )
    data = _http_get_json(url)
    if not isinstance(data, list):
        return []
    return data


def _market(book: Dict[str, Any], market_key: str) -> Optional[Dict[str, Any]]:
    return next((m for m in book.get("markets", []) or [] if m.get("key") == market_key), None)


def _extract_h2h(book: Dict[str, Any], home: str, away: str) -> Optional[Dict[str, Any]]:
    market = _market(book, "h2h")
    if not market:
        return None
    out: Dict[str, Any] = {}
    for outcome in market.get("outcomes", []) or []:
        name = outcome.get("name")
        if name == home:
            out["home"] = outcome.get("price")
        elif name == away:
            out["away"] = outcome.get("price")
    return out if out.get("home") is not None and out.get("away") is not None else None


def _extract_spread(book: Dict[str, Any], home: str, away: str) -> Optional[Dict[str, Any]]:
    market = _market(book, "spreads")
    if not market:
        return None
    out: Dict[str, Any] = {}
    for outcome in market.get("outcomes", []) or []:
        name = outcome.get("name")
        if name == home:
            out["home_point"] = outcome.get("point")
            out["home_price"] = outcome.get("price")
        elif name == away:
            out["away_point"] = outcome.get("point")
            out["away_price"] = outcome.get("price")
    required = ["home_point", "home_price", "away_point", "away_price"]
    return out if all(out.get(k) is not None for k in required) else None


def _extract_total(book: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    market = _market(book, "totals")
    if not market:
        return None
    out: Dict[str, Any] = {}
    for outcome in market.get("outcomes", []) or []:
        name = (outcome.get("name") or "").lower()
        if name == "over":
            out["over_point"] = outcome.get("point")
            out["over_price"] = outcome.get("price")
        elif name == "under":
            out["under_point"] = outcome.get("point")
            out["under_price"] = outcome.get("price")
    required = ["over_point", "over_price", "under_point", "under_price"]
    return out if all(out.get(k) is not None for k in required) else None


def compact_game(raw: Dict[str, Any], sport_key: str) -> Optional[Dict[str, Any]]:
    home = raw.get("home_team")
    away = raw.get("away_team")
    if not home or not away:
        return None
    game_id = raw.get("id") or f"{sport_key}:{away}:{home}:{raw.get('commence_time')}"
    books: Dict[str, Any] = {}
    for book in raw.get("bookmakers", []) or []:
        book_key = (book.get("key") or "").lower().strip()
        if not book_key:
            continue
        payload: Dict[str, Any] = {"title": book.get("title"), "last_update": book.get("last_update")}
        h2h = _extract_h2h(book, home, away)
        spread = _extract_spread(book, home, away)
        total = _extract_total(book)
        if h2h:
            payload["moneyline"] = h2h
        if spread:
            payload["spread"] = spread
        if total:
            payload["total"] = total
        if any(k in payload for k in ("moneyline", "spread", "total")):
            books[book_key] = payload
    return {
        "game_id": game_id,
        "sport_key": sport_key,
        "commence_time": raw.get("commence_time"),
        "home_team": home,
        "away_team": away,
        "books": books,
        "available_books": sorted(books.keys()),
    }


def compact_sport(raw_games: List[Dict[str, Any]], sport_key: str) -> Dict[str, Any]:
    games = [g for g in (compact_game(raw, sport_key) for raw in raw_games) if g]
    book_keys = sorted({b for g in games for b in g.get("books", {}).keys()})
    return {
        "sport_key": sport_key,
        "pulled_at": now_iso(),
        "pull_interval_minutes": 15,
        "markets": ["moneyline", "spread", "total"],
        "available_book_keys": book_keys,
        "games": games,
        "game_count": len(games),
    }


def store_snapshot(sport_key: str, compact: Dict[str, Any]) -> Dict[str, Any]:
    if _snapshots is None:
        raise InqsiError("SNAPSHOTS_TABLE not configured")
    asof = compact.get("pulled_at") or now_iso()
    item = {
        "PK": f"INQSI#SPORT#{sport_key}",
        "SK": f"SNAPSHOT#{asof}",
        "entity_type": "INQSI_SNAPSHOT",
        "sport_key": sport_key,
        "asof": asof,
        "pull_interval_minutes": 15,
        "data": compact,
        "meta": {"source": "the-odds-api", "markets": ODDS_MARKETS.split(","), "regions": ODDS_REGIONS},
    }
    _snapshots.put_item(Item=item)
    return item


def _store_signal_state(game_state: Dict[str, Any]) -> None:
    if _signal_ledger is None:
        return
    asof = game_state["asof"]
    sport_key = game_state["sport_key"]
    game_id = game_state["game_id"]
    item = {
        "PK": f"INQSI#STATE#{sport_key}",
        "SK": f"GAME#{game_id}#ASOF#{asof}",
        "entity_type": "INQSI_GAME_STATE",
        **_to_ddb(game_state),
    }
    _signal_ledger.put_item(Item=item)
    latest = {
        "PK": f"INQSI#LATEST#{sport_key}",
        "SK": f"GAME#{game_id}",
        "entity_type": "INQSI_LATEST_GAME_STATE",
        **_to_ddb(game_state),
    }
    _signal_ledger.put_item(Item=latest)


def _to_ddb(value: Any) -> Any:
    if isinstance(value, float):
        return _decimal(value)
    if isinstance(value, dict):
        return {k: _to_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_ddb(v) for v in value]
    return value


def _from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_ddb(v) for v in value]
    return value


def pull_and_analyze_sport(sport_key: str) -> Dict[str, Any]:
    raw = pull_odds_for_sport(sport_key)
    compact = compact_sport(raw, sport_key)
    stored = store_snapshot(sport_key, compact)
    states = analyze_sport(sport_key, limit=96, store=True)
    return {
        "ok": True,
        "sport_key": sport_key,
        "pulled_at": compact["pulled_at"],
        "games_pulled": compact["game_count"],
        "available_book_keys": compact["available_book_keys"],
        "stored": {"PK": stored["PK"], "SK": stored["SK"]},
        "games_analyzed": len(states.get("games", [])),
    }


def pull_and_analyze_all() -> Dict[str, Any]:
    results = []
    for sport_key in active_sport_keys():
        try:
            results.append(pull_and_analyze_sport(sport_key))
        except Exception as exc:
            results.append({"ok": False, "sport_key": sport_key, "error": str(exc)})
    return {"ok": all(r.get("ok") for r in results), "count": len(results), "results": results}


def recent_snapshots(sport_key: str, limit: int = 96) -> List[Dict[str, Any]]:
    if _snapshots is None:
        raise InqsiError("SNAPSHOTS_TABLE not configured")
    response = _snapshots.query(
        KeyConditionExpression=Key("PK").eq(f"INQSI#SPORT#{sport_key}") & Key("SK").begins_with("SNAPSHOT#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    items = response.get("Items", [])
    return sorted([_from_ddb(item) for item in items], key=lambda x: x.get("asof", ""))


def latest_snapshot(sport_key: str) -> Optional[Dict[str, Any]]:
    snaps = recent_snapshots(sport_key, limit=1)
    return snaps[-1] if snaps else None


def _game_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {g["game_id"]: g for g in snapshot.get("data", {}).get("games", []) if g.get("game_id")}


def american_to_prob(price: Any) -> Optional[float]:
    try:
        p = int(price)
        if p == 0:
            return None
        return abs(p) / (abs(p) + 100.0) if p < 0 else 100.0 / (p + 100.0)
    except Exception:
        return None


def no_vig(home_price: Any, away_price: Any) -> Optional[Tuple[float, float]]:
    hp = american_to_prob(home_price)
    ap = american_to_prob(away_price)
    if hp is None or ap is None or hp + ap <= 0:
        return None
    return hp / (hp + ap), ap / (hp + ap)


def avg(values: Iterable[float]) -> Optional[float]:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def market_point(game: Dict[str, Any]) -> Dict[str, Any]:
    home_probs: List[float] = []
    away_probs: List[float] = []
    home_spreads: List[float] = []
    totals: List[float] = []
    books_used = []
    for book_key, book in (game.get("books") or {}).items():
        ml = book.get("moneyline") or {}
        probs = no_vig(ml.get("home"), ml.get("away")) if ml else None
        if probs:
            home_probs.append(probs[0])
            away_probs.append(probs[1])
            books_used.append(book_key)
        spread = book.get("spread") or {}
        if spread.get("home_point") is not None:
            home_spreads.append(float(spread["home_point"]))
        total = book.get("total") or {}
        if total.get("over_point") is not None:
            totals.append(float(total["over_point"]))
    home_p = avg(home_probs)
    away_p = avg(away_probs)
    return {
        "asof": None,
        "home_prob": home_p,
        "away_prob": away_p,
        "home_spread": avg(home_spreads),
        "total": avg(totals),
        "book_count": len(set(books_used)),
        "available_book_count": len(game.get("books") or {}),
    }


def series_for_game(snapshots: List[Dict[str, Any]], game_id: str) -> List[Dict[str, Any]]:
    series = []
    for snap in snapshots:
        game = _game_index(snap).get(game_id)
        if not game:
            continue
        point = market_point(game)
        point.update({
            "asof": snap.get("asof"),
            "game_id": game_id,
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "commence_time": game.get("commence_time"),
            "available_books": game.get("available_books", []),
        })
        series.append(point)
    return series


def _delta(latest: Optional[float], prior: Optional[float]) -> float:
    if latest is None or prior is None:
        return 0.0
    return float(latest) - float(prior)


def _direction_team(latest: Dict[str, Any], first: Dict[str, Any]) -> Optional[str]:
    hd = _delta(latest.get("home_prob"), first.get("home_prob"))
    ad = _delta(latest.get("away_prob"), first.get("away_prob"))
    if abs(hd) < 0.003 and abs(ad) < 0.003:
        return None
    return "home" if hd >= ad else "away"


def _status_label(score: int, chaos: bool, reversal: bool) -> str:
    if chaos:
        return "Chaos Alert"
    if reversal:
        return "Reversal Watch"
    if score >= 80:
        return "Steam Detected"
    if score >= 65:
        return "Movement Detected"
    if score >= 50:
        return "Watch"
    return "Market Stable"


def classify_series(sport_key: str, game_id: str, series: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not series:
        return None
    latest = series[-1]
    first = series[0]
    prior = series[-2] if len(series) >= 2 else first
    home_team = latest.get("home_team")
    away_team = latest.get("away_team")

    ml_full_delta_home = _delta(latest.get("home_prob"), first.get("home_prob"))
    ml_recent_delta_home = _delta(latest.get("home_prob"), prior.get("home_prob"))
    spread_full_delta = _delta(latest.get("home_spread"), first.get("home_spread"))
    spread_recent_delta = _delta(latest.get("home_spread"), prior.get("home_spread"))
    total_full_delta = _delta(latest.get("total"), first.get("total"))
    total_recent_delta = _delta(latest.get("total"), prior.get("total"))

    direction_side = _direction_team(latest, first)
    direction_team = home_team if direction_side == "home" else away_team if direction_side == "away" else None

    ml_strength = min(35, int(abs(ml_full_delta_home) * 700))
    recent_strength = min(20, int(abs(ml_recent_delta_home) * 1500))
    spread_strength = min(15, int(abs(spread_full_delta) * 5))
    total_conflict = bool(direction_side and total_full_delta < -1.0 and abs(spread_full_delta) >= 0.5)
    book_bonus = min(15, int((latest.get("book_count") or 0) * 3))
    base = 45 + ml_strength + recent_strength + spread_strength + book_bonus

    reversal = len(series) >= 3 and (ml_full_delta_home * ml_recent_delta_home < -0.0005)
    chaos = reversal and abs(total_recent_delta) >= 1.0 or (latest.get("book_count") or 0) <= 1 and len(series) >= 2
    if total_conflict:
        base -= 8
    if reversal:
        base -= 10
    if chaos:
        base -= 15
    score = max(0, min(100, base))

    if chaos:
        stability = "Chaos"
        primary = "Chaos"
    elif reversal:
        stability = "Unstable"
        primary = "Reversal"
    elif score >= 80:
        stability = "Stable"
        primary = "Steam"
    elif total_conflict:
        stability = "Watch"
        primary = "Resistance"
    elif abs(ml_recent_delta_home) > 0.006:
        stability = "Watch"
        primary = "Momentum"
    else:
        stability = "Stable" if score >= 50 else "Watch"
        primary = "Market Agreement" if score >= 60 else "Compression"

    side_score = {
        "home": max(0, min(100, score + (10 if direction_side == "home" else -8 if direction_side == "away" else 0))),
        "away": max(0, min(100, score + (10 if direction_side == "away" else -8 if direction_side == "home" else 0))),
    }
    anchor_class = "strong_anchor" if max(side_score.values()) >= 80 and stability in {"Stable", "Watch"} else "moderate_risk" if max(side_score.values()) >= 60 else "high_risk"
    explanation_bits = []
    if direction_team:
        explanation_bits.append(f"Market movement is leaning toward {direction_team}.")
    else:
        explanation_bits.append("The market has not shown a clear side lean yet.")
    if total_full_delta < -0.75:
        explanation_bits.append("The total has moved lower.")
    elif total_full_delta > 0.75:
        explanation_bits.append("The total has moved higher.")
    if total_conflict:
        explanation_bits.append("Side movement and total movement are not fully aligned.")
    if reversal:
        explanation_bits.append("Recent pulls show a reversal risk.")
    if chaos:
        explanation_bits.append("Book coverage or movement is unstable.")

    return {
        "sport_key": sport_key,
        "game_id": game_id,
        "asof": latest.get("asof"),
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": latest.get("commence_time"),
        "pull_interval_minutes": 15,
        "status_label": _status_label(score, chaos, reversal),
        "primary_signal": primary,
        "signal_score": score,
        "stability_classification": stability,
        "anchor_classification": anchor_class,
        "market_direction": {"side": direction_side, "team": direction_team},
        "side_scores": side_score,
        "movement": {
            "moneyline": {"home_probability_delta_full": round(ml_full_delta_home, 5), "home_probability_delta_recent": round(ml_recent_delta_home, 5)},
            "spread": {"home_spread_delta_full": round(spread_full_delta, 3), "home_spread_delta_recent": round(spread_recent_delta, 3)},
            "total": {"total_delta_full": round(total_full_delta, 3), "total_delta_recent": round(total_recent_delta, 3)},
        },
        "indicators": {
            "steam": score >= 80,
            "resistance": total_conflict or (50 <= score < 65),
            "reversal": reversal,
            "chaos": chaos,
        },
        "market_direction_summary": " ".join(explanation_bits[:3]),
        "what_looks_wrong": " ".join(explanation_bits[2:]) if len(explanation_bits) > 2 else "No major conflict detected; continue reviewing line movement before locking it in.",
        "graph_available": {"15m": True, "1h": True, "3h": True, "full": True},
        "latest_point": latest,
    }


def analyze_sport(sport_key: str, limit: int = 96, store: bool = False) -> Dict[str, Any]:
    snaps = recent_snapshots(sport_key, limit=limit)
    if not snaps:
        return {"ok": True, "sport_key": sport_key, "games": [], "message": "No snapshots available yet."}
    latest_games = _game_index(snaps[-1])
    states = []
    for game_id in latest_games:
        state = classify_series(sport_key, game_id, series_for_game(snaps, game_id))
        if state:
            states.append(state)
            if store:
                _store_signal_state(state)
    states.sort(key=lambda x: x.get("signal_score", 0), reverse=True)
    return {"ok": True, "sport_key": sport_key, "asof": snaps[-1].get("asof"), "games": states, "count": len(states)}


def latest_game_states(sport_key: str, limit: int = 200) -> List[Dict[str, Any]]:
    if _signal_ledger is None:
        return analyze_sport(sport_key, limit=96, store=False).get("games", [])
    response = _signal_ledger.query(
        KeyConditionExpression=Key("PK").eq(f"INQSI#LATEST#{sport_key}"),
        Limit=limit,
    )
    items = [_from_ddb(i) for i in response.get("Items", [])]
    if not items:
        return analyze_sport(sport_key, limit=96, store=False).get("games", [])
    items.sort(key=lambda x: x.get("signal_score", 0), reverse=True)
    return items


def game_detail(sport_key: str, game_id: str) -> Dict[str, Any]:
    snaps = recent_snapshots(sport_key, limit=96)
    series = series_for_game(snaps, game_id)
    if not series:
        raise InqsiError("Game not found in recent snapshots")
    state = classify_series(sport_key, game_id, series)
    return {"ok": True, "game": state, "graphs": graph_data(sport_key, game_id, "full")}


def graph_data(sport_key: str, game_id: str, window: str = "full") -> Dict[str, Any]:
    snaps = recent_snapshots(sport_key, limit=192)
    cutoff = None
    if window == "1h":
        cutoff = now_utc() - timedelta(hours=1)
    elif window == "3h":
        cutoff = now_utc() - timedelta(hours=3)
    elif window == "15m":
        cutoff = now_utc() - timedelta(minutes=15)
    series = series_for_game(snaps, game_id)
    if cutoff:
        filtered = []
        for p in series:
            try:
                dt = datetime.fromisoformat((p.get("asof") or "").replace("Z", "+00:00"))
                if dt >= cutoff:
                    filtered.append(p)
            except Exception:
                pass
        series = filtered or series[-2:]
    return {
        "ok": True,
        "sport_key": sport_key,
        "game_id": game_id,
        "window": window,
        "points": series,
        "lines": {
            "moneyline_home_probability": [{"x": p.get("asof"), "y": p.get("home_prob")} for p in series],
            "moneyline_away_probability": [{"x": p.get("asof"), "y": p.get("away_prob")} for p in series],
            "home_spread": [{"x": p.get("asof"), "y": p.get("home_spread")} for p in series],
            "total": [{"x": p.get("asof"), "y": p.get("total")} for p in series],
        },
    }


def _side_name(game: Dict[str, Any], side: str) -> str:
    return game.get("home_team") if side == "home" else game.get("away_team")


def candidate_side(game: Dict[str, Any]) -> Dict[str, Any]:
    direction = (game.get("market_direction") or {}).get("side")
    if direction in {"home", "away"}:
        side = direction
    else:
        scores = game.get("side_scores", {})
        side = "home" if scores.get("home", 0) >= scores.get("away", 0) else "away"
    return {"game_id": game["game_id"], "side": side, "team": _side_name(game, side), "score": game.get("side_scores", {}).get(side, game.get("signal_score", 0))}


def rank_combinations(games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(games) != 3:
        raise InqsiError("Exactly three games are required to rank eight combinations")
    ranked = []
    for sides in product(["home", "away"], repeat=3):
        legs = []
        score = 0.0
        risk_penalty = 0.0
        for game, side in zip(games, sides):
            side_score = float((game.get("side_scores") or {}).get(side, game.get("signal_score", 50)))
            if game.get("stability_classification") == "Chaos":
                risk_penalty += 15
            elif game.get("stability_classification") == "Unstable":
                risk_penalty += 8
            score += side_score
            legs.append({"game_id": game.get("game_id"), "team": _side_name(game, side), "side": side, "side_score": round(side_score, 2), "primary_signal": game.get("primary_signal"), "stability": game.get("stability_classification")})
        final_score = max(0, min(100, (score / 3.0) - risk_penalty))
        ranked.append({"legs": legs, "signal_score": round(final_score, 2), "risk_score": round(100 - final_score, 2)})
    ranked.sort(key=lambda x: x["signal_score"], reverse=True)
    for index, combo in enumerate(ranked, start=1):
        combo["rank"] = index
        combo["summary"] = " / ".join(leg["team"] for leg in combo["legs"])
    return ranked


def auto_parlay(sport_key: Optional[str] = None) -> Dict[str, Any]:
    sports = [sport_key] if sport_key else active_sport_keys()
    all_games: List[Dict[str, Any]] = []
    for sk in sports:
        all_games.extend(latest_game_states(sk))
    anchors = [g for g in all_games if g.get("anchor_classification") == "strong_anchor" and g.get("stability_classification") != "Chaos"]
    moderate = [g for g in all_games if g.get("anchor_classification") == "moderate_risk" and g.get("stability_classification") in {"Stable", "Watch", "Unstable"}]
    anchors.sort(key=lambda x: x.get("signal_score", 0), reverse=True)
    moderate.sort(key=lambda x: x.get("signal_score", 0), reverse=True)
    if len(anchors) < 2 or len(moderate) < 1:
        return {"ok": True, "built": False, "refusal": {"code": "INSUFFICIENT_STRUCTURE", "reason": "Need at least two strong anchors and one moderate-risk leg."}, "anchor_count": len(anchors), "moderate_count": len(moderate)}
    selected = anchors[:2] + [moderate[0]]
    return {"ok": True, "built": True, "structure": "2_STRONG_ANCHORS_1_MODERATE_RISK", "selected_legs": [candidate_side(g) for g in selected], "games": selected, "ranked_combinations": rank_combinations(selected)}


def user_parlay(sport_key: str, game_ids: List[str]) -> Dict[str, Any]:
    if len(game_ids) != 3:
        raise InqsiError("Select exactly three games")
    states = {g["game_id"]: g for g in latest_game_states(sport_key)}
    missing = [gid for gid in game_ids if gid not in states]
    if missing:
        raise InqsiError(f"Selected games not found: {', '.join(missing)}")
    selected = [states[gid] for gid in game_ids]
    anchors = len([g for g in selected if g.get("anchor_classification") == "strong_anchor"])
    moderates = len([g for g in selected if g.get("anchor_classification") == "moderate_risk"])
    return {
        "ok": True,
        "structure_check": {"strong_anchor_count": anchors, "moderate_risk_count": moderates, "meets_target_structure": anchors >= 2 and moderates >= 1},
        "strongest_leg": max(selected, key=lambda g: g.get("signal_score", 0)),
        "weakest_leg": min(selected, key=lambda g: g.get("signal_score", 0)),
        "games": selected,
        "ranked_combinations": rank_combinations(selected),
    }
