"""Read-only deploy policy for the public MLB prediction authority boundary."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
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


VERSION = "MLB-PUBLIC-PREDICTION-SMOKE-POLICY-v2-split-quarantine"


def _winner(row: Mapping[str, Any]) -> bool:
    return row.get("predictedWinner") not in (None, "")


def _status(row: Mapping[str, Any]) -> str:
    return str(
        row.get("lockStatus")
        or row.get("officialPredictionStatus")
        or ((row.get("perGameCanonicalLock") or {}).get("status"))
        or ""
    ).strip().upper()


def _status_projection(
    status_rows: Sequence[Mapping[str, Any]],
    game_count: int,
    *,
    operational_defect: bool,
) -> Dict[str, Any]:
    rows = [deepcopy(dict(row)) for row in status_rows]
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
        historical_projection = historical_lifecycle_acceptance(
            lifecycle,
            status_rows,
            int(game_count),
            now=now,
        )
        if not historical_projection:
            lifecycle = _status_projection(
                status_rows,
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
