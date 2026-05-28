from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from soccer_league_segments import LEAGUE_PROFILE_VERSION, SOCCER_LEAGUE_SEGMENTS, get_soccer_league_profile


dynamodb = boto3.resource("dynamodb")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")
OUTCOMES_TABLE = os.environ.get("OUTCOMES_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None
outcomes_tbl = dynamodb.Table(OUTCOMES_TABLE) if OUTCOMES_TABLE else None

FEATURE_VERSION = "soccer_full_capture_1_to_14_v2_result_audit"
MODEL_VERSION = "SOC-B1.1-three-way-audit-v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: ddb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [ddb_safe(v) for v in value]
    return value


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _minutes_until(asof: str, commence_time: Optional[str]) -> Optional[int]:
    asof_dt = _parse_dt(asof)
    start_dt = _parse_dt(commence_time)
    if not asof_dt or not start_dt:
        return None
    return int((start_dt - asof_dt).total_seconds() // 60)


def _time_bucket(minutes_until_start: Optional[int]) -> str:
    if minutes_until_start is None:
        return "UNKNOWN"
    if minutes_until_start > 360:
        return "360_PLUS_MINUTES"
    if minutes_until_start > 180:
        return "180_TO_360_MINUTES"
    if minutes_until_start > 90:
        return "90_TO_180_MINUTES"
    if minutes_until_start > 30:
        return "30_TO_90_MINUTES"
    if minutes_until_start >= 0:
        return "0_TO_30_MINUTES"
    return "AFTER_START"


def _payload_hash(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _american_to_prob(american: int) -> float:
    return abs(american) / (abs(american) + 100.0) if american < 0 else 100.0 / (american + 100.0)


def _canonical_outcome_name(outcome_name: Optional[str], home_team: Optional[str], away_team: Optional[str]) -> Optional[str]:
    name = (outcome_name or "").strip()
    if not name:
        return None
    if home_team and name == home_team:
        return "home"
    if away_team and name == away_team:
        return "away"
    if name.lower() in {"draw", "tie", "x"}:
        return "draw"
    return name.lower()


def soccer_three_way_probs(game: Dict[str, Any]) -> Dict[str, Any]:
    home_team = game.get("home_team")
    away_team = game.get("away_team")
    book_probs: Dict[str, Dict[str, float]] = {}
    for book_key, markets in (game.get("books") or {}).items():
        h2h = markets.get("h2h") or markets.get("ml") or []
        raw: Dict[str, float] = {}
        if isinstance(h2h, list):
            for outcome in h2h:
                side = _canonical_outcome_name(outcome.get("name"), home_team, away_team)
                if side in {"home", "draw", "away"} and outcome.get("price") is not None:
                    raw[side] = _american_to_prob(int(outcome["price"]))
        elif isinstance(h2h, dict):
            for side in ["home", "draw", "away"]:
                if h2h.get(side) is not None:
                    raw[side] = _american_to_prob(int(h2h[side]))
        total = sum(raw.values())
        if total > 0 and {"home", "draw", "away"}.issubset(raw.keys()):
            book_probs[book_key] = {side: raw[side] / total for side in ["home", "draw", "away"]}
    if not book_probs:
        return {"home": None, "draw": None, "away": None, "books": [], "leader": None, "leader_gap": None}
    consensus = {side: sum(book[side] for book in book_probs.values()) / len(book_probs) for side in ["home", "draw", "away"]}
    ordered = sorted(consensus.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "home": round(consensus["home"], 6),
        "draw": round(consensus["draw"], 6),
        "away": round(consensus["away"], 6),
        "books": sorted(book_probs.keys()),
        "book_count": len(book_probs),
        "leader": ordered[0][0],
        "leader_gap": round(ordered[0][1] - ordered[1][1], 6),
        "book_probs": {book: {k: round(v, 6) for k, v in vals.items()} for book, vals in book_probs.items()},
    }


def _market_availability(game: Dict[str, Any]) -> Dict[str, Any]:
    h2h_books, spread_books, total_books = [], [], []
    for book_key, markets in (game.get("books") or {}).items():
        if markets.get("h2h") or markets.get("ml"):
            h2h_books.append(book_key)
        if markets.get("spreads") or markets.get("spread"):
            spread_books.append(book_key)
        if markets.get("totals") or markets.get("total"):
            total_books.append(book_key)
    return {
        "book_count_total": len(game.get("books") or {}),
        "h2h_book_count": len(h2h_books),
        "spread_book_count": len(spread_books),
        "total_book_count": len(total_books),
        "h2h_books": sorted(h2h_books),
        "spread_books": sorted(spread_books),
        "total_books": sorted(total_books),
        "has_h2h": bool(h2h_books),
        "has_spread": bool(spread_books),
        "has_total": bool(total_books),
    }


def _league_profile(game: Dict[str, Any]) -> Dict[str, Any]:
    return game.get("league_profile") or get_soccer_league_profile(game.get("sport_key") or "")


def source_registry() -> Dict[str, Any]:
    return {
        "1_raw_odds_snapshots": {"status": "CONNECTED_HASHED", "source": "theOddsAPI soccer odds response"},
        "2_every_book": {"status": "CONNECTED", "source": "all returned bookmakers"},
        "3_opening_every_move_closing": {"status": "PARTIAL_CONNECTED", "source": "snapshot timeline"},
        "4_timestamp_quality": {"status": "CONNECTED", "source": "asof + minutes_until_start + time_bucket"},
        "5_result_labels": {"status": "CONNECTED", "source": "manual result payload or theOddsAPI soccer scores endpoint"},
        "6_market_shape_signals": {"status": "SCHEMA_INSTALLED", "source": "computed from 3-way snapshot deltas"},
        "7_cross_market_confirmation": {"status": "SCHEMA_INSTALLED", "source": "h2h/spread/total availability and movement"},
        "8_pitching_data": {"status": "NOT_APPLICABLE", "source": None},
        "9_weather": {"status": "NOT_CONNECTED_YET", "source": None},
        "10_injuries_lineups_news": {"status": "NOT_CONNECTED_YET", "source": None},
        "11_public_betting_handle": {"status": "NOT_CONNECTED_YET", "source": None},
        "12_game_context": {"status": "LEAGUE_SEGMENTED_SCHEMA_INSTALLED", "source": "league_segment + sport_key + match context fields"},
        "13_prediction_logs_no_edge": {"status": "CONNECTED", "source": "soccer prediction and result audit rows"},
        "14_model_versioning": {"status": "CONNECTED", "source": "model_version + feature_version + league_profile_version"},
    }


def _external_context(game: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    profile = _league_profile(game or {}) if game else None
    return {
        "weather": {"source_status": "NOT_CONNECTED_YET"},
        "injuries_lineups_news": {"source_status": "NOT_CONNECTED_YET"},
        "public_betting_handle": {"source_status": "NOT_CONNECTED_YET"},
        "game_context": {
            "source_status": "LEAGUE_SEGMENTED_SCHEMA_INSTALLED_PARTIAL",
            "league_context": profile,
            "table_position": None,
            "rest_days": None,
            "travel_spot": None,
            "cup_match": None,
        },
    }


def build_game_audit_row(*, slate_date_et: str, asof: str, t: str, run_type: str, game: Dict[str, Any], raw_game: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    minutes_until_start = _minutes_until(asof, game.get("commence_time"))
    game_key = game.get("game_key") or game.get("id")
    consensus = soccer_three_way_probs(game)
    profile = _league_profile(game)
    return ddb_safe({
        "PK": f"AUDIT#soccer#{slate_date_et}",
        "SK": f"ASOF#{asof}#LEAGUE#{profile.get('league_segment')}#GAME#{game_key}",
        "entity_type": "GAME_SNAPSHOT_AUDIT",
        "sport": "soccer",
        "sport_key": game.get("sport_key"),
        "league_segment": profile.get("league_segment"),
        "league_name": profile.get("league_name"),
        "league_profile_version": LEAGUE_PROFILE_VERSION,
        "league_profile": profile,
        "slate_date_et": slate_date_et,
        "asof": asof,
        "t": t,
        "run_type": run_type,
        "created_at": _now_iso(),
        "model_version": MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "game_id": game.get("id"),
        "game_key": game_key,
        "home_team": game.get("home_team"),
        "away_team": game.get("away_team"),
        "commence_time": game.get("commence_time"),
        "minutes_until_start": minutes_until_start,
        "time_bucket": _time_bucket(minutes_until_start),
        "soccer_market_type": "THREE_WAY_HOME_DRAW_AWAY",
        "market_type": "3-way home/draw/away",
        "consensus_three_way": consensus,
        "market_availability": _market_availability(game),
        "markets_stored": game.get("markets_stored") or [],
        "book_keys": sorted((game.get("books") or {}).keys()),
        "normalized_snapshot": game,
        "raw_game_hash": _payload_hash(raw_game) if raw_game else None,
        "compact_game_hash": _payload_hash(game),
        "line_lifecycle": {"line_lifecycle_status": "CURRENT_ONLY_UNTIL_HISTORY_DERIVED"},
        "market_shape_signals": {"source_status": "SCHEMA_INSTALLED_PENDING_DELTA_COMPUTE", "league_segment": profile.get("league_segment")},
        "external_context": _external_context(game),
        "result_labels": {"source_status": "PENDING_FINAL", "winner_result": None, "home_score": None, "away_score": None, "draw": None},
        "prediction_status": "NOT_PREDICTED_YET",
        "outcome_status": "PENDING",
        "reason_codes": [],
        "source_registry": source_registry(),
        "capture_items_1_to_14_installed": True,
        "notes": ["Soccer full 1-14 capture schema installed.", "Soccer h2h is three-way: home/draw/away.", "League segmentation is required before soccer scoring."],
    })


def record_soccer_snapshot_audit(*, slate_date_et: str, asof: str, t: str, run_type: str, compact_snapshot: Dict[str, Any], raw_by_sport_key: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    if signal_ledger_tbl is None:
        return {"ok": False, "stored": 0, "error": "SIGNAL_LEDGER_TABLE not configured"}
    raw_by_id = {}
    for games in (raw_by_sport_key or {}).values():
        for game in games or []:
            if game.get("id"):
                raw_by_id[game["id"]] = game
    stored = 0
    errors: List[str] = []
    league_segments = sorted({game.get("league_segment") or _league_profile(game).get("league_segment") for game in compact_snapshot.get("games", []) or []})
    for game in compact_snapshot.get("games", []) or []:
        try:
            signal_ledger_tbl.put_item(Item=build_game_audit_row(slate_date_et=slate_date_et, asof=asof, t=t, run_type=run_type, game=game, raw_game=raw_by_id.get(game.get("id"))))
            stored += 1
        except Exception as exc:
            errors.append(f"{game.get('game_key')}: {exc}")
    summary = ddb_safe({
        "PK": f"AUDIT#soccer#{slate_date_et}",
        "SK": f"ASOF#{asof}#SUMMARY",
        "entity_type": "SNAPSHOT_AUDIT_SUMMARY",
        "sport": "soccer",
        "slate_date_et": slate_date_et,
        "asof": asof,
        "t": t,
        "run_type": run_type,
        "created_at": _now_iso(),
        "feature_version": FEATURE_VERSION,
        "model_version": MODEL_VERSION,
        "league_profile_version": LEAGUE_PROFILE_VERSION,
        "league_segments": league_segments,
        "game_count": len(compact_snapshot.get("games", []) or []),
        "audit_rows_stored": stored,
        "soccer_keys": compact_snapshot.get("soccer_keys") or [],
        "markets": compact_snapshot.get("markets") or [],
        "snapshot_hash": _payload_hash(compact_snapshot),
        "source_registry": source_registry(),
        "capture_items_1_to_14_installed": True,
        "errors": errors,
    })
    signal_ledger_tbl.put_item(Item=summary)
    return {"ok": len(errors) == 0, "stored": stored, "errors": errors, "feature_version": FEATURE_VERSION, "league_profile_version": LEAGUE_PROFILE_VERSION, "league_segments": league_segments}


def record_soccer_no_edge_prediction_rows(*, slate_date_et: str, asof: str, compact_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    if predictions_tbl is None:
        return {"ok": False, "stored": 0, "error": "PREDICTIONS_TABLE not configured"}
    stored = 0
    for game in compact_snapshot.get("games", []) or []:
        game_key = game.get("game_key") or game.get("id")
        profile = _league_profile(game)
        predictions_tbl.put_item(Item=ddb_safe({
            "PK": f"PRED#soccer#{slate_date_et}",
            "SK": f"ASOF#{asof}#LEAGUE#{profile.get('league_segment')}#GAME#{game_key}",
            "entity_type": "PREDICTION_AUDIT",
            "sport": "soccer",
            "sport_key": game.get("sport_key"),
            "league_segment": profile.get("league_segment"),
            "league_name": profile.get("league_name"),
            "league_profile_version": LEAGUE_PROFILE_VERSION,
            "league_profile": profile,
            "slate_date_et": slate_date_et,
            "asof": asof,
            "created_at": _now_iso(),
            "model_version": MODEL_VERSION,
            "feature_version": FEATURE_VERSION,
            "game_id": game.get("id"),
            "game_key": game_key,
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "prediction_market": "h2h_three_way",
            "market_type": "3-way home/draw/away",
            "prediction_status": "NO_EDGE",
            "status": "NO_EDGE",
            "predicted_outcome": None,
            "confidence": None,
            "confidence_label": "NO_EDGE",
            "reason_codes": ["initial_soccer_audit_capture", "three_way_no_published_prediction_yet", "league_segment_required"],
            "consensus_three_way": soccer_three_way_probs(game),
            "outcome_status": "PENDING",
            "success": None,
            "source_registry": source_registry(),
            "capture_items_1_to_14_installed": True,
        }))
        stored += 1
    return {"ok": True, "stored": stored, "feature_version": FEATURE_VERSION, "league_profile_version": LEAGUE_PROFILE_VERSION}


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return None


def _score_for_team(result: Dict[str, Any], team: Optional[str]) -> Optional[int]:
    if not team:
        return None
    scores = result.get("scores") or []
    if isinstance(scores, list):
        for row in scores:
            if row.get("name") == team:
                return _safe_int(row.get("score"))
    # Manual payload fallback names.
    if team == result.get("home_team"):
        return _safe_int(result.get("home_score"))
    if team == result.get("away_team"):
        return _safe_int(result.get("away_score"))
    return None


def _derive_game_key(result: Dict[str, Any]) -> str:
    return result.get("game_key") or f"soccer|{result.get('sport_key')}|{result.get('away_team')}|{result.get('home_team')}|{result.get('commence_time')}"


def _normalize_result(result: Dict[str, Any], slate_date_et: str, source: str) -> Optional[Dict[str, Any]]:
    home_team = result.get("home_team")
    away_team = result.get("away_team")
    home_score = _score_for_team(result, home_team)
    away_score = _score_for_team(result, away_team)
    completed = bool(result.get("completed") or result.get("status") in {"FINAL", "COMPLETED"})
    if home_score is None or away_score is None:
        return None
    if home_score > away_score:
        actual_outcome = "home"
        actual_selection = home_team
    elif away_score > home_score:
        actual_outcome = "away"
        actual_selection = away_team
    else:
        actual_outcome = "draw"
        actual_selection = "Draw"
    game_key = _derive_game_key(result)
    profile = get_soccer_league_profile(result.get("sport_key") or "")
    return ddb_safe({
        "PK": f"OUTCOME#soccer#{slate_date_et}",
        "SK": f"GAME#{game_key}",
        "entity_type": "SOCCER_FINAL_OUTCOME",
        "sport": "soccer",
        "sport_key": result.get("sport_key"),
        "league_segment": profile.get("league_segment"),
        "league_name": profile.get("league_name"),
        "league_profile_version": LEAGUE_PROFILE_VERSION,
        "slate_date_et": slate_date_et,
        "created_at": _now_iso(),
        "source": source,
        "source_result_id": result.get("id"),
        "game_id": result.get("id"),
        "game_key": game_key,
        "home_team": home_team,
        "away_team": away_team,
        "commence_time": result.get("commence_time"),
        "completed": completed,
        "home_score": home_score,
        "away_score": away_score,
        "actual_outcome": actual_outcome,
        "actual_selection": actual_selection,
        "draw": actual_outcome == "draw",
        "raw_result_hash": _payload_hash(result),
    })


def _fetch_scores_for_league(sport_key: str, days_from: int = 3) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if not ODDS_API_KEY:
        return [], "ODDS_API_KEY not configured"
    query = urllib.parse.urlencode({"apiKey": ODDS_API_KEY, "daysFrom": max(1, min(int(days_from), 3))})
    url = f"https://api.the-odds-api.com/v4/sports/{urllib.parse.quote(sport_key)}/scores/?{query}"
    try:
        with urllib.request.urlopen(url, timeout=12) as response:
            return json.loads(response.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = str(exc)
        return [], f"{sport_key}: HTTP {exc.code}: {detail}"
    except Exception as exc:
        return [], f"{sport_key}: {exc}"


def _collect_score_results(*, manual_results: Optional[List[Dict[str, Any]]] = None, fetch_live: bool = False, days_from: int = 3) -> Dict[str, Any]:
    source = "manual_payload" if manual_results is not None else "the_odds_api_scores"
    raw_results: List[Dict[str, Any]] = []
    errors: List[str] = []
    if manual_results is not None:
        raw_results = manual_results
    elif fetch_live:
        for sport_key in SOCCER_LEAGUE_SEGMENTS.keys():
            results, error = _fetch_scores_for_league(sport_key, days_from=days_from)
            raw_results.extend(results or [])
            if error:
                errors.append(error)
    return {"source": source, "raw_results": raw_results, "errors": errors}


def _query_predictions(slate_date_et: str) -> List[Dict[str, Any]]:
    if predictions_tbl is None:
        return []
    items: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(f"PRED#soccer#{slate_date_et}")}
    while True:
        resp = predictions_tbl.query(**kwargs)
        items.extend(resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def _query_outcomes(slate_date_et: str) -> List[Dict[str, Any]]:
    if outcomes_tbl is None:
        return []
    items: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(f"OUTCOME#soccer#{slate_date_et}")}
    while True:
        resp = outcomes_tbl.query(**kwargs)
        items.extend(resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def _grade_predictions(slate_date_et: str, outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    if predictions_tbl is None:
        return {"ok": False, "graded": 0, "error": "PREDICTIONS_TABLE not configured"}
    outcome_by_game_key = {o.get("game_key"): o for o in outcomes if o.get("game_key")}
    predictions = _query_predictions(slate_date_et)
    graded = correct = wrong = no_pick = pending = 0
    graded_items: List[Dict[str, Any]] = []
    for pred in predictions:
        game_key = pred.get("game_key")
        outcome = outcome_by_game_key.get(game_key)
        predicted = pred.get("attempted_outcome") or pred.get("predicted_outcome")
        if not outcome:
            pending += 1
            continue
        if not predicted:
            no_pick += 1
            result_status = "NO_PICK"
            success = None
        else:
            graded += 1
            success = predicted == outcome.get("actual_outcome")
            result_status = "CORRECT" if success else "WRONG"
            correct += 1 if success else 0
            wrong += 0 if success else 1
        predictions_tbl.update_item(
            Key={"PK": pred["PK"], "SK": pred["SK"]},
            UpdateExpression="SET outcome_status=:os, #status=:st, success=:success, actual_outcome=:ao, actual_selection=:asel, actual_home_score=:hs, actual_away_score=:as, graded_at=:ga, result_audit_version=:rav",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=ddb_safe({
                ":os": "GRADED" if result_status in {"CORRECT", "WRONG", "NO_PICK"} else "PENDING",
                ":st": result_status,
                ":success": success,
                ":ao": outcome.get("actual_outcome"),
                ":asel": outcome.get("actual_selection"),
                ":hs": outcome.get("home_score"),
                ":as": outcome.get("away_score"),
                ":ga": _now_iso(),
                ":rav": FEATURE_VERSION,
            }),
        )
        graded_items.append({
            "game_key": game_key,
            "match": f"{pred.get('away_team')} at {pred.get('home_team')}",
            "predicted_outcome": predicted,
            "predicted_selection": pred.get("attempted_selection") or pred.get("hot_label"),
            "actual_outcome": outcome.get("actual_outcome"),
            "actual_selection": outcome.get("actual_selection"),
            "home_score": outcome.get("home_score"),
            "away_score": outcome.get("away_score"),
            "status": result_status,
        })
    accuracy = round(correct / graded * 100, 2) if graded else None
    return {"ok": True, "prediction_rows": len(predictions), "graded": graded, "correct": correct, "wrong": wrong, "no_pick": no_pick, "pending": pending, "accuracy_pct": accuracy, "graded_items": graded_items}


def soccer_results_audit(*, slate_date: Optional[str] = None, manual_results: Optional[List[Dict[str, Any]]] = None, fetch_live: bool = False, days_from: int = 3) -> Dict[str, Any]:
    if outcomes_tbl is None:
        raise RuntimeError("OUTCOMES_TABLE not configured")
    slate_date = slate_date or _slate_date_et()
    collected = _collect_score_results(manual_results=manual_results, fetch_live=fetch_live, days_from=days_from)
    stored = 0
    skipped = 0
    for result in collected["raw_results"]:
        normalized = _normalize_result(result, slate_date, collected["source"])
        if not normalized:
            skipped += 1
            continue
        outcomes_tbl.put_item(Item=normalized)
        stored += 1
    outcomes = _query_outcomes(slate_date)
    grade_result = _grade_predictions(slate_date, outcomes)
    return {
        "ok": True,
        "sport": "soccer",
        "slate_date_et": slate_date,
        "result_audit_version": FEATURE_VERSION,
        "source": collected["source"],
        "fetch_live_requested": fetch_live,
        "raw_results_seen": len(collected["raw_results"]),
        "outcomes_stored_now": stored,
        "outcomes_skipped_unusable": skipped,
        "outcomes_available": len(outcomes),
        "score_pull_errors": collected["errors"],
        "grading": grade_result,
        "next_step": "If outcomes_available is 0, run again after matches complete or POST manual results payload.",
    }


def soccer_results_status(slate_date: Optional[str] = None) -> Dict[str, Any]:
    if outcomes_tbl is None:
        raise RuntimeError("OUTCOMES_TABLE not configured")
    slate_date = slate_date or _slate_date_et()
    outcomes = _query_outcomes(slate_date)
    predictions = _query_predictions(slate_date)
    graded = [p for p in predictions if p.get("status") in {"CORRECT", "WRONG"}]
    correct = [p for p in graded if p.get("status") == "CORRECT"]
    accuracy = round(len(correct) / len(graded) * 100, 2) if graded else None
    by_league: Dict[str, Dict[str, Any]] = {}
    for p in predictions:
        league = p.get("league_segment") or "unknown_soccer_league"
        bucket = by_league.setdefault(league, {"predictions_count": 0, "graded_count": 0, "correct": 0, "wrong": 0, "accuracy_pct": None})
        bucket["predictions_count"] += 1
        if p.get("status") in {"CORRECT", "WRONG"}:
            bucket["graded_count"] += 1
            if p.get("status") == "CORRECT":
                bucket["correct"] += 1
            else:
                bucket["wrong"] += 1
    for bucket in by_league.values():
        bucket["accuracy_pct"] = round(bucket["correct"] / bucket["graded_count"] * 100, 2) if bucket["graded_count"] else None
    return {"ok": True, "sport": "soccer", "slate_date_et": slate_date, "league_profile_version": LEAGUE_PROFILE_VERSION, "outcomes_count": len(outcomes), "predictions_count": len(predictions), "graded_count": len(graded), "correct": len(correct), "wrong": len(graded) - len(correct), "accuracy_pct": accuracy, "accuracy_by_league": by_league, "target_success_rate": 75, "outcomes": outcomes}


def soccer_source_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "sport": "soccer",
        "algorithm_silo": "SOC-B1.1-three-way-audit-v1",
        "league_profile_version": LEAGUE_PROFILE_VERSION,
        "segmentation": "league_segmented_soccer",
        "items_status": {
            "full_1_to_14_soccer_audit_schema": "CONNECTED",
            "soccer_movement_delta_endpoint": "CONNECTED",
            "soccer_hot_side_prediction_endpoint": "CONNECTED",
            "soccer_result_evaluation_endpoint": "CONNECTED_LEAGUE_SEGMENTED",
            "soccer_results_audit_grading_endpoint": "CONNECTED",
            "soccer_source_status_endpoint": "CONNECTED",
            "soccer_specific_algorithm_silo": "CONNECTED",
            "soccer_league_segmentation": "CONNECTED",
        },
        "external_data_sources": {
            "odds_h2h_spread_total": "CONNECTED",
            "all_returned_books": "CONNECTED",
            "timestamps": "CONNECTED",
            "audit_ledger_schema": "CONNECTED_LEAGUE_SEGMENTED",
            "results_scores": "CONNECTED_MANUAL_AND_LIVE_PULL_READY",
            "weather": "NOT_CONNECTED_YET",
            "injuries_lineups_news": "NOT_CONNECTED_YET",
            "public_betting_handle": "NOT_CONNECTED_YET",
            "pitching": "NOT_APPLICABLE_TO_SOCCER",
        },
        "important_note": "Soccer h2h is modeled as home/draw/away and segmented by league. It does not use MLB 2-outcome logic.",
    }
