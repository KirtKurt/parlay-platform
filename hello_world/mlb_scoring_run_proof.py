from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Key


VERSION = "MLB-SCORING-RUN-PROOF-v1-all-games-components-persisted"
RECORD_TYPE = "mlb_scoring_run_proof"
SPORT = "mlb"
TABLE = None


def _table(override: Any = None) -> Any:
    if override is not None:
        return override
    global TABLE
    if TABLE is not None:
        return TABLE
    table_name = os.environ.get("SNAPSHOTS_TABLE", "")
    if not table_name:
        return None
    TABLE = boto3.resource("dynamodb").Table(table_name)
    return TABLE


class ScoringProofError(RuntimeError):
    """Raised when a scoring proof cannot be built or durably persisted."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(key): _ddb_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_ddb_safe(item) for item in value]
    return value


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _first_int(row: Dict[str, Any], keys: Iterable[str], default: int = 0) -> int:
    for key in keys:
        if row.get(key) not in (None, ""):
            return _safe_int(row.get(key), default)
    return default


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _index_by_date(rows: Any) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        game_date = str(row.get("game_date_et") or row.get("slate_date") or "")
        if game_date:
            indexed[game_date] = row
    return indexed


def _component_score(component: Any) -> Optional[float]:
    if not isinstance(component, dict):
        return None
    for key in ("score", "ensembleScore", "optimizedWinnerScore", "value"):
        score = _safe_float(component.get(key))
        if score is not None:
            return round(score, 4)
    return None


def _ml_component(row: Dict[str, Any]) -> Dict[str, Any]:
    overlay = row.get("mlOverlay") or row.get("mlSignalLayers") or {}
    if not isinstance(overlay, dict):
        overlay = {}
    score = _component_score(overlay)
    applied = bool(
        overlay.get("applied") is True
        or overlay.get("enabled") is True
        or overlay.get("modelApplied") is True
    )
    authority = bool(
        overlay.get("authority") is True
        or overlay.get("productionAuthority") is True
        or overlay.get("promoted") is True
    )
    return {
        "score": score,
        "applied": applied,
        "authority": authority,
        "version": overlay.get("version") or overlay.get("modelVersion"),
        "mode": overlay.get("mode") or ("SHADOW_OR_DIAGNOSTIC" if applied and not authority else "NOT_APPLIED"),
    }


def _score_component_row(row: Dict[str, Any]) -> Dict[str, Any]:
    stack = row.get("winnerStackV2") or {}
    components = stack.get("components") if isinstance(stack, dict) else {}
    components = components if isinstance(components, dict) else {}
    market = components.get("market") or row.get("marketComponent") or {}
    movement = components.get("movement") or row.get("movementComponent") or {}
    fundamentals = components.get("fundamentals") or row.get("fundamentalsComponent") or {}
    ml = _ml_component(row)

    home = row.get("homeTeam") or row.get("home_team")
    away = row.get("awayTeam") or row.get("away_team")
    final_score = _safe_float(row.get("score"))
    weights = stack.get("weights") if isinstance(stack, dict) else None
    calibration = stack.get("calibration") if isinstance(stack, dict) else None

    return {
        "gameId": row.get("gameId") or row.get("game_id"),
        "gameIdentity": row.get("gameIdentity"),
        "gameKey": row.get("gameKey") or row.get("game_key"),
        "matchup": f"{away} at {home}" if away and home else row.get("matchup"),
        "homeTeam": home,
        "awayTeam": away,
        "commenceTime": row.get("commenceTime") or row.get("commence_time"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "scores": {
            "market": _component_score(market),
            "movement": _component_score(movement),
            "fundamentals": _component_score(fundamentals),
            "ml": ml.get("score"),
            "final": round(final_score, 4) if final_score is not None else None,
        },
        "weights": copy.deepcopy(weights) if isinstance(weights, dict) else {},
        "fundamentalsApplied": bool(isinstance(fundamentals, dict) and fundamentals.get("applied") is True),
        "fundamentalsMode": fundamentals.get("mode") if isinstance(fundamentals, dict) else None,
        "ml": ml,
        "calibration": copy.deepcopy(calibration) if isinstance(calibration, dict) else {},
        "actionablePick": row.get("actionablePick") is True,
        "officialPrediction": row.get("officialPrediction") is True,
        "displayPrediction": row.get("displayPrediction") is True,
        "predictionSourcePullAt": row.get("predictionSourcePullAt"),
        "tags": list(row.get("tags") or []),
    }


def _fingerprint_material(proof: Dict[str, Any]) -> Dict[str, Any]:
    material = copy.deepcopy(proof)
    material.pop("proofFingerprint", None)
    material.pop("storage", None)
    return material


def proof_fingerprint(proof: Dict[str, Any]) -> str:
    payload = json.dumps(
        _plain(_fingerprint_material(proof)),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _blockers(
    *,
    expected: int,
    canonical: Dict[str, Any],
    movement: Dict[str, Any],
    hot_sides: Dict[str, Any],
    winners: Dict[str, Any],
    component_rows: List[Dict[str, Any]],
) -> List[str]:
    blockers: List[str] = []
    if expected <= 0:
        blockers.append("expected_game_count_invalid")
    if canonical.get("ok") is not True:
        blockers.append("canonical_pull_storage_failed")
    if movement.get("ok") is not True:
        blockers.append("movement_feature_stage_failed")
    if hot_sides.get("ok") is not True:
        blockers.append("hot_side_stage_failed")
    if winners.get("ok") is not True:
        blockers.append("winner_stage_failed")

    hot_count = _first_int(hot_sides, ("individual_prediction_count", "count", "gameCount"))
    winner_count = _first_int(winners, ("gameCount", "count", "predictionCount"))
    candidate_count = _first_int(
        winners,
        ("preLockStorageCandidateCount", "candidateCount", "gameCount", "count"),
    )
    stored_count = _first_int(
        winners,
        ("preLockStoredCount", "storedCount", "gameCount", "count"),
    )

    if hot_count != expected:
        blockers.append(f"hot_side_count_mismatch:{hot_count}!={expected}")
    if winner_count != expected:
        blockers.append(f"winner_count_mismatch:{winner_count}!={expected}")
    if winners.get("allGamesPredicted") is not True:
        blockers.append("all_games_predicted_not_true")
    if winners.get("preLockStorageComplete") is False:
        blockers.append("prelock_storage_incomplete")
    if candidate_count != expected:
        blockers.append(f"prelock_candidate_count_mismatch:{candidate_count}!={expected}")
    if stored_count != expected:
        blockers.append(f"prelock_stored_count_mismatch:{stored_count}!={expected}")
    if len(component_rows) != expected:
        blockers.append(f"component_row_count_mismatch:{len(component_rows)}!={expected}")

    for index, row in enumerate(component_rows):
        scores = row.get("scores") or {}
        if scores.get("market") is None:
            blockers.append(f"market_component_missing:{index}")
        if scores.get("movement") is None:
            blockers.append(f"movement_component_missing:{index}")
        if scores.get("fundamentals") is None:
            blockers.append(f"fundamentals_component_missing:{index}")
        if scores.get("final") is None:
            blockers.append(f"final_score_missing:{index}")
        if not row.get("predictedWinner"):
            blockers.append(f"predicted_winner_missing:{index}")

    return sorted(set(blockers))


def build_proof(payload: Dict[str, Any], game_date: str) -> Dict[str, Any]:
    manifests = _index_by_date(payload.get("provider_schedule_manifests"))
    canonical_by_date = _index_by_date(payload.get("canonical_pull_history"))
    movement_by_date = _index_by_date(payload.get("hot_movement_features"))
    hot_by_date = _index_by_date(payload.get("hot_side_predictions"))
    winner_by_date = _index_by_date(payload.get("game_winner_predictions"))

    manifest = manifests.get(game_date) or {}
    canonical = canonical_by_date.get(game_date) or {}
    movement = movement_by_date.get(game_date) or {}
    hot_sides = hot_by_date.get(game_date) or {}
    winners = winner_by_date.get(game_date) or {}
    expected = _first_int(manifest, ("gameCount", "count"))
    predictions = winners.get("predictions") or winners.get("winner_predictions") or []
    predictions = [row for row in predictions if isinstance(row, dict)]
    component_rows = [_score_component_row(row) for row in predictions]
    blockers = _blockers(
        expected=expected,
        canonical=canonical,
        movement=movement,
        hot_sides=hot_sides,
        winners=winners,
        component_rows=component_rows,
    )

    fundamentals_applied = sum(1 for row in component_rows if row.get("fundamentalsApplied"))
    ml_applied = sum(1 for row in component_rows if (row.get("ml") or {}).get("applied"))
    ml_authority = sum(1 for row in component_rows if (row.get("ml") or {}).get("authority"))
    market_count = sum(1 for row in component_rows if (row.get("scores") or {}).get("market") is not None)
    movement_count = sum(1 for row in component_rows if (row.get("scores") or {}).get("movement") is not None)
    fundamentals_count = sum(1 for row in component_rows if (row.get("scores") or {}).get("fundamentals") is not None)
    final_count = sum(1 for row in component_rows if (row.get("scores") or {}).get("final") is not None)

    slot_start = (
        canonical.get("canonicalSlotStartUtc")
        or ((canonical.get("canonicalSlot") or {}).get("slotStartUtc") if isinstance(canonical.get("canonicalSlot"), dict) else None)
        or payload.get("asof")
        or _now()
    )
    source_pull_at = (
        canonical.get("canonicalPulledAtUtc")
        or canonical.get("pulled_at")
        or payload.get("asof")
        or slot_start
    )
    proof: Dict[str, Any] = {
        "ok": not blockers,
        "status": "PASS" if not blockers else "FAIL",
        "proofType": "MLB_SCORING_RUN_PROOF",
        "version": VERSION,
        "sport": SPORT,
        "gameDateEt": game_date,
        "slotStartUtc": slot_start,
        "sourcePullAtUtc": source_pull_at,
        "run": payload.get("run"),
        "createdAtUtc": source_pull_at,
        "expectedGameCount": expected,
        "canonicalPullStored": canonical.get("ok") is True,
        "canonicalPullId": canonical.get("canonicalPullId") or canonical.get("pull_id"),
        "providerManifestFingerprint": manifest.get("fingerprint"),
        "providerManifestComplete": payload.get("providerScheduleManifestComplete") is True,
        "stageCounts": {
            "movementFeatureRowsStored": _first_int(movement, ("stored",)),
            "hotSideRows": _first_int(hot_sides, ("individual_prediction_count", "count", "gameCount")),
            "winnerRows": _first_int(winners, ("gameCount", "count", "predictionCount")),
            "preLockCandidates": _first_int(winners, ("preLockStorageCandidateCount", "candidateCount", "gameCount", "count")),
            "preLockRowsStored": _first_int(winners, ("preLockStoredCount", "storedCount", "gameCount", "count")),
            "marketComponents": market_count,
            "movementComponents": movement_count,
            "fundamentalsComponents": fundamentals_count,
            "finalScores": final_count,
        },
        "stageStatus": {
            "movementFeaturesOk": movement.get("ok") is True,
            "hotSidesOk": hot_sides.get("ok") is True,
            "winnerPredictionsOk": winners.get("ok") is True,
            "allGamesPredicted": winners.get("allGamesPredicted") is True,
            "preLockStorageComplete": winners.get("preLockStorageComplete") is not False,
        },
        "fundamentals": {
            "componentCount": fundamentals_count,
            "appliedCount": fundamentals_applied,
            "neutralOrUnavailableCount": max(expected - fundamentals_applied, 0),
            "status": (
                "FULLY_APPLIED"
                if expected > 0 and fundamentals_applied == expected
                else "PARTIALLY_APPLIED"
                if fundamentals_applied > 0
                else "NEUTRAL_OR_SOURCE_UNAVAILABLE"
            ),
        },
        "ml": {
            "componentCount": len(component_rows),
            "appliedCount": ml_applied,
            "productionAuthorityCount": ml_authority,
            "status": "PRODUCTION_AUTHORITY" if ml_authority else "SHADOW_DIAGNOSTIC_OR_NOT_APPLIED",
        },
        "componentRows": component_rows,
        "blockers": blockers,
        "policy": (
            "A canonical pull is not considered a successful scoring run until every official game has a persisted "
            "winner row plus market, movement, fundamentals, and final score components. Missing live fundamentals "
            "remain explicit and neutral; they are never fabricated."
        ),
    }
    proof["proofFingerprint"] = proof_fingerprint(proof)
    return proof


def _conditional_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    return str((response.get("Error") or {}).get("Code") or "") == "ConditionalCheckFailedException"


def _proof_key(proof: Dict[str, Any]) -> Dict[str, str]:
    return {
        "PK": f"SCORING_RUN#{SPORT}#{proof['gameDateEt']}",
        "SK": f"SLOT#{proof['slotStartUtc']}",
    }


def store_proof(proof: Dict[str, Any], table: Any = None) -> Dict[str, Any]:
    target = _table(table)
    if target is None:
        raise ScoringProofError("SNAPSHOTS_TABLE not configured for scoring proof storage")
    key = _proof_key(proof)
    item = {
        **key,
        "record_type": RECORD_TYPE,
        "version": VERSION,
        "sport": SPORT,
        "game_date_et": proof.get("gameDateEt"),
        "slot_start_utc": proof.get("slotStartUtc"),
        "status": proof.get("status"),
        "expected_game_count": proof.get("expectedGameCount"),
        "proof_fingerprint": proof.get("proofFingerprint"),
        "data": _ddb_safe(proof),
        "created_at": proof.get("createdAtUtc") or _now(),
    }
    deduped = False
    try:
        target.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
    except Exception as exc:
        if not _conditional_failure(exc):
            raise
        existing = target.get_item(Key=key, ConsistentRead=True).get("Item")
        if (
            not isinstance(existing, dict)
            or existing.get("record_type") != RECORD_TYPE
            or str(existing.get("proof_fingerprint") or "") != str(proof.get("proofFingerprint") or "")
        ):
            raise ScoringProofError("SCORING_RUN_PROOF_IMMUTABLE_COLLISION") from exc
        deduped = True
    return {
        "ok": True,
        "pk": key["PK"],
        "sk": key["SK"],
        "deduped": deduped,
        "recordType": RECORD_TYPE,
        "version": VERSION,
    }


def _decode_response(response: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    body = response.get("body")
    if isinstance(body, str):
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ScoringProofError("MLB scoring response body is not an object")
        return parsed, True
    if isinstance(body, dict):
        return dict(body), False
    raise ScoringProofError("MLB scoring response body is missing or invalid")


def _encode_response(response: Dict[str, Any], payload: Dict[str, Any], was_string: bool) -> Dict[str, Any]:
    out = dict(response)
    out["body"] = json.dumps(payload, default=str) if was_string else payload
    return out


def attach_and_store(response: Any, event: Optional[Dict[str, Any]] = None, table: Any = None) -> Any:
    """Attach immutable scoring proofs to a HOT-pull response and fail closed.

    The pull writer stores canonical odds before downstream scoring. This wrapper
    therefore creates a separate durable contract proving that every official
    game was scored and persisted for the same canonical slot.
    """
    if not isinstance(response, dict):
        return response
    try:
        payload, was_string = _decode_response(response)
    except Exception:
        return response

    if payload.get("skipped") is True or payload.get("fallback_used") is True:
        payload["scoringProofVersion"] = VERSION
        payload["scoringProofComplete"] = False
        payload["scoringProofStatus"] = "SKIPPED_NON_CANONICAL_SCORING_RESPONSE"
        return _encode_response(response, payload, was_string)

    provider_count = _safe_int(payload.get("count"), 0)
    if payload.get("ok") is not True or payload.get("live_pull_ok") is False:
        return _encode_response(response, payload, was_string)
    if provider_count == 0:
        payload["scoringProofVersion"] = VERSION
        payload["scoringProofComplete"] = True
        payload["scoringProofStatus"] = "EMPTY_VERIFIED_SLATE_NO_SCORING_REQUIRED"
        payload["scoring_proofs"] = []
        return _encode_response(response, payload, was_string)

    manifests = _index_by_date(payload.get("provider_schedule_manifests"))
    proofs: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    try:
        if not manifests:
            raise ScoringProofError("provider_schedule_manifests missing from scoring response")
        for game_date in sorted(manifests):
            proof = build_proof(payload, game_date)
            proof["storage"] = store_proof(proof, table=table)
            proofs.append(proof)
            if proof.get("ok") is not True:
                failures.append({"gameDateEt": game_date, "blockers": proof.get("blockers") or []})
    except Exception as exc:
        failures.append({"gameDateEt": None, "blockers": [f"scoring_proof_exception:{type(exc).__name__}:{exc}"]})

    payload["scoringProofVersion"] = VERSION
    payload["scoring_proofs"] = proofs
    payload["scoringProofComplete"] = not failures and bool(proofs)
    payload["scoringProofStatus"] = "PASS" if payload["scoringProofComplete"] else "FAIL"
    payload["scoringProofFailures"] = failures

    out = _encode_response(response, payload, was_string)
    if failures:
        payload["ok"] = False
        payload["error"] = "MLB_SCORING_RUN_PROOF_FAILED"
        out = _encode_response(out, payload, was_string)
        out["statusCode"] = 500
    return out


def latest_proof(game_date: str, table: Any = None) -> Dict[str, Any]:
    target = _table(table)
    if target is None:
        return {
            "ok": False,
            "sport": SPORT,
            "gameDateEt": game_date,
            "error": "SNAPSHOTS_TABLE not configured for scoring proof reads",
            "proof": None,
        }
    response = target.query(
        KeyConditionExpression=Key("PK").eq(f"SCORING_RUN#{SPORT}#{game_date}"),
        ScanIndexForward=False,
        Limit=1,
        ConsistentRead=True,
    )
    items = response.get("Items") or []
    item = items[0] if items else None
    proof = _plain((item or {}).get("data")) if isinstance(item, dict) else None
    return {
        "ok": bool(proof),
        "sport": SPORT,
        "gameDateEt": game_date,
        "recordType": RECORD_TYPE,
        "version": VERSION,
        "proof": proof,
        "message": None if proof else "No persisted MLB scoring-run proof found for this date.",
    }
