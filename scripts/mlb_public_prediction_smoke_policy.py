"""Read-only deploy policy for the public MLB prediction authority boundary."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
from typing import Any, Dict, Mapping, Optional, Sequence

try:
    from scripts.mlb_deploy_cutoff_smoke_policy import (
        ALLOWED_POST_CUTOFF_STATUSES,
        historical_lifecycle_acceptance,
    )
    from scripts.verify_mlb_authority_response import (
        verify_public_prediction_payload,
    )
except ImportError:  # pragma: no cover - direct script execution
    from mlb_deploy_cutoff_smoke_policy import (
        ALLOWED_POST_CUTOFF_STATUSES,
        historical_lifecycle_acceptance,
    )
    from verify_mlb_authority_response import verify_public_prediction_payload


VERSION = "MLB-PUBLIC-PREDICTION-SMOKE-POLICY-v3-sanitized-terminal-quarantine"


def _winner(row: Mapping[str, Any]) -> bool:
    return row.get("predictedWinner") not in (None, "")


def _status(row: Mapping[str, Any]) -> str:
    return str(
        row.get("lockStatus")
        or row.get("officialPredictionStatus")
        or ((row.get("perGameCanonicalLock") or {}).get("status"))
        or ""
    ).strip().upper()


_TERMINAL_STATUSES = {
    "LOCKED_NO_PREDICTION_DATA",
    "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED",
}
_TERMINAL_FORBIDDEN_FIELDS = {
    "predictedwinner",
    "predictedside",
    "selection",
    "selectionfingerprint",
    "probability",
    "price",
    "odds",
    "edge",
    "expectedvalue",
    "result",
    "label",
    "winner",
    "outcome",
}


def _terminal_forbidden_material(row: Mapping[str, Any]) -> bool:
    pending = [(row, 0)]
    visited = 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if visited > 256 or depth > 6:
            return True
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = re.sub(
                    r"[^a-z0-9]",
                    "",
                    str(key).lower(),
                )
                if (
                    normalized in _TERMINAL_FORBIDDEN_FIELDS
                    and child not in (None, "", [], {})
                ):
                    return True
                if isinstance(child, (Mapping, list, tuple)):
                    pending.append((child, depth + 1))
        elif isinstance(value, (list, tuple)):
            for child in value:
                if isinstance(child, (Mapping, list, tuple)):
                    pending.append((child, depth + 1))
    return False


def _sanitized_terminal_status_row(
    row: Mapping[str, Any],
    status: str,
) -> Dict[str, Any]:
    expected_operational_defect = bool(
        status
        == "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
    )
    required_false = (
        "lockedPrediction",
        "officialPrediction",
        "canonicalPrediction",
        "playable",
        "trainingEligible",
        "accuracyEligible",
        "wagerAllowed",
        "predictionAdopted",
        "canonicalPredictionComplete",
    )
    if (
        any(row.get(field) is not False for field in required_false)
        or row.get("blocked") is not True
        or row.get("operationalDefect")
        is not expected_operational_defect
        or _winner(row)
        or row.get("predictedSide") not in (None, "")
        or _terminal_forbidden_material(row)
    ):
        raise ValueError("terminal_status_projection_authority_invalid")
    identity = str(
        row.get("gameIdentity") or row.get("gameId") or ""
    ).strip()
    official_pk = str(row.get("officialGamePk") or "").strip()
    commence_time = str(row.get("commenceTime") or "").strip()
    if not identity or not official_pk or not commence_time:
        raise ValueError("terminal_status_projection_identity_missing")
    return {
        "gameId": row.get("gameId"),
        "gameIdentity": identity,
        "officialGamePk": official_pk,
        "commenceTime": commence_time,
        "scheduledLockAtUtc": row.get("scheduledLockAtUtc"),
        "homeTeam": row.get("homeTeam"),
        "awayTeam": row.get("awayTeam"),
        "lockStatus": status,
        "officialPredictionStatus": status,
        "predictedWinner": None,
        "predictedSide": None,
        "selectionFingerprint": None,
        "lockedPrediction": False,
        "officialPrediction": False,
        "canonicalPrediction": False,
        "playable": False,
        "blocked": True,
        "trainingEligible": False,
        "accuracyEligible": False,
        "wagerAllowed": False,
        "predictionAdopted": False,
        "operationalDefect": expected_operational_defect,
        "canonicalPredictionComplete": False,
    }


def _validated_status_rows(
    status_rows: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    rows = []
    seen = set()
    for raw in status_rows:
        if not isinstance(raw, Mapping):
            raise ValueError("status_projection_row_not_object")
        status = _status(raw)
        if status in _TERMINAL_STATUSES:
            row = _sanitized_terminal_status_row(raw, status)
        else:
            row = deepcopy(dict(raw))
        identity = str(
            row.get("gameIdentity") or row.get("gameId") or ""
        ).strip()
        if not identity or identity in seen:
            raise ValueError("status_projection_identity_invalid")
        seen.add(identity)
        rows.append(row)
    return rows


def _status_projection(
    status_rows: Sequence[Mapping[str, Any]],
    game_count: int,
    *,
    operational_defect: bool,
) -> Dict[str, Any]:
    rows = _validated_status_rows(status_rows)
    locked = sum(
        1
        for row in rows
        if row.get("lockedPrediction") is True and _winner(row)
    )
    terminal = sum(
        1
        for row in rows
        if not _winner(row) and _status(row) in ALLOWED_POST_CUTOFF_STATUSES
    )
    quarantine = sum(
        1
        for row in rows
        if (
            not _winner(row)
            and _status(row)
            == "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
        )
    )
    no_prediction_data = terminal - quarantine
    locked_statuses = locked + no_prediction_data + quarantine
    return {
        "sport": "mlb",
        "gameCount": int(game_count),
        "lockedPredictionCount": locked,
        "officialPredictionCount": locked,
        "lockedStatusCount": locked_statuses,
        "noPredictionDataCount": no_prediction_data,
        "missedLockValidPrelockQuarantineCount": quarantine,
        "terminalExcludedCount": no_prediction_data + quarantine,
        "lockStatusComplete": bool(game_count) and locked_statuses == game_count,
        "canonicalPredictionComplete": bool(game_count) and locked == game_count,
        "operationalDefect": bool(operational_defect),
        "predictions": rows,
        "authorityClosedStatusProjection": True,
        "authorityClosedStatusProjectionVersion": VERSION,
        "authorityClosedStatusProjectionPersisted": False,
    }


def reconcile_public_prediction_lifecycle(
    http_status: int,
    public_payload: Any,
    status_rows: Sequence[Mapping[str, Any]],
    game_count: int,
    *,
    now: Optional[datetime] = None,
    status_operational_defect: bool = False,
) -> Dict[str, Any]:
    """Verify public authority and select read-only lifecycle evidence.

    A qualified R7 response retains its public prediction rows. The healthy
    no-champion state must be an exact fail-closed HTTP 503 with zero public
    winners. In that state only, lifecycle checks use a detached copy of the
    lock-status rows; the projection is never persisted or published.
    """

    authority = verify_public_prediction_payload(http_status, public_payload)
    if authority.get("ok") is not True:
        raise ValueError(
            "public_prediction_authority_invalid:"
            + ",".join(authority.get("errors") or ["unknown"])
        )
    if not isinstance(public_payload, Mapping):
        raise ValueError("public_prediction_payload_not_object")

    lifecycle = deepcopy(dict(public_payload))
    historical_projection = False
    authority_closed_projection = False
    if authority.get("state") == "NO_QUALIFIED_CHAMPION":
        if len(status_rows) != int(game_count) or int(game_count) <= 0:
            raise ValueError("authority_closed_status_projection_incomplete")
        validated_status_rows = _validated_status_rows(status_rows)
        historical_projection = historical_lifecycle_acceptance(
            lifecycle,
            validated_status_rows,
            int(game_count),
            now=now,
        )
        if not historical_projection:
            lifecycle = _status_projection(
                validated_status_rows,
                int(game_count),
                operational_defect=status_operational_defect,
            )
        authority_closed_projection = True

    return {
        "ok": True,
        "version": VERSION,
        "authority": authority,
        "publicPayload": deepcopy(dict(public_payload)),
        "publicWinnerCount": authority.get("publicWinnerCount"),
        "lifecyclePayload": lifecycle,
        "historicalStatusProjectionUsed": historical_projection,
        "authorityClosedStatusProjectionUsed": authority_closed_projection,
        "statusProjectionPersisted": False,
    }
