"""Read exact persisted MLB pre-lock rows without recomputing predictions."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Tuple

from boto3.dynamodb.conditions import Key

import inqsi_pull_history as history_contract
import mlb_prediction_probability_contract_v1 as probability_contract
from mlb_slate_coverage_patch import (
    AUTHORITY_VERSION as PUBLIC_AUTHORITY_VERSION,
    game_identity,
)

VERSION = "MLB-PERSISTED-PRELOCK-PUBLIC-READ-v2-raw-identity-decimal-safe"
LIVE_RECORD_TYPE = "mlb_single_game_moneyline_prediction"
SNAPSHOT_RECORD_TYPE = "mlb_immutable_prelock_prediction_snapshot"
SNAPSHOT_VERSION = "MLB-PREGAME-PREDICTION-SNAPSHOT-v3-user-visible-platform-prelock"
SNAPSHOT_ROLE = "USER_VISIBLE_PLATFORM_PRELOCK"
PERSISTENCE_PROOF_TYPE = "DDB_LIVE_PREDICTION_PUT_SUCCESS_ACK-v1"
DISPLAY_STATUS = "PRE_LOCK_PLATFORM_PREDICTION"
DISPLAY_SURFACE = "nonOfficialPredictionDisplay"
PAYLOAD_FINGERPRINT_VERSION = history_contract.CANONICAL_PAYLOAD_FINGERPRINT_VERSION
MAX_LIVE_ROWS = 40


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _snapshot_identity(row: Mapping[str, Any]) -> str:
    """Return the raw identity used inside the immutable snapshot sort key."""

    value = str(
        row.get("gameIdentity")
        or row.get("gameId")
        or row.get("eventId")
        or row.get("providerEventId")
        or ""
    ).strip()
    return value[len("provider:") :] if value.startswith("provider:") else value


def _query_prefix(
    table: Any,
    *,
    pk: str,
    prefix: str,
    scan_forward: bool = True,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start_key = None
    while True:
        kwargs: Dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(pk)
            & Key("SK").begins_with(prefix),
            "ConsistentRead": True,
            "ScanIndexForward": scan_forward,
        }
        if limit is not None:
            kwargs["Limit"] = max(int(limit) - len(rows), 1)
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        response = table.query(**kwargs)
        values = response.get("Items") or []
        if not isinstance(values, list):
            raise RuntimeError("persisted pre-lock query returned invalid items")
        # Preserve DynamoDB Decimal values until the immutable fingerprint has
        # been checked. Conversion happens only after a pair is accepted.
        rows.extend(
            copy.deepcopy(dict(value))
            for value in values
            if isinstance(value, Mapping)
        )
        if limit is not None and len(rows) >= int(limit):
            return rows[: int(limit)]
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return rows


def _marker_errors(item: Mapping[str, Any], row: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    for field, expected in {
        "record_type": SNAPSHOT_RECORD_TYPE,
        "snapshot_version": SNAPSHOT_VERSION,
        "snapshot_role": SNAPSHOT_ROLE,
        "public_authority_version": PUBLIC_AUTHORITY_VERSION,
        "prediction_persistence_proof_type": PERSISTENCE_PROOF_TYPE,
        "display_status": DISPLAY_STATUS,
        "display_surface": DISPLAY_SURFACE,
        "prediction_payload_fingerprint_version": PAYLOAD_FINGERPRINT_VERSION,
    }.items():
        if item.get(field) != expected:
            errors.append(f"{field}_mismatch")
    for field in ("user_visible", "display_prediction", "immutable_pregame", "write_once"):
        if item.get(field) is not True:
            errors.append(f"{field}_missing")

    per_game = row.get("perGameCanonicalLock") or {}
    if not isinstance(per_game, Mapping):
        per_game = {}
        errors.append("public_authority_missing")
    for field, expected in {
        "lockedPrediction": False,
        "officialPrediction": False,
        "displayPrediction": True,
        "officialPredictionStatus": DISPLAY_STATUS,
        "displayGroup": "pre_lock_prediction",
    }.items():
        if row.get(field) != expected:
            errors.append(f"row_{field}_mismatch")
    if per_game.get("authorityVersion") != PUBLIC_AUTHORITY_VERSION:
        errors.append("row_public_authority_version_mismatch")
    if per_game.get("status") != "OPEN_PRE_LOCK":
        errors.append("row_public_status_not_open")
    if per_game.get("canonical") is not False:
        errors.append("row_public_canonical_flag_invalid")
    if "PRE_LOCK_PREDICTION" not in {
        str(value) for value in (row.get("tags") or [])
    }:
        errors.append("row_prelock_tag_missing")
    if row.get("predictedSide") not in {"home", "away"}:
        errors.append("predicted_side_invalid")
    winner = str(row.get("predictedWinner") or "").strip().lower()
    teams = {
        str(row.get("homeTeam") or "").strip().lower(),
        str(row.get("awayTeam") or "").strip().lower(),
    }
    if not winner or winner not in teams:
        errors.append("predicted_winner_not_in_matchup")
    errors.extend(probability_contract.validation_errors(dict(row)))
    return sorted(set(errors))


def _validate_pair(
    live_item: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    slate: str,
    now: datetime,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    live_row = live_item.get("data")
    snapshot_row = snapshot.get("data")
    if live_item.get("record_type") != LIVE_RECORD_TYPE:
        errors.append("live_record_type_mismatch")
    if not isinstance(live_row, Mapping):
        errors.append("live_data_missing")
        live_row = {}
    if not isinstance(snapshot_row, Mapping):
        errors.append("snapshot_data_missing")
        snapshot_row = {}

    errors.extend(_marker_errors(snapshot, snapshot_row))
    if str(snapshot.get("slate_date") or "") != slate:
        errors.append("snapshot_slate_mismatch")
    if str(snapshot_row.get("slate_date") or snapshot_row.get("slateDateEt") or "") != slate:
        errors.append("row_slate_mismatch")
    if snapshot.get("prediction_persistence_write_pk") != live_item.get("PK"):
        errors.append("live_write_pk_mismatch")
    if snapshot.get("prediction_persistence_write_sk") != live_item.get("SK"):
        errors.append("live_write_sk_mismatch")

    expected_fingerprint = history_contract.canonical_payload_fingerprint(
        dict(snapshot_row)
    )
    if snapshot.get("prediction_payload_fingerprint") != expected_fingerprint:
        errors.append("snapshot_payload_fingerprint_mismatch")
    if history_contract.canonical_payload_fingerprint(
        dict(live_row)
    ) != expected_fingerprint:
        errors.append("mutable_live_row_changed_after_snapshot")

    persisted_at = _parse_dt(snapshot.get("prediction_persisted_at_utc"))
    created_at = _parse_dt(snapshot.get("prediction_created_at_utc"))
    commence = _parse_dt(
        snapshot_row.get("commenceTime") or snapshot_row.get("commence_time")
    )
    if persisted_at is None:
        errors.append("persisted_at_invalid")
    elif persisted_at > now:
        errors.append("persisted_after_read_time")
    if created_at is None:
        errors.append("created_at_invalid")
    elif persisted_at is not None and created_at > persisted_at:
        errors.append("created_after_persistence")
    if commence is None:
        errors.append("commence_time_invalid")
    else:
        cutoff = commence - timedelta(minutes=45)
        if now >= cutoff:
            errors.append("prelock_public_window_closed")
        if persisted_at is not None and persisted_at > cutoff:
            errors.append("snapshot_persisted_after_cutoff")

    if errors:
        return None, sorted(set(errors))
    return copy.deepcopy(_plain(dict(snapshot_row))), []


def read_validated_rows(
    engine: Any,
    slate: str,
    *,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    observed = observed.astimezone(timezone.utc)
    table = getattr(getattr(engine, "history", None), "PULLS", None)
    if table is None:
        return [], {
            "ok": False,
            "version": VERSION,
            "error": "SNAPSHOTS_TABLE_not_configured",
            "productionAuthorityChanged": False,
        }

    pk = f"GAME_WINNERS#mlb#{slate}"
    live_items = _query_prefix(
        table,
        pk=pk,
        prefix="GAME#",
        limit=MAX_LIVE_ROWS + 1,
    )
    if len(live_items) > MAX_LIVE_ROWS:
        raise RuntimeError("persisted pre-lock live-row bound exceeded")

    rows: Dict[str, Dict[str, Any]] = {}
    invalid: Dict[str, List[str]] = {}
    snapshot_query_count = 0
    for live_item in live_items:
        live_row = live_item.get("data") or {}
        if not isinstance(live_row, Mapping):
            continue
        canonical_identity = game_identity(dict(live_row))
        raw_identity = _snapshot_identity(live_row)
        if not canonical_identity or not raw_identity:
            continue
        snapshots = _query_prefix(
            table,
            pk=pk,
            prefix=f"PREGAME#GAME#{raw_identity}#PERSISTED#",
            scan_forward=False,
            limit=1,
        )
        snapshot_query_count += 1
        if not snapshots:
            invalid[canonical_identity] = ["immutable_pregame_snapshot_missing"]
            continue
        row, errors = _validate_pair(
            live_item,
            snapshots[0],
            slate=slate,
            now=observed,
        )
        if errors:
            invalid[canonical_identity] = errors
            continue
        rows[canonical_identity] = row or {}

    return list(rows.values()), {
        "ok": True,
        "version": VERSION,
        "authority": "EXACT_MUTABLE_ROW_PLUS_WRITE_ONCE_PREGAME_SNAPSHOT",
        "readOnly": True,
        "recomputed": False,
        "slateDateEt": slate,
        "liveRowCount": len(live_items),
        "validatedPredictionCount": len(rows),
        "snapshotQueryCount": snapshot_query_count,
        "invalidRows": invalid,
        "invalidRowCount": len(invalid),
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
    }


def _bind_lifecycle(
    row: Dict[str, Any],
    placeholder: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    if isinstance(placeholder, Mapping):
        for field in (
            "officialGamePk",
            "officialGameId",
            "providerEventId",
            "providerCommenceTime",
            "providerStartDriftSeconds",
            "canonicalStartTimeSource",
            "commenceTime",
            "homeTeam",
            "awayTeam",
            "scheduledLockAtUtc",
            "slatePredictionLock",
            "readinessLifecycle",
            "lockReadiness",
        ):
            if placeholder.get(field) not in (None, ""):
                out[field] = copy.deepcopy(placeholder[field])
    out.update(
        {
            "locked": False,
            "canonical": False,
            "lockedPrediction": False,
            "lockStatus": "OPEN_PRE_LOCK",
            "officialPrediction": False,
            "officialPick": False,
            "displayPrediction": True,
            "officialPredictionStatus": DISPLAY_STATUS,
            "recommendationStatus": "PRE_LOCK_PREDICTION",
            "displayGroup": "pre_lock_prediction",
            "persistedPrelockPublicRead": True,
            "persistedPrelockPublicReadVersion": VERSION,
        }
    )
    per_game = dict(out.get("perGameCanonicalLock") or {})
    per_game.update(
        {
            "authorityVersion": PUBLIC_AUTHORITY_VERSION,
            "status": "OPEN_PRE_LOCK",
            "canonical": False,
        }
    )
    out["perGameCanonicalLock"] = per_game
    out["tags"] = sorted(
        set(str(value) for value in (out.get("tags") or []))
        | {"PRE_LOCK_PREDICTION", "PERSISTED_EXACT_PUBLIC_READ"}
    )
    return out


def merge_into_payload(
    engine: Any,
    slate: str,
    payload: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Replace only missing pre-lock lifecycle placeholders with stored rows."""

    out = copy.deepcopy(dict(payload or {}))
    current_rows = [
        copy.deepcopy(row)
        for row in (out.get("predictions") or [])
        if isinstance(row, Mapping)
    ]
    persisted, proof = read_validated_rows(engine, slate, now=now)
    current_by_identity = {
        game_identity(row): row for row in current_rows if game_identity(row)
    }
    persisted_by_identity = {
        game_identity(row): row for row in persisted if game_identity(row)
    }
    merged: List[Dict[str, Any]] = []
    replaced = 0
    for current in current_rows:
        identity = game_identity(current)
        if (
            current.get("lockedPrediction") is True
            or current.get("canonical") is True
            or identity not in persisted_by_identity
        ):
            merged.append(current)
            continue
        merged.append(_bind_lifecycle(persisted_by_identity[identity], current))
        replaced += 1
    for identity, row in persisted_by_identity.items():
        if identity not in current_by_identity:
            merged.append(_bind_lifecycle(row, None))
            replaced += 1

    winner_rows = [
        row for row in merged if row.get("predictedWinner") not in (None, "")
    ]
    game_count = int(out.get("gameCount") or len(merged) or 0)
    out["predictions"] = merged
    out["count"] = len(winner_rows)
    out["displayPredictionCount"] = len(winner_rows)
    out["allGamesPredicted"] = bool(game_count and len(winner_rows) == game_count)
    out["allGamesHaveDisplayedWinnerPrediction"] = out["allGamesPredicted"]
    out["persistedPrelockPublicRead"] = {
        **proof,
        "placeholderReplacementCount": replaced,
        "returnedWinnerPredictionCount": len(winner_rows),
        "manifestGameCount": game_count,
        "coverageComplete": bool(game_count and len(winner_rows) == game_count),
    }
    return out
