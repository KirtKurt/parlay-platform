"""Read-only deploy policy for the public MLB prediction authority boundary."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
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


VERSION = "MLB-PUBLIC-PREDICTION-SMOKE-POLICY-v1-authority-closed-projection"
SUCCESSOR_CONSUMER = "MLB-SUCCESSOR-DURABLE-QUALIFIED-DIRECTION-v1"


def _verify_successor_predictions(payload, status_rows):
    """Verify a separately qualified model without relabeling old engine locks."""
    digest = str(payload.get("artifactDigest") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("successor_model_digest_missing")
    if payload.get("predictionSource") != SUCCESSOR_CONSUMER or payload.get("readOnly") is not True:
        raise ValueError("successor_persisted_read_contract_missing")
    if payload.get("playabilityAuthorityEnabled") is not False:
        raise ValueError("successor_direction_must_not_grant_playability")
    roster = {str(row.get("officialGamePk") or ""): row for row in status_rows}
    rows = payload.get("predictions")
    if not isinstance(rows, list) or payload.get("winner_predictions") != rows or payload.get("count") != len(rows):
        raise ValueError("successor_prediction_count_mismatch")
    seen = set()
    def parsed(value):
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None: raise ValueError("successor_timestamp_timezone_missing")
        return result
    for row in rows:
        pk = str(row.get("officialGamePk") or "")
        if not pk or pk in seen or pk not in roster or row.get("artifactDigest") != digest:
            raise ValueError("successor_prediction_identity_mismatch")
        seen.add(pk)
        if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("inputFingerprint") or "")):
            raise ValueError("successor_input_fingerprint_missing")
        home, away = row.get("homeProbability"), row.get("awayProbability")
        if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 < p < 1 for p in (home, away)) or abs(home + away - 1) > 1e-9:
            raise ValueError("successor_probability_pair_invalid")
        side = "home" if home >= .5 else "away"
        if row.get("predictedSide") != side or row.get("predictedWinner") != row.get(side+"Team"):
            raise ValueError("successor_direction_probability_mismatch")
        original = roster[pk]
        for field in ("homeTeam", "awayTeam"):
            if original.get(field) and row.get(field) != original.get(field):
                raise ValueError("successor_team_identity_mismatch")
        start = parsed(row.get("commenceTime"))
        original_start = original.get("commenceTime") or original.get("commence_time")
        if original_start and start != parsed(original_start):
            raise ValueError("successor_start_identity_mismatch")
        if not parsed(row.get("featureLockAtUtc")) <= parsed(row.get("capturedAtUtc")) < start:
            raise ValueError("successor_prediction_not_captured_before_start")
        if row.get("automaticWagerAllowed") is not False or row.get("playabilityAuthorityEnabled") is not False or row.get("immutable") is not True:
            raise ValueError("successor_prediction_authority_invalid")


def qualified_champion_readiness_blockers(
    winner_results: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Keep healthy, authority-closed observations ineligible for promotion.

    These states come from the observer's validated public authority response.
    Complete internal scoring or storage cannot supply champion authority.
    """
    states = {row.get("publicAuthorityState") for row in winner_results}
    if states == {"QUALIFIED_R7_CHAMPION"}:
        return []
    blockers = []
    if "NO_QUALIFIED_CHAMPION" in states:
        blockers.append("no_qualified_champion")
    if not states or states - {"QUALIFIED_R7_CHAMPION", "NO_QUALIFIED_CHAMPION"}:
        blockers.append("qualified_champion_authority_not_verified")
    return blockers


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
    locked_statuses = locked + terminal
    return {
        "sport": "mlb",
        "gameCount": int(game_count),
        "lockedPredictionCount": locked,
        "officialPredictionCount": locked,
        "lockedStatusCount": locked_statuses,
        "noPredictionDataCount": terminal,
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
    successor_projection = False
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
    elif public_payload.get("successorConsumerVersion") == SUCCESSOR_CONSUMER:
        if len(status_rows) != int(game_count) or len({str(r.get("officialGamePk") or "") for r in status_rows}) != int(game_count):
            raise ValueError("successor_status_roster_incomplete")
        _verify_successor_predictions(public_payload, status_rows)
        # Public predictions retain their own namespace and outcome direction.
        # Existing lifecycle checks inspect a detached copy of the old lock
        # records; neither set is substituted into storage or the public API.
        lifecycle = _status_projection(status_rows, int(game_count), operational_defect=status_operational_defect)
        lifecycle["authorityClosedStatusProjection"] = False
        lifecycle["successorStatusProjection"] = True
        successor_projection = True

    return {
        "ok": True,
        "version": VERSION,
        "authority": authority,
        "publicPayload": deepcopy(dict(public_payload)),
        "publicWinnerCount": authority.get("publicWinnerCount"),
        "lifecyclePayload": lifecycle,
        "historicalStatusProjectionUsed": historical_projection,
        "authorityClosedStatusProjectionUsed": authority_closed_projection,
        "successorPredictionAuthorityVerified": successor_projection,
        "statusProjectionPersisted": False,
    }
