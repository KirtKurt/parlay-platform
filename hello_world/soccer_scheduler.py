import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from soccer_audit import record_soccer_no_edge_prediction_rows, record_soccer_snapshot_audit, soccer_three_way_probs
from soccer_league_segments import LEAGUE_PROFILE_VERSION, get_soccer_league_profile


dynamodb = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None

# Controlled starter-plan soccer list using only keys returned by live /v4/sports discovery.
# This expands beyond EPL/MLS because those returned 0 games, while keeping outrights excluded.
DEFAULT_SOCCER_KEYS = [
    "soccer_brazil_campeonato",
    "soccer_brazil_serie_b",
    "soccer_chile_campeonato",
    "soccer_china_superleague",
    "soccer_conmebol_copa_libertadores",
    "soccer_conmebol_copa_sudamericana",
    "soccer_finland_veikkausliiga",
    "soccer_japan_j_league",
    "soccer_league_of_ireland",
    "soccer_norway_eliteserien",
    "soccer_spain_segunda_division",
    "soccer_sweden_allsvenskan",
    "soccer_sweden_superettan",
]
SOCCER_KEYS = [s.strip() for s in os.environ.get("SOCCER_KEYS", ",".join(DEFAULT_SOCCER_KEYS)).split(",") if s.strip()]
ODDS_MARKETS = "h2h,spreads,totals"
ML_FEATURE_VERSION = "soccer_hot_pull_movement_features_v1"
SOCCER_OUTCOMES = ("home", "draw", "away")


def ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: ddb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [ddb_safe(v) for v in value]
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _game_date_et(commence_time: Optional[str]) -> Optional[str]:
    if not commence_time:
        return None
    try:
        dt = datetime.fromisoformat(str(commence_time).replace("Z", "+00:00"))
    except Exception:
        return None
    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _odds_url(sport_key: str) -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": ODDS_MARKETS,
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    return f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?" + urllib.parse.urlencode(params)


def _compact_soccer_game(raw_game: Dict[str, Any], sport_key: str) -> Dict[str, Any]:
    home = raw_game.get("home_team")
    away = raw_game.get("away_team")
    game_id = raw_game.get("id") or f"{sport_key}|{away}|{home}|{raw_game.get('commence_time')}"
    books: Dict[str, Any] = {}
    league_profile = get_soccer_league_profile(sport_key)
    game_date = _game_date_et(raw_game.get("commence_time"))

    for bookmaker in raw_game.get("bookmakers", []) or []:
        book_key = (bookmaker.get("key") or "").lower().strip()
        if not book_key:
            continue
        markets: Dict[str, Any] = {}
        for market in bookmaker.get("markets", []) or []:
            market_key = market.get("key")
            outcomes = []
            for outcome in market.get("outcomes", []) or []:
                row = {"name": outcome.get("name"), "price": outcome.get("price")}
                if "point" in outcome:
                    row["point"] = outcome.get("point")
                outcomes.append(row)
            if outcomes:
                markets[market_key] = outcomes
        if markets:
            books[book_key] = markets

    return {
        "id": game_id,
        "game_key": f"soccer|{sport_key}|{away}|{home}|{raw_game.get('commence_time')}",
        "sport": "soccer",
        "sport_key": sport_key,
        "game_date_et": game_date,
        "league_segment": league_profile["league_segment"],
        "league_name": league_profile["league_name"],
        "league_profile_version": LEAGUE_PROFILE_VERSION,
        "league_profile": league_profile,
        "market_type": "3-way home/draw/away",
        "commence_time": raw_game.get("commence_time"),
        "home_team": home,
        "away_team": away,
        "books": books,
        "markets_stored": ["h2h", "spreads", "totals"],
        "model_note": "Soccer is 3-way for h2h: home/draw/away. League segmented. Keep isolated from 2-way sport models.",
    }


def _game_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {g.get("game_key") or g.get("id"): g for g in snapshot.get("data", {}).get("games", []) or [] if g.get("game_key") or g.get("id")}


def _has_games(snapshot: Dict[str, Any]) -> bool:
    games = snapshot.get("data", {}).get("games", []) or []
    return bool(games)


def _latest_two_hot_snapshots(limit: int = 40) -> List[Dict[str, Any]]:
    if snapshots_tbl is None:
        return []
    resp = snapshots_tbl.query(
        KeyConditionExpression=Key("PK").eq("SPORT#soccer") & Key("SK").begins_with("HOT#DATE#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    rows = sorted([row for row in resp.get("Items", []) if _has_games(row)], key=lambda x: x.get("asof") or "")
    return rows[-2:]


def _book_probs(game: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    consensus = soccer_three_way_probs(game)
    return consensus.get("book_probs") or {}


def _book_agreement(prev: Dict[str, Any], latest: Dict[str, Any], outcome: str) -> Dict[str, Any]:
    prev_probs = _book_probs(prev)
    latest_probs = _book_probs(latest)
    common = sorted(set(prev_probs.keys()) & set(latest_probs.keys()))
    agreeing, disagreeing = [], []
    for book in common:
        delta = latest_probs[book].get(outcome, 0) - prev_probs[book].get(outcome, 0)
        if delta > 0:
            agreeing.append({"book": book, "delta": round(delta, 6)})
        elif delta < 0:
            disagreeing.append({"book": book, "delta": round(delta, 6)})
    return {"common_books": len(common), "agreeing_books": len(agreeing), "disagreeing_books": len(disagreeing), "agreeing": agreeing, "disagreeing": disagreeing}


def _outcome_label(outcome: Optional[str], game: Dict[str, Any]) -> Optional[str]:
    if outcome == "home":
        return game.get("home_team")
    if outcome == "away":
        return game.get("away_team")
    if outcome == "draw":
        return "Draw"
    return None


def _movement_strength(delta: float, agreeing_books: int, disagreeing_books: int) -> str:
    abs_delta = abs(float(delta or 0))
    if abs_delta >= 0.018 and agreeing_books >= 2 and disagreeing_books == 0:
        return "HIGH"
    if abs_delta >= 0.006 and agreeing_books >= 2:
        return "MEDIUM"
    if abs_delta > 0:
        return "LOW"
    return "FLAT"


def _avg_market_point(game: Dict[str, Any], market_key: str) -> Optional[float]:
    points: List[float] = []
    for markets in (game.get("books") or {}).values():
        market = markets.get(market_key) or []
        if isinstance(market, list):
            for row in market:
                if row.get("point") is not None:
                    points.append(float(row["point"]))
    if not points:
        return None
    return sum(points) / len(points)


def _point_delta(prev: Dict[str, Any], latest: Dict[str, Any], market_key: str) -> Dict[str, Any]:
    prev_point = _avg_market_point(prev, market_key)
    latest_point = _avg_market_point(latest, market_key)
    if prev_point is None or latest_point is None:
        return {"direction": "unavailable", "previous_point": None, "latest_point": None, "delta": None}
    delta = round(latest_point - prev_point, 4)
    direction = "flat" if abs(delta) < 0.001 else "up" if delta > 0 else "down"
    return {"direction": direction, "previous_point": round(prev_point, 4), "latest_point": round(latest_point, 4), "delta": delta}


def _store_soccer_hot_movement_features(run: str) -> Dict[str, Any]:
    """Persist one HOT-to-HOT soccer movement feature row per match for ML research.

    The rows are partitioned by actual ET game date, while each game_key includes league,
    teams, and commence_time. This avoids cross-date and cross-league bleed as soccer expands.
    """
    if signal_ledger_tbl is None:
        return {"ok": False, "stored": 0, "error": "SIGNAL_LEDGER_TABLE not configured"}
    snaps = _latest_two_hot_snapshots()
    if len(snaps) < 2:
        return {"ok": True, "stored": 0, "reason": "Need at least two populated HOT soccer snapshots."}
    prev_snap, latest_snap = snaps[-2], snaps[-1]
    prev_games = _game_index(prev_snap)
    latest_games = _game_index(latest_snap)
    stored = 0
    errors: List[str] = []
    sample: List[Dict[str, Any]] = []
    summary_by_game_date: Dict[str, int] = {}

    for game_key, latest_game in latest_games.items():
        prev_game = prev_games.get(game_key)
        if not prev_game:
            continue
        prev_p = soccer_three_way_probs(prev_game)
        latest_p = soccer_three_way_probs(latest_game)
        if prev_p.get("home") is None or latest_p.get("home") is None:
            continue
        deltas = {side: round((latest_p.get(side) or 0) - (prev_p.get(side) or 0), 6) for side in SOCCER_OUTCOMES}
        hot_outcome = max(deltas, key=lambda side: deltas[side])
        hot_delta = float(deltas[hot_outcome])
        current_leader = latest_p.get("leader")
        leader_gap = latest_p.get("leader_gap")
        game_date = latest_game.get("game_date_et") or _game_date_et(latest_game.get("commence_time")) or _slate_date_et()
        agreement = _book_agreement(prev_game, latest_game, hot_outcome)
        strength = _movement_strength(hot_delta, int(agreement.get("agreeing_books") or 0), int(agreement.get("disagreeing_books") or 0))
        signal_tags: List[str] = []
        if hot_outcome == "draw" and hot_delta > 0:
            signal_tags.append("draw_pressure")
        if hot_outcome != current_leader and hot_delta > 0:
            signal_tags.append("non_favorite_pressure")
        if hot_outcome == current_leader and hot_delta > 0:
            signal_tags.append("leader_pressure")
        if agreement.get("agreeing_books", 0) >= 2:
            signal_tags.append("cross_book_confirmation")
        if leader_gap is not None and float(leader_gap) < 0.05:
            signal_tags.append("three_way_market_compression")
        if strength != "FLAT":
            signal_tags.append(f"hot_move_{strength.lower()}")

        # ML candidate is the side receiving pressure when there is movement; otherwise current market leader.
        feature_candidate_outcome = hot_outcome if hot_delta > 0 else current_leader
        feature = {
            "PK": f"ML_FEATURE#soccer#{game_date}",
            "SK": f"HOT_DELTA#{latest_snap.get('asof')}#LEAGUE#{latest_game.get('league_segment')}#GAME#{game_key}",
            "entity_type": "SOCCER_HOT_PULL_MOVEMENT_FEATURE",
            "sport": "soccer",
            "game_date_et": game_date,
            "game_key": game_key,
            "game_id": latest_game.get("id"),
            "sport_key": latest_game.get("sport_key"),
            "league_segment": latest_game.get("league_segment"),
            "league_name": latest_game.get("league_name"),
            "league_profile_version": LEAGUE_PROFILE_VERSION,
            "feature_version": ML_FEATURE_VERSION,
            "created_at": _now_iso(),
            "run": run,
            "date_isolated": True,
            "league_isolated": True,
            "hot_only": True,
            "market_type": "3-way home/draw/away",
            "home_team": latest_game.get("home_team"),
            "away_team": latest_game.get("away_team"),
            "commence_time": latest_game.get("commence_time"),
            "previous_asof": prev_snap.get("asof"),
            "latest_asof": latest_snap.get("asof"),
            "home_delta": deltas["home"],
            "draw_delta": deltas["draw"],
            "away_delta": deltas["away"],
            "hot_outcome": hot_outcome,
            "hot_selection": _outcome_label(hot_outcome, latest_game),
            "hot_delta": hot_delta,
            "movement_strength": strength,
            "current_leader": current_leader,
            "current_leader_selection": _outcome_label(current_leader, latest_game),
            "leader_gap": leader_gap,
            "feature_candidate_outcome": feature_candidate_outcome,
            "feature_candidate_selection": _outcome_label(feature_candidate_outcome, latest_game),
            "book_agreement": agreement,
            "agreeing_books_count": agreement.get("agreeing_books", 0),
            "disagreeing_books_count": agreement.get("disagreeing_books", 0),
            "spread_signal": _point_delta(prev_game, latest_game, "spreads"),
            "total_signal": _point_delta(prev_game, latest_game, "totals"),
            "latest_consensus_three_way": {k: latest_p.get(k) for k in ["home", "draw", "away", "leader", "leader_gap", "books", "book_count"]},
            "previous_consensus_three_way": {k: prev_p.get(k) for k in ["home", "draw", "away", "leader", "leader_gap", "books", "book_count"]},
            "signal_tags": sorted(set(signal_tags)),
            "label_status": "PENDING_RESULT",
            "actual_outcome": None,
            "actual_selection": None,
            "feature_correct": None,
            "notes": [
                "HOT-to-HOT soccer movement feature row for ML research.",
                "Use only within same game_date_et and league/game_key context to avoid data bleed.",
                "Soccer h2h is 3-way: home/draw/away.",
            ],
        }
        try:
            signal_ledger_tbl.put_item(Item=ddb_safe(feature))
            stored += 1
            summary_by_game_date[game_date] = summary_by_game_date.get(game_date, 0) + 1
            if len(sample) < 10:
                sample.append({"game_key": game_key, "hot_selection": feature["hot_selection"], "hot_delta": round(hot_delta, 6), "candidate": feature["feature_candidate_selection"], "movement_strength": strength})
        except Exception as exc:
            errors.append(f"{game_key}: {exc}")

    for game_date, count in summary_by_game_date.items():
        try:
            signal_ledger_tbl.put_item(Item=ddb_safe({
                "PK": f"ML_FEATURE#soccer#{game_date}",
                "SK": f"HOT_DELTA_SUMMARY#{latest_snap.get('asof')}",
                "entity_type": "SOCCER_HOT_PULL_MOVEMENT_FEATURE_SUMMARY",
                "sport": "soccer",
                "game_date_et": game_date,
                "feature_version": ML_FEATURE_VERSION,
                "created_at": _now_iso(),
                "run": run,
                "date_isolated": True,
                "hot_only": True,
                "previous_asof": prev_snap.get("asof"),
                "latest_asof": latest_snap.get("asof"),
                "feature_rows_stored": count,
            }))
        except Exception as exc:
            errors.append(f"summary {game_date}: {exc}")

    return {"ok": len(errors) == 0, "stored": stored, "previous_asof": prev_snap.get("asof"), "latest_asof": latest_snap.get("asof"), "feature_version": ML_FEATURE_VERSION, "by_game_date": summary_by_game_date, "errors": errors, "sample": sample}


def pull_soccer_hot_snapshot() -> Dict[str, Any]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")

    asof = _now_iso()
    slate_date = _slate_date_et()
    games: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    raw_by_sport_key: Dict[str, List[Dict[str, Any]]] = {}

    for sport_key in SOCCER_KEYS:
        try:
            raw_games = _http_get_json(_odds_url(sport_key))
            raw_by_sport_key[sport_key] = raw_games or []
            for raw_game in raw_games or []:
                games.append(_compact_soccer_game(raw_game, sport_key))
        except Exception as exc:
            errors.append({"sport_key": sport_key, "error": str(exc)})

    league_segments = sorted({game.get("league_segment") for game in games if game.get("league_segment")})
    game_dates_et = sorted({game.get("game_date_et") for game in games if game.get("game_date_et")})
    compact_snapshot = {
        "games": games,
        "count": len(games),
        "soccer_keys": SOCCER_KEYS,
        "league_profile_version": LEAGUE_PROFILE_VERSION,
        "league_segments": league_segments,
        "game_dates_et": game_dates_et,
        "markets": ["h2h", "spreads", "totals"],
        "errors": errors,
    }
    item = {
        "PK": "SPORT#soccer",
        "SK": f"HOT#DATE#{slate_date}#ASOF#{asof}#SLATE#SOCCER_HOT",
        "sport": "soccer",
        "t": "HOT",
        "slate_id": f"SOCCER_{slate_date}_hot_pull",
        "slate_date_et": slate_date,
        "game_dates_et": game_dates_et,
        "asof": asof,
        "created_at": asof,
        "data": compact_snapshot,
        "meta": {
            "source": "theOddsAPI",
            "run_type": "hot_pull_audited",
            "pulled_at": asof,
            "temporary_mode": "until_friday_starter_plan_baseball_plus_soccer_only_expanded_exact_keys",
            "soccer_model": "SOC-B1.1-three-way-audit-v1",
            "league_profile_version": LEAGUE_PROFILE_VERSION,
            "segmentation": "league_segmented_soccer",
            "ml_research_policy": "hot_pull_movement_features_enabled",
        },
    }
    snapshots_tbl.put_item(Item=ddb_safe(item))
    audit_result = record_soccer_snapshot_audit(slate_date_et=slate_date, asof=asof, t="HOT", run_type="hot_pull_audited", compact_snapshot=compact_snapshot, raw_by_sport_key=raw_by_sport_key)
    prediction_audit = record_soccer_no_edge_prediction_rows(slate_date_et=slate_date, asof=asof, compact_snapshot=compact_snapshot)
    ml_features = _store_soccer_hot_movement_features(run="hot_pull_audited")
    return {
        "ok": len(errors) == 0 and audit_result.get("ok", False),
        "sport": "soccer",
        "t": "HOT",
        "count": len(games),
        "soccer_keys": SOCCER_KEYS,
        "league_profile_version": LEAGUE_PROFILE_VERSION,
        "league_segments": league_segments,
        "game_dates_et": game_dates_et,
        "errors": errors,
        "audit": audit_result,
        "prediction_audit": prediction_audit,
        "ml_research_policy": {
            "enabled": True,
            "scope": "HOT pulls only",
            "feature_partition_pk_pattern": "ML_FEATURE#soccer#YYYY-MM-DD",
            "rule": "Each soccer HOT-to-HOT home/draw/away movement comparison is stored as a feature row for winner-signal learning.",
        },
        "hot_movement_features": ml_features,
    }


def lambda_handler(event, context):
    try:
        return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(pull_soccer_hot_snapshot(), default=str)}
    except Exception as exc:
        return {"statusCode": 500, "headers": {"content-type": "application/json"}, "body": json.dumps({"ok": False, "sport": "soccer", "error": str(exc)})}
