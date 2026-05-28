from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import boto3


dynamodb = boto3.resource("dynamodb")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")

signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None

MODEL_VERSION_BY_SPORT = {
    "mlb": "MLB-B1.0A.4.2-audit-v1",
    "nfl": "NFL-OptionB1-audit-v1",
    "soccer": "SOC-B1.1-audit-v1",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: ddb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [ddb_safe(v) for v in value]
    return value


def _american_to_prob(american: int) -> float:
    return abs(american) / (abs(american) + 100.0) if american < 0 else 100.0 / (american + 100.0)


def _vig_norm(home_american: int, away_american: int) -> Tuple[float, float]:
    home_raw = _american_to_prob(home_american)
    away_raw = _american_to_prob(away_american)
    total = home_raw + away_raw
    if total <= 0:
        return 0.5, 0.5
    return home_raw / total, away_raw / total


def _payload_hash(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_book_ml(game: Dict[str, Any], book_key: str) -> Optional[Dict[str, int]]:
    ml = ((game.get("books") or {}).get(book_key) or {}).get("ml")
    if not ml or ml.get("home") is None or ml.get("away") is None:
        return None
    return {"home": int(ml["home"]), "away": int(ml["away"])}


def _favorite_from_consensus(game: Dict[str, Any]) -> Dict[str, Any]:
    home_team = game.get("home_team")
    away_team = game.get("away_team")
    home_probs: List[float] = []
    away_probs: List[float] = []
    books_with_ml: List[str] = []

    for book_key in sorted((game.get("books") or {}).keys()):
        ml = _safe_book_ml(game, book_key)
        if not ml:
            continue
        home_p, away_p = _vig_norm(ml["home"], ml["away"])
        home_probs.append(home_p)
        away_probs.append(away_p)
        books_with_ml.append(book_key)

    if not home_probs:
        return {
            "favorite_team": None,
            "underdog_team": None,
            "favorite_side": None,
            "underdog_side": None,
            "consensus_home_p": None,
            "consensus_away_p": None,
            "leader_gap": None,
            "books_with_ml": [],
        }

    home_avg = sum(home_probs) / len(home_probs)
    away_avg = sum(away_probs) / len(away_probs)
    if home_avg >= away_avg:
        favorite_team, underdog_team = home_team, away_team
        favorite_side, underdog_side = "home", "away"
        gap = home_avg - away_avg
    else:
        favorite_team, underdog_team = away_team, home_team
        favorite_side, underdog_side = "away", "home"
        gap = away_avg - home_avg

    return {
        "favorite_team": favorite_team,
        "underdog_team": underdog_team,
        "favorite_side": favorite_side,
        "underdog_side": underdog_side,
        "consensus_home_p": round(home_avg, 6),
        "consensus_away_p": round(away_avg, 6),
        "leader_gap": round(gap, 6),
        "books_with_ml": books_with_ml,
    }


def _market_availability(game: Dict[str, Any]) -> Dict[str, Any]:
    books = game.get("books") or {}
    ml_books = []
    spread_books = []
    total_books = []
    for book_key, payload in books.items():
        if payload.get("ml"):
            ml_books.append(book_key)
        if payload.get("spread"):
            spread_books.append(book_key)
        if payload.get("total"):
            total_books.append(book_key)
    return {
        "book_count_total": len(books),
        "ml_book_count": len(ml_books),
        "spread_book_count": len(spread_books),
        "total_book_count": len(total_books),
        "ml_books": sorted(ml_books),
        "spread_books": sorted(spread_books),
        "total_books": sorted(total_books),
        "has_ml": bool(ml_books),
        "has_spread": bool(spread_books),
        "has_total": bool(total_books),
    }


def build_game_audit_row(
    *,
    sport: str,
    slate_date_et: str,
    asof: str,
    t: str,
    run_type: str,
    game: Dict[str, Any],
    raw_game: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    consensus = _favorite_from_consensus(game)
    availability = _market_availability(game)
    model_version = MODEL_VERSION_BY_SPORT.get(sport, f"{sport}-audit-v1")
    game_key = game.get("game_key") or game.get("id")

    return ddb_safe({
        "PK": f"AUDIT#{sport}#{slate_date_et}",
        "SK": f"ASOF#{asof}#GAME#{game_key}",
        "entity_type": "GAME_SNAPSHOT_AUDIT",
        "sport": sport,
        "slate_date_et": slate_date_et,
        "asof": asof,
        "t": t,
        "run_type": run_type,
        "created_at": _now_iso(),
        "model_version": model_version,
        "feature_version": "market_features_v1",
        "game_id": game.get("id"),
        "game_key": game_key,
        "home_team": game.get("home_team"),
        "away_team": game.get("away_team"),
        "commence_time": game.get("commence_time"),
        "minutes_until_start": None,
        "consensus": consensus,
        "market_availability": availability,
        "markets_stored": game.get("markets_stored") or [],
        "book_keys": sorted((game.get("books") or {}).keys()),
        "raw_game_hash": _payload_hash(raw_game) if raw_game else None,
        "compact_game_hash": _payload_hash(game),
        "prediction_status": "NOT_PREDICTED_YET",
        "outcome_status": "PENDING",
        "reason_codes": [],
        "notes": [
            "Audit row created at snapshot time.",
            "Use this row for model training, signal correlation, and regret-proof feature expansion.",
        ],
    })


def record_snapshot_audit(
    *,
    sport: str,
    slate_date_et: str,
    asof: str,
    t: str,
    run_type: str,
    compact_snapshot: Dict[str, Any],
    raw_games: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if signal_ledger_tbl is None:
        return {"ok": False, "stored": 0, "error": "SIGNAL_LEDGER_TABLE not configured"}

    raw_by_id = {g.get("id"): g for g in (raw_games or []) if g.get("id")}
    stored = 0
    errors: List[str] = []
    for game in compact_snapshot.get("games", []) or []:
        raw_game = raw_by_id.get(game.get("id"))
        try:
            signal_ledger_tbl.put_item(Item=build_game_audit_row(
                sport=sport,
                slate_date_et=slate_date_et,
                asof=asof,
                t=t,
                run_type=run_type,
                game=game,
                raw_game=raw_game,
            ))
            stored += 1
        except Exception as exc:
            errors.append(f"{game.get('game_key')}: {exc}")

    summary = ddb_safe({
        "PK": f"AUDIT#{sport}#{slate_date_et}",
        "SK": f"ASOF#{asof}#SUMMARY",
        "entity_type": "SNAPSHOT_AUDIT_SUMMARY",
        "sport": sport,
        "slate_date_et": slate_date_et,
        "asof": asof,
        "t": t,
        "run_type": run_type,
        "created_at": _now_iso(),
        "game_count": len(compact_snapshot.get("games", []) or []),
        "audit_rows_stored": stored,
        "markets": compact_snapshot.get("markets") or [],
        "available_book_keys": compact_snapshot.get("available_book_keys") or [],
        "snapshot_hash": _payload_hash(compact_snapshot),
        "raw_response_hash": _payload_hash(raw_games) if raw_games else None,
        "errors": errors,
    })
    signal_ledger_tbl.put_item(Item=summary)
    return {"ok": len(errors) == 0, "stored": stored, "errors": errors}


def create_prediction_skeleton(
    *,
    sport: str,
    slate_date_et: str,
    asof: str,
    game: Dict[str, Any],
    status: str = "NO_EDGE",
    reason_codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    consensus = _favorite_from_consensus(game)
    game_key = game.get("game_key") or game.get("id")
    row = ddb_safe({
        "PK": f"PRED#{sport}#{slate_date_et}",
        "SK": f"ASOF#{asof}#GAME#{game_key}",
        "entity_type": "PREDICTION_AUDIT",
        "sport": sport,
        "slate_date_et": slate_date_et,
        "asof": asof,
        "created_at": _now_iso(),
        "model_version": MODEL_VERSION_BY_SPORT.get(sport, f"{sport}-audit-v1"),
        "game_id": game.get("id"),
        "game_key": game_key,
        "home_team": game.get("home_team"),
        "away_team": game.get("away_team"),
        "prediction_status": status,
        "predicted_team": None,
        "confidence": None,
        "confidence_label": "NO_EDGE",
        "reason_codes": reason_codes or [],
        "consensus": consensus,
        "outcome_status": "PENDING",
        "success": None,
    })
    return row


def record_no_edge_prediction_rows(
    *,
    sport: str,
    slate_date_et: str,
    asof: str,
    compact_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    if predictions_tbl is None:
        return {"ok": False, "stored": 0, "error": "PREDICTIONS_TABLE not configured"}
    stored = 0
    for game in compact_snapshot.get("games", []) or []:
        predictions_tbl.put_item(Item=create_prediction_skeleton(
            sport=sport,
            slate_date_et=slate_date_et,
            asof=asof,
            game=game,
            status="NO_EDGE",
            reason_codes=["initial_audit_capture", "no_published_prediction_yet"],
        ))
        stored += 1
    return {"ok": True, "stored": stored}
