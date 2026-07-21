from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Tuple


VERSION = "MLB-ML-CURRENT-LOCK-REVALIDATION-v1-consistent-read-current-wins"
CANONICAL_RECORD_TYPE = "mlb_immutable_locked_single_game_prediction"


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _slate(row: Dict[str, Any]) -> str:
    return str(row.get("slateDateEt") or row.get("slate_date") or "")


def _authority(row: Dict[str, Any]) -> Dict[str, Any]:
    value = row.get("canonicalLockAuthority") or {}
    return value if isinstance(value, dict) else {}


def _source_key(row: Dict[str, Any]) -> Tuple[str, str]:
    authority = _authority(row)
    return str(authority.get("sourcePk") or ""), str(authority.get("sourceSk") or "")


def _official_game_pk(row: Dict[str, Any]) -> str:
    for key in ("officialGamePk", "official_game_pk"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    for key in ("officialGameId", "official_game_id", "gameId", "game_id", "id"):
        value = str(row.get(key) or "").strip()
        if value.startswith("mlb_statsapi:"):
            return value.split(":", 1)[1]
    return ""


def _provider_game_id(row: Dict[str, Any]) -> str:
    authority = _authority(row)
    for value in (
        authority.get("providerGameId"),
        row.get("providerEventId"),
        row.get("provider_event_id"),
        row.get("providerGameId"),
        row.get("provider_game_id"),
    ):
        text = str(value or "").strip()
        if text:
            return text[len("provider:"):] if text.startswith("provider:") else text
    return ""


def _canonical_game_id(row: Dict[str, Any]) -> str:
    for key in ("gameIdentity", "gameId", "game_id", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _identity_binding_errors(current: Dict[str, Any], settled: Dict[str, Any]) -> List[str]:
    """Require a doubleheader-safe identity before joining final labels.

    Official gamePk is the strongest binding. Legacy rows without both official
    IDs must still agree on a verified provider alias or exact canonical ID;
    ordered teams alone are never sufficient because doubleheaders share them.
    """
    current_official = _official_game_pk(current)
    settled_official = _official_game_pk(settled)
    if current_official and settled_official:
        return [] if current_official == settled_official else [
            "current_official_game_pk_mismatch"
        ]

    current_provider = _provider_game_id(current)
    settled_provider = _provider_game_id(settled)
    if current_provider and settled_provider:
        return [] if current_provider == settled_provider else [
            "current_provider_game_id_mismatch"
        ]

    current_id = _canonical_game_id(current)
    settled_id = _canonical_game_id(settled)
    if current_id and settled_id and current_id == settled_id:
        return []
    return ["current_game_identity_binding_missing_or_mismatched"]


def _current_authority_errors(row: Dict[str, Any], slate: str) -> List[str]:
    authority = _authority(row)
    errors: List[str] = []
    if authority.get("verified") is not True:
        errors.append("current_canonical_authority_not_verified")
    if authority.get("consistentRead") is not True:
        errors.append("current_canonical_authority_not_consistent_read")
    if authority.get("immutableLocked") is not True:
        errors.append("current_canonical_lock_not_immutable")
    if authority.get("stageAuthorityVerified") is not True:
        errors.append("current_stage_authority_not_verified")
    if authority.get("persistedStageAuthorityValidated") is not True:
        errors.append("current_persisted_stage_authority_not_validated")
    if authority.get("recordType") != CANONICAL_RECORD_TYPE:
        errors.append("current_canonical_record_type_mismatch")
    if authority.get("sourcePk") != f"GAME_WINNERS#mlb#{slate}":
        errors.append("current_canonical_source_pk_mismatch")
    if not str(authority.get("sourceSk") or "").startswith("LOCKED#GAME#"):
        errors.append("current_canonical_source_sk_mismatch")
    if authority.get("officialAuditEligible") is not True:
        errors.append("current_canonical_official_audit_ineligible")
    if authority.get("exactLockVectorValidated") is not True:
        errors.append("current_exact_lock_vector_invalid")
    if authority.get("learningEligible") is not True:
        errors.append("current_canonical_learning_ineligible")
    return sorted(set(errors))


def _invalidate(row: Dict[str, Any], reasons: Iterable[str]) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    effective = sorted(set(str(reason) for reason in reasons if str(reason))) or [
        "current_canonical_lock_not_found"
    ]
    authority = dict(_authority(out))
    authority.update({
        "verified": False,
        "learningEligible": False,
        "officialAuditEligible": False,
        "currentRevalidationVersion": VERSION,
        "currentRevalidationPassed": False,
        "rejectionReasons": effective,
    })
    out.update({
        "status": "INVALID_CANONICAL_LOCK",
        "trainingEligible": False,
        "trainingEligibilityStatus": "INELIGIBLE",
        "canonicalLockAuthority": authority,
        "currentCanonicalLockRevalidation": {
            "version": VERSION,
            "verified": False,
            "consistentRead": True,
            "currentAuthorityWins": True,
            "rejectionReasons": effective,
        },
    })
    freeze = dict(out.get("mlFeatureFreeze") or {})
    freeze["trainingEligible"] = False
    freeze["trainingExclusionReasons"] = sorted(set(
        list(freeze.get("trainingExclusionReasons") or [])
        + ["current_canonical_lock_revalidation_failed"]
        + effective
    ))
    out["mlFeatureFreeze"] = freeze
    return out


def _rehydrate(current: Dict[str, Any], settled: Dict[str, Any]) -> Dict[str, Any]:
    slate = _slate(settled)
    authority = copy.deepcopy(_authority(current))
    source_pk, source_sk = _source_key(current)
    out = copy.deepcopy(current)
    # Only official final-result labels cross the pregame boundary. All
    # prediction fields and feature vectors come from the current lock row.
    for key in (
        "id",
        "gameKeyBase",
        "homeScore",
        "awayScore",
        "winner",
        "margin",
        "totalRuns",
        "completed",
        "correct",
        "success",
    ):
        if key in settled:
            out[key] = copy.deepcopy(settled.get(key))
    out["slateDateEt"] = slate
    out["status"] = "GRADED"
    lock_at = (
        (out.get("frozenFeatureVector") or {}).get("lockAtUtc")
        or out.get("lockedAtUtc")
        or (out.get("slatePredictionLock") or {}).get("lockAtUtc")
    )
    source_at = (
        (out.get("frozenFeatureVector") or {}).get("sourcePullAtUtc")
        or out.get("predictionSourcePullAt")
        or (out.get("slatePredictionLock") or {}).get("latestScoringPullAt")
    )
    audit = dict(out.get("lockedCardAudit") or {})
    audit.update({
        "applied": True,
        "lockAtUtc": lock_at,
        "explicitSourceAtUtc": source_at,
        "preventsLateRows": True,
        "currentCanonicalLockRevalidated": True,
        "currentCanonicalLockRevalidationVersion": VERSION,
    })
    out["lockedCardAudit"] = audit
    authority.update({
        "currentRevalidationVersion": VERSION,
        "currentRevalidationPassed": True,
        "currentRevalidationSourcePk": source_pk,
        "currentRevalidationSourceSk": source_sk,
    })
    out["canonicalLockAuthority"] = authority
    out["trainingEligible"] = True
    out["currentCanonicalLockRevalidation"] = {
        "version": VERSION,
        "verified": True,
        "consistentRead": True,
        "currentAuthorityWins": True,
        "sourcePk": source_pk,
        "sourceSk": source_sk,
        "recordType": authority.get("recordType"),
        "exactLockVectorValidated": authority.get("exactLockVectorValidated") is True,
        "learningEligible": authority.get("learningEligible") is True,
    }
    return out


def revalidate(rows: Iterable[Dict[str, Any]], audit_module: Any) -> Dict[str, Any]:
    source = [copy.deepcopy(row) for row in (rows or []) if isinstance(row, dict)]
    by_slate: Dict[str, List[Dict[str, Any]]] = {}
    for row in source:
        if row.get("status") == "GRADED" and _slate(row):
            by_slate.setdefault(_slate(row), []).append(row)

    current_by_source: Dict[Tuple[str, str], Dict[str, Any]] = {}
    slate_errors: Dict[str, str] = {}
    for slate in sorted(by_slate):
        try:
            current_rows = list(audit_module._query_predictions_for_slate(slate) or [])
        except Exception as exc:
            slate_errors[slate] = f"current_canonical_query_failed:{type(exc).__name__}:{exc}"
            continue
        for current in current_rows:
            key = _source_key(current)
            if all(key):
                current_by_source[key] = current

    output: List[Dict[str, Any]] = []
    passed = failed = 0
    for row in source:
        if row.get("status") != "GRADED":
            output.append(_invalidate(row, ["current_audit_status_not_graded"]))
            failed += 1
            continue
        slate = _slate(row)
        if slate in slate_errors:
            output.append(_invalidate(row, [slate_errors[slate]]))
            failed += 1
            continue
        key = _source_key(row)
        if not all(key):
            output.append(_invalidate(row, ["historical_canonical_source_key_missing"]))
            failed += 1
            continue
        current = current_by_source.get(key)
        if not current:
            output.append(_invalidate(row, ["current_canonical_lock_not_found_or_rejected"]))
            failed += 1
            continue
        errors = _current_authority_errors(current, slate)
        errors.extend(_identity_binding_errors(current, row))
        if _norm(current.get("homeTeam")) != _norm(row.get("homeTeam")):
            errors.append("current_home_team_mismatch")
        if _norm(current.get("awayTeam")) != _norm(row.get("awayTeam")):
            errors.append("current_away_team_mismatch")
        if errors:
            output.append(_invalidate(row, errors))
            failed += 1
            continue
        output.append(_rehydrate(current, row))
        passed += 1

    return {
        "ok": failed == 0,
        "version": VERSION,
        "inputRowCount": len(source),
        "revalidatedRowCount": passed,
        "rejectedRowCount": failed,
        "currentAuthorityWins": True,
        "consistentReadRequired": True,
        "rows": output,
        "slateErrors": slate_errors,
    }
