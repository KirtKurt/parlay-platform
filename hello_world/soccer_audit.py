from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key


dynamodb = boto3.resource("dynamodb")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")
OUTCOMES_TABLE = os.environ.get("OUTCOMES_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None
outcomes_tbl = dynamodb.Table(OUTCOMES_TABLE) if OUTCOMES_TABLE else None

FEATURE_VERSION = "soccer_full_capture_1_to_14_v1"
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
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
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

    consensus = {}
    for side in ["home", "draw", "away"]:
        consensus[side] = sum(book[side] for book in book_probs.values()) / len(book_probs)
    ordered = sorted(consensus.items(), key=lambda kv: kv[1], reverse=True)
    leader = ordered[0][0]
    gap = ordered[0][1] - ordered[1][1]
    return {
        "home": round(consensus["home"], 6),
        "draw": round(consensus["draw"], 6),
        "away": round(consensus["away"], 6),
        "books": sorted(book_probs.keys()),
        "book_count": len(book_probs),
        "leader": leader,
        "leader_gap": round(gap, 6),
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


def source_registry() -> Dict[str, Any]:
    return {
        "1_raw_odds_snapshots": {"status": "CONNECTED_HASHED", "source": "theOddsAPI soccer odds response"},
        "2_every_book": {"status": "CONNECTED", "source": "all returned bookmakers"},
        "3_opening_every_move_closing": {"status": "PARTIAL_CONNECTED", "source": "snapshot timeline"},
        "4_timestamp_quality": {"status": "CONNECTED", "source": "asof + minutes_until_start + time_bucket"},
        "5_result_labels": {"status": "CONNECTED_PENDING_PULL", "source": "theOddsAPI soccer scores endpoint"},
        "6_market_shape_signals": {"status": "SCHEMA_INSTALLED", "source": "computed from 3-way snapshot deltas"},
        "7_cross_market_confirmation": {"status": "SCHEMA_INSTALLED", "source": "h2h/spread/total availability and movement"},
        "8_pitching_data": {"status": "NOT_APPLICABLE", "source": None},
        "9_weather": {"status": "NOT_CONNECTED_YET", "source": None},
        "10_injuries_lineups_news": {"status": "NOT_CONNECTED_YET", "source": None},
        "11_public_betting_handle": {"status": "NOT_CONNECTED_YET", "source": None},
        "12_game_context": {"status": "SCHEMA_INSTALLED", "source": "league, sport_key, match context fields"},
        "13_prediction_logs_no_edge": {"status": "CONNECTED", "source": "soccer prediction skeleton rows"},
        "14_model_versioning": {"status": "CONNECTED", "source": "model_version + feature_version"},
    }


def _external_context() -> Dict[str, Any]:
    return {
        "weather": {"source_status": "NOT_CONNECTED_YET"},
        "injuries_lineups_news": {"source_status": "NOT_CONNECTED_YET"},
        "public_betting_handle": {"source_status": "NOT_CONNECTED_YET"},
        "game_context": {"source_status": "SCHEMA_INSTALLED_PARTIAL", "league_context": None, "table_position": None, "rest_days": None, "travel_spot": None, "cup_match": None},
    }


def build_game_audit_row(*, slate_date_et: str, asof: str, t: str, run_type: str, game: Dict[str, Any], raw_game: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    minutes_until_start = _minutes_until(asof, game.get("commence_time"))
    game_key = game.get("game_key") or game.get("id")
    consensus = soccer_three_way_probs(game)
    return ddb_safe({
        "PK": f"AUDIT#soccer#{slate_date_et}",
        "SK": f"ASOF#{asof}#GAME#{game_key}",
        "entity_type": "GAME_SNAPSHOT_AUDIT",
        "sport": "soccer",
        "sport_key": game.get("sport_key"),
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
        "consensus_three_way": consensus,
        "market_availability": _market_availability(game),
        "markets_stored": game.get("markets_stored") or [],
        "book_keys": sorted((game.get("books") or {}).keys()),
        "normalized_snapshot": game,
        "raw_game_hash": _payload_hash(raw_game) if raw_game else None,
        "compact_game_hash": _payload_hash(game),
        "line_lifecycle": {"line_lifecycle_status": "CURRENT_ONLY_UNTIL_HISTORY_DERIVED"},
        "market_shape_signals": {"source_status": "SCHEMA_INSTALLED_PENDING_DELTA_COMPUTE"},
        "external_context": _external_context(),
        "result_labels": {"source_status": "PENDING_FINAL", "winner_result": None, "home_score": None, "away_score": None, "draw": None},
        "prediction_status": "NOT_PREDICTED_YET",
        "outcome_status": "PENDING",
        "reason_codes": [],
        "source_registry": source_registry(),
        "capture_items_1_to_14_installed": True,
        "notes": ["Soccer full 1-14 capture schema installed.", "Soccer h2h is three-way: home/draw/away. Do not apply 2-way MLB logic."],
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
    return {"ok": len(errors) == 0, "stored": stored, "errors": errors, "feature_version": FEATURE_VERSION}


def record_soccer_no_edge_prediction_rows(*, slate_date_et: str, asof: str, compact_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    if predictions_tbl is None:
        return {"ok": False, "stored": 0, "error": "PREDICTIONS_TABLE not configured"}
    stored = 0
    for game in compact_snapshot.get("games", []) or []:
        game_key = game.get("game_key") or game.get("id")
        predictions_tbl.put_item(Item=ddb_safe({
            "PK": f"PRED#soccer#{slate_date_et}",
            "SK": f"ASOF#{asof}#GAME#{game_key}",
            "entity_type": "PREDICTION_AUDIT",
            "sport": "soccer",
            "sport_key": game.get("sport_key"),
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
            "prediction_status": "NO_EDGE",
            "status": "NO_EDGE",
            "predicted_outcome": None,
            "confidence": None,
            "confidence_label": "NO_EDGE",
            "reason_codes": ["initial_soccer_audit_capture", "three_way_no_published_prediction_yet"],
            "consensus_three_way": soccer_three_way_probs(game),
            "outcome_status": "PENDING",
            "success": None,
            "source_registry": source_registry(),
            "capture_items_1_to_14_installed": True,
        }))
        stored += 1
    return {"ok": True, "stored": stored, "feature_version": FEATURE_VERSION}


def soccer_results_status(slate_date: Optional[str] = None) -> Dict[str, Any]:
    if outcomes_tbl is None:
        raise RuntimeError("OUTCOMES_TABLE not configured")
    slate_date = slate_date or _slate_date_et()
    outcomes = outcomes_tbl.query(KeyConditionExpression=Key("PK").eq(f"OUTCOME#soccer#{slate_date}")).get("Items", [])
    predictions = predictions_tbl.query(KeyConditionExpression=Key("PK").eq(f"PRED#soccer#{slate_date}")).get("Items", []) if predictions_tbl is not None else []
    graded = [p for p in predictions if p.get("status") in {"CORRECT", "WRONG"}]
    correct = [p for p in graded if p.get("status") == "CORRECT"]
    accuracy = round(len(correct) / len(graded) * 100, 2) if graded else None
    return {"ok": True, "sport": "soccer", "slate_date_et": slate_date, "outcomes_count": len(outcomes), "predictions_count": len(predictions), "graded_count": len(graded), "correct": len(correct), "wrong": len(graded) - len(correct), "accuracy_pct": accuracy, "target_success_rate": 75, "outcomes": outcomes}


def soccer_source_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "sport": "soccer",
        "algorithm_silo": "SOC-B1.1-three-way-audit-v1",
        "items_status": {
            "full_1_to_14_soccer_audit_schema": "CONNECTED",
            "soccer_movement_delta_endpoint": "CONNECTED",
            "soccer_hot_side_prediction_endpoint": "CONNECTED",
            "soccer_result_evaluation_endpoint": "CONNECTED",
            "soccer_source_status_endpoint": "CONNECTED",
            "soccer_specific_algorithm_silo": "CONNECTED",
        },
        "external_data_sources": {
            "odds_h2h_spread_total": "CONNECTED",
            "all_returned_books": "CONNECTED",
            "timestamps": "CONNECTED",
            "audit_ledger_schema": "CONNECTED",
            "results_scores": "CONNECTED_PENDING_COMPLETED_MATCHES",
            "weather": "NOT_CONNECTED_YET",
            "injuries_lineups_news": "NOT_CONNECTED_YET",
            "public_betting_handle": "NOT_CONNECTED_YET",
            "pitching": "NOT_APPLICABLE_TO_SOCCER",
        },
        "important_note": "Soccer h2h is modeled as home/draw/away. It does not use MLB 2-outcome logic.",
    }
