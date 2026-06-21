import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

from inqsi_core import auto_parlay
from inqsi_live import pull_scores_for_sport


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


SIGNAL_LEDGER_TABLE = _env("SIGNAL_LEDGER_TABLE")
PREDICTIONS_TABLE = _env("PREDICTIONS_TABLE")
OUTCOMES_TABLE = _env("OUTCOMES_TABLE")

_dynamodb = boto3.resource("dynamodb")
_signal_ledger = _dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
_predictions = _dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None
_outcomes = _dynamodb.Table(OUTCOMES_TABLE) if OUTCOMES_TABLE else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table():
    table = _outcomes or _predictions or _signal_ledger
    if table is None:
        raise RuntimeError("No autopsy storage table configured")
    return table


def _to_ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
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


def save_platform_auto_parlay(sport_key: str, model_version: str = "INQSI_AUTOPARLAY_V1") -> Dict[str, Any]:
    if not sport_key:
        raise RuntimeError("sport_key is required; autopsy cannot mix sports")
    built = auto_parlay(sport_key)
    if not built.get("built"):
        return {"ok": True, "built": False, "sport_key": sport_key, "reason": built.get("refusal")}
    asof = now_iso()
    parlay_id = f"{sport_key}#{asof}#{uuid.uuid4().hex[:8]}"
    item = {
        "PK": f"INQSI#AUTOPARLAY#{sport_key}",
        "SK": f"PARLAY#{parlay_id}",
        "entity_type": "INQSI_PLATFORM_AUTO_PARLAY",
        "sport_key": sport_key,
        "parlay_id": parlay_id,
        "created_at": asof,
        "status": "OPEN",
        "source": "platform_generated",
        "model_version": model_version,
        "structure": built.get("structure"),
        "selected_legs": built.get("selected_legs"),
        "games": built.get("games"),
        "ranked_combinations": built.get("ranked_combinations"),
        "autopsy": {},
    }
    _table().put_item(Item=_to_ddb(item))
    return {"ok": True, "built": True, "sport_key": sport_key, "parlay_id": parlay_id, "created_at": asof}


def open_platform_parlays(sport_key: str, limit: int = 50) -> List[Dict[str, Any]]:
    response = _table().query(
        KeyConditionExpression=Key("PK").eq(f"INQSI#AUTOPARLAY#{sport_key}"),
        ScanIndexForward=False,
        Limit=limit,
    )
    items = [_from_ddb(i) for i in response.get("Items", [])]
    return [i for i in items if i.get("status") == "OPEN"]


def _score_winners(scores: List[Dict[str, Any]]) -> Dict[str, str]:
    winners = {}
    for game in scores:
        if game.get("completed") is not True:
            continue
        game_id = game.get("id")
        teams = game.get("scores") or []
        if not game_id or len(teams) < 2:
            continue
        try:
            sorted_scores = sorted(teams, key=lambda x: int(x.get("score", 0)), reverse=True)
            winners[game_id] = sorted_scores[0].get("name")
        except Exception:
            continue
    return winners


def _winning_combo_rank(parlay: Dict[str, Any], winners: Dict[str, str]) -> Optional[int]:
    ranked = parlay.get("ranked_combinations") or []
    game_ids = [g.get("game_id") for g in parlay.get("games") or []]
    if not game_ids or any(gid not in winners for gid in game_ids):
        return None
    for combo in ranked:
        legs = combo.get("legs") or []
        if all(winners.get(leg.get("game_id")) == leg.get("team") for leg in legs):
            return combo.get("rank")
    return None


def grade_platform_parlay(sport_key: str, parlay: Dict[str, Any], winners: Dict[str, str]) -> Dict[str, Any]:
    rank = _winning_combo_rank(parlay, winners)
    game_ids = [g.get("game_id") for g in parlay.get("games") or []]
    if rank is None:
        return {"graded": False, "reason": "Not all final scores are available", "game_ids": game_ids}
    selected = parlay.get("selected_legs") or []
    selected_hits = [winners.get(leg.get("game_id")) == leg.get("team") for leg in selected]
    autopsy = {
        "graded": True,
        "graded_at": now_iso(),
        "winning_combo_rank": rank,
        "top_3_containment": rank <= 3,
        "top_4_containment": rank <= 4,
        "selected_leg_results": selected_hits,
        "selected_legs_hit": all(selected_hits) if selected_hits else False,
        "anchor_hits": selected_hits[:2],
        "moderate_leg_hit": selected_hits[2] if len(selected_hits) >= 3 else None,
        "winners": {gid: winners.get(gid) for gid in game_ids},
    }
    updated = dict(parlay)
    updated["status"] = "GRADED"
    updated["autopsy"] = autopsy
    _table().put_item(Item=_to_ddb(updated))
    write_learning_record(updated)
    return {"graded": True, "parlay_id": parlay.get("parlay_id"), "autopsy": autopsy}


def write_learning_record(graded_parlay: Dict[str, Any]) -> None:
    sport_key = graded_parlay.get("sport_key")
    if not sport_key:
        raise RuntimeError("Sport missing from graded parlay")
    autopsy = graded_parlay.get("autopsy") or {}
    item = {
        "PK": f"INQSI#LEARNING#{sport_key}",
        "SK": f"AUTOPSY#{autopsy.get('graded_at')}#{graded_parlay.get('parlay_id')}",
        "entity_type": "INQSI_SPORT_LEARNING_RECORD",
        "sport_key": sport_key,
        "parlay_id": graded_parlay.get("parlay_id"),
        "model_version": graded_parlay.get("model_version"),
        "winning_combo_rank": autopsy.get("winning_combo_rank"),
        "top_3_containment": autopsy.get("top_3_containment"),
        "top_4_containment": autopsy.get("top_4_containment"),
        "selected_legs_hit": autopsy.get("selected_legs_hit"),
        "anchor_hits": autopsy.get("anchor_hits"),
        "moderate_leg_hit": autopsy.get("moderate_leg_hit"),
        "created_at": now_iso(),
    }
    _table().put_item(Item=_to_ddb(item))


def run_daily_autopsy_for_sport(sport_key: str) -> Dict[str, Any]:
    if not sport_key:
        raise RuntimeError("sport_key is required; autopsy cannot mix sports")
    scores = pull_scores_for_sport(sport_key, days_from=3)
    winners = _score_winners(scores)
    open_parlays = open_platform_parlays(sport_key)
    results = [grade_platform_parlay(sport_key, p, winners) for p in open_parlays]
    return {"ok": True, "sport_key": sport_key, "open_parlays_checked": len(open_parlays), "graded_count": len([r for r in results if r.get("graded")]), "results": results}
