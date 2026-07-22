from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from boto3.dynamodb.conditions import Key

import inqsi_pull_history as history_contract

VERSION = "MLB-SLATE-COVERAGE-v4-immutable-provider-manifest-authority"
AUTHORITY_VERSION = "MLB-LAST-PRELOCK-PROMOTION-AUTHORITY-v1-canonical-read-overlay"
CANONICAL_RECORD_TYPE = "mlb_immutable_locked_single_game_prediction"
LOCK_OUTCOME_RECORD_TYPE = "mlb_immutable_per_game_lock_outcome"
LOCK_OUTCOME_VERSION = "MLB-LOCK-OUTCOME-v1-explicit-terminal-status"
PLAYABILITY_RECORD_TYPE = "mlb_immutable_playability_assessment"
PLAYABILITY_VERSION = "MLB-PLAYABILITY-ASSESSMENT-v1-immutable-selection-bound"
PLAYABILITY_CHECKPOINTS = (
    "T_MINUS_30",
    "T_MINUS_15",
    "EVENT_GAME1_PENDING",
    "EVENT_GAME1_FINAL",
)
SCHEDULED_PLAYABILITY_CHECKPOINTS = ((30, "T_MINUS_30"), (15, "T_MINUS_15"))
READINESS_RECORD_TYPE = "mlb_per_game_lock_readiness_checkpoint"
READINESS_VERSION = "MLB-LOCK-READINESS-v1-tminus60-tminus50"
SCHEDULED_READINESS_CHECKPOINTS = ((60, "T_MINUS_60"), (50, "T_MINUS_50"))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _norm_team(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _start_key(game: Dict[str, Any]) -> str:
    dt = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return dt.isoformat() if dt else str(game.get("commence_time") or game.get("commenceTime") or "unknown")


def game_identity(game: Dict[str, Any]) -> str:
    """Return a stable game identity that never collapses doubleheaders."""
    provider_id = game.get("game_id") or game.get("gameId") or game.get("id") or game.get("gameIdentity")
    if provider_id:
        value = str(provider_id)
        if value.startswith(("provider:", "key:", "teams:")):
            return value
        return f"provider:{value}"
    game_key = str(game.get("game_key") or game.get("gameKey") or "").strip()
    start = _start_key(game)
    if game_key:
        return f"key:{game_key}|start:{start}"
    away = _norm_team(game.get("away_team") or game.get("awayTeam"))
    home = _norm_team(game.get("home_team") or game.get("homeTeam"))
    return f"teams:{away}|{home}|start:{start}"


def _raw_game_identity(game: Dict[str, Any]) -> str:
    identity = game_identity(game)
    return identity[len("provider:"):] if identity.startswith("provider:") else identity


def _official_game_pk(game: Dict[str, Any]) -> str:
    return str(game.get("official_game_pk") or game.get("officialGamePk") or "")


def _same_manifest_game(reference: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    reference_pk = _official_game_pk(reference)
    candidate_pk = _official_game_pk(candidate)
    if reference_pk and candidate_pk:
        return reference_pk == candidate_pk
    return game_identity(reference) == game_identity(candidate)


def _bind_row_to_manifest(
    row: Dict[str, Any],
    manifest_game: Dict[str, Any],
) -> Dict[str, Any]:
    """Expose a provider-ID prediction under the durable official roster ID."""
    out = copy.deepcopy(row)
    has_source_identity = any(
        row.get(key) not in (None, "")
        for key in ("game_id", "gameId", "id", "gameIdentity", "game_key", "gameKey")
    )
    source_identity = _raw_game_identity(row) if has_source_identity else ""
    canonical_identity = _raw_game_identity(manifest_game)
    if source_identity and source_identity != canonical_identity:
        out.setdefault("sourcePredictionGameId", source_identity)
        out.setdefault(
            "sourcePredictionGameIdentity",
            row.get("gameIdentity") or source_identity,
        )
        if not out.get("providerEventId") and not source_identity.startswith("mlb_statsapi:"):
            out["providerEventId"] = source_identity
    out.update({
        "gameId": canonical_identity,
        "gameIdentity": canonical_identity,
        "officialGamePk": (
            manifest_game.get("official_game_pk")
            or manifest_game.get("officialGamePk")
            or row.get("officialGamePk")
        ),
        "officialGameId": (
            manifest_game.get("official_game_id")
            or manifest_game.get("officialGameId")
            or row.get("officialGameId")
        ),
        "providerEventId": (
            out.get("providerEventId")
            or manifest_game.get("provider_event_id")
            or manifest_game.get("providerEventId")
        ),
        "providerCommenceTime": (
            out.get("providerCommenceTime")
            or manifest_game.get("provider_commence_time")
            or manifest_game.get("providerCommenceTime")
        ),
        "providerStartDriftSeconds": (
            out.get("providerStartDriftSeconds")
            if out.get("providerStartDriftSeconds") is not None
            else manifest_game.get("provider_start_drift_seconds")
            if manifest_game.get("provider_start_drift_seconds") is not None
            else manifest_game.get("providerStartDriftSeconds")
        ),
        "canonicalStartTimeSource": (
            manifest_game.get("canonical_start_time_source")
            or manifest_game.get("canonicalStartTimeSource")
            or out.get("canonicalStartTimeSource")
        ),
        "commenceTime": (
            manifest_game.get("commence_time")
            or manifest_game.get("commenceTime")
            or out.get("commenceTime")
        ),
        "homeTeam": (
            manifest_game.get("home_team")
            or manifest_game.get("homeTeam")
            or out.get("homeTeam")
        ),
        "awayTeam": (
            manifest_game.get("away_team")
            or manifest_game.get("awayTeam")
            or out.get("awayTeam")
        ),
    })
    return out


def _is_doubleheader_game_two(
    manifest: List[Dict[str, Any]],
    game: Dict[str, Any],
) -> bool:
    teams = frozenset({
        _norm_team(game.get("home_team") or game.get("homeTeam")),
        _norm_team(game.get("away_team") or game.get("awayTeam")),
    })
    matches = [
        entry
        for entry in manifest
        if frozenset({
            _norm_team(entry.get("home_team") or entry.get("homeTeam")),
            _norm_team(entry.get("away_team") or entry.get("awayTeam")),
        }) == teams
    ]
    matches.sort(key=lambda entry: (_start_key(entry), game_identity(entry)))
    return bool(
        len(matches) >= 2
        and game_identity(game) == game_identity(matches[-1])
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _status_digest(game: Dict[str, Any]) -> str:
    return hashlib.sha256(game_identity(game).encode("utf-8")).hexdigest()


def _status_pk(lock_module: Any, slate: str) -> str:
    builder = getattr(lock_module, "_lock_pk", None)
    return builder(slate) if callable(builder) else f"LOCKED_PICKS#mlb#{slate}"


def _status_record(
    module: Any,
    lock_module: Any,
    slate: str,
    sk: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    table = getattr(getattr(module, "history", None), "PULLS", None)
    if table is None:
        return None, "status_table_not_configured"
    try:
        item = table.get_item(
            Key={"PK": _status_pk(lock_module, slate), "SK": sk},
            ConsistentRead=True,
        ).get("Item")
        return (item if isinstance(item, dict) else None), None
    except Exception as exc:
        return None, f"status_read_failed:{type(exc).__name__}:{exc}"


def _record_fingerprint(item: Dict[str, Any], field: str) -> str:
    material = {
        str(key): value
        for key, value in _plain(item).items()
        if key != field
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _terminal_outcome_for_public(
    module: Any,
    lock_module: Any,
    slate: str,
    game: Dict[str, Any],
    manifest_authority: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    sk = f"PER_GAME_LOCK_OUTCOME#TMINUS45#{_status_digest(game)}"
    item, read_error = _status_record(module, lock_module, slate, sk)
    if read_error:
        return None, [read_error]
    if not item:
        return None, []
    errors: List[str] = []
    if item.get("record_type") != LOCK_OUTCOME_RECORD_TYPE or item.get("version") != LOCK_OUTCOME_VERSION:
        errors.append("lock_outcome_contract_mismatch")
    if str(item.get("slate_date") or "") != slate or str(item.get("game_identity") or "") != game_identity(game):
        errors.append("lock_outcome_identity_mismatch")
    if item.get("lock_outcome_fingerprint") != _record_fingerprint(item, "lock_outcome_fingerprint"):
        errors.append("lock_outcome_fingerprint_mismatch")
    if item.get("lock_status") != "LOCKED_NO_PREDICTION_DATA":
        errors.append("lock_outcome_status_mismatch")
    if str(item.get("provider_manifest_fingerprint") or "") != str(manifest_authority.get("providerManifestFingerprint") or ""):
        errors.append("lock_outcome_manifest_fingerprint_mismatch")
    if str(item.get("provider_manifest_pk") or "") != str(manifest_authority.get("providerManifestPk") or ""):
        errors.append("lock_outcome_manifest_pk_mismatch")
    if str(item.get("provider_manifest_sk") or "") != str(manifest_authority.get("providerManifestSk") or ""):
        errors.append("lock_outcome_manifest_sk_mismatch")
    try:
        expected_count = int(manifest_authority.get("verifiedFullSlateGameCount"))
        if int(item.get("manifest_game_count")) != expected_count:
            errors.append("lock_outcome_manifest_count_mismatch")
    except Exception:
        errors.append("lock_outcome_manifest_count_invalid")
    return (copy.deepcopy(item) if not errors else None), sorted(set(errors))


def _probability_equal(left: Any, right: Any) -> bool:
    if left in (None, "") or right in (None, ""):
        return left in (None, "") and right in (None, "")
    try:
        return abs(Decimal(str(left)) - Decimal(str(right))) <= Decimal("0.000001")
    except Exception:
        return str(left) == str(right)


def _canonical_probability(row: Dict[str, Any]) -> Any:
    for field in ("teamWinProbabilityPct", "winProbabilityPct"):
        if row.get(field) not in (None, ""):
            return row.get(field)
    return None


def _readiness_for_public(
    module: Any,
    lock_module: Any,
    slate: str,
    game: Dict[str, Any],
    now: datetime,
) -> Dict[str, Any]:
    """Read readiness diagnostics without making them lock prerequisites."""
    start = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    required_checkpoint: Optional[str] = None
    if start:
        for minutes, checkpoint in SCHEDULED_READINESS_CHECKPOINTS:
            if now >= start - timedelta(minutes=minutes):
                required_checkpoint = checkpoint

    public: Dict[str, Dict[str, Any]] = {}
    validation_errors: List[str] = []
    for minutes, checkpoint in SCHEDULED_READINESS_CHECKPOINTS:
        sk = f"PER_GAME_READINESS#TMINUS{minutes}#{_status_digest(game)}"
        item, read_error = _status_record(module, lock_module, slate, sk)
        key = f"tMinus{minutes}"
        row_errors: List[str] = []
        if read_error:
            row_errors.append(str(read_error))
        elif isinstance(item, dict):
            if item.get("record_type") != READINESS_RECORD_TYPE or item.get("version") != READINESS_VERSION:
                row_errors.append("contract_mismatch")
            if str(item.get("slate_date") or "") != slate or str(item.get("game_identity") or "") != game_identity(game):
                row_errors.append("identity_mismatch")
            if str(item.get("checkpoint") or "") != checkpoint:
                row_errors.append("checkpoint_mismatch")
            if item.get("write_once") is not True:
                row_errors.append("write_once_contract_mismatch")
            if _parse_dt(item.get("scheduled_at_utc")) is None:
                row_errors.append("scheduled_at_invalid")
            if _parse_dt(item.get("evaluated_at_utc")) is None:
                row_errors.append("evaluated_at_invalid")
            if item.get("readiness_fingerprint") != _record_fingerprint(item, "readiness_fingerprint"):
                row_errors.append("readiness_fingerprint_mismatch")
        elif checkpoint == required_checkpoint or (
            start and now >= start - timedelta(minutes=minutes)
        ):
            row_errors.append("required_checkpoint_missing")

        if row_errors:
            validation_errors.extend(f"{checkpoint}:{error}" for error in row_errors)
        valid = isinstance(item, dict) and not row_errors
        public[key] = {
            "recorded": valid,
            "status": item.get("status") if valid else None,
            "timingStatus": item.get("checkpoint_timing_status") if valid else None,
            "scheduledAtUtc": item.get("scheduled_at_utc") if valid else None,
            "evaluatedAtUtc": item.get("evaluated_at_utc") if valid else None,
            "candidateReady": item.get("candidate_ready") is True if valid else False,
            "blockingReasons": list(item.get("blocking_reasons") or []) if valid else [],
            "validationErrors": sorted(set(row_errors)),
        }

    if not start:
        validation_errors.append("GAME:commence_time_invalid")
    return {
        "checkpoints": public,
        "requiredCheckpoint": required_checkpoint,
        "requiredCheckpointDue": required_checkpoint is not None,
        "validationErrors": sorted(set(validation_errors)),
    }


def _assessment_errors(
    item: Dict[str, Any],
    *,
    checkpoint: str,
    slate: str,
    game: Dict[str, Any],
    locked_row: Dict[str, Any],
) -> List[str]:
    errors: List[str] = []
    if item.get("record_type") != PLAYABILITY_RECORD_TYPE or item.get("version") != PLAYABILITY_VERSION:
        errors.append("contract_mismatch")
    if str(item.get("slate_date") or "") != slate or str(item.get("game_identity") or "") != game_identity(game):
        errors.append("identity_mismatch")
    if str(item.get("checkpoint") or "") != checkpoint:
        errors.append("checkpoint_mismatch")
    if item.get("selection_rewrite_allowed") is not False:
        errors.append("selection_rewrite_contract_mismatch")
    selection_fingerprint = str(locked_row.get("lastPrelockSelectionFingerprint") or "")
    if not selection_fingerprint:
        errors.append("canonical_selection_fingerprint_missing")
    elif str(item.get("canonical_selection_fingerprint") or "") != selection_fingerprint:
        errors.append("selection_fingerprint_mismatch")
    if _norm_team(item.get("canonical_predicted_winner")) != _norm_team(locked_row.get("predictedWinner")):
        errors.append("canonical_winner_mismatch")
    if str(item.get("canonical_predicted_side") or "") != str(locked_row.get("predictedSide") or ""):
        errors.append("canonical_side_mismatch")
    if not _probability_equal(item.get("canonical_probability_pct"), _canonical_probability(locked_row)):
        errors.append("canonical_probability_mismatch")
    if _parse_dt(item.get("evaluated_at_utc")) is None:
        errors.append("evaluated_at_invalid")
    playable = item.get("playable") is True
    blocked = item.get("blocked") is True
    if playable == blocked:
        errors.append("playable_blocked_contract_mismatch")
    expected_status = "PLAYABLE" if playable else "BLOCKED"
    if str(item.get("status") or "") != expected_status:
        errors.append("status_mismatch")
    if item.get("assessment_fingerprint") != _record_fingerprint(item, "assessment_fingerprint"):
        errors.append("assessment_fingerprint_mismatch")
    return sorted(set(errors))


def resolve_playability_lifecycle(
    *,
    slate: str,
    game: Dict[str, Any],
    locked_row: Dict[str, Any],
    now: datetime,
    record_reader: Callable[[str], Tuple[Optional[Dict[str, Any]], Optional[str]]],
    event_pending_required: bool = False,
) -> Dict[str, Any]:
    """Resolve the release-only lifecycle without ever changing the winner.

    The newest scheduled checkpoint that is currently due is mandatory.  A
    valid event-driven doubleheader assessment may supersede it, but cannot
    hide a missing or invalid scheduled assessment.  Older scheduled records
    remain diagnostic once a newer checkpoint is due.
    """
    now = now.astimezone(timezone.utc)
    start = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    required_checkpoint: Optional[str] = None
    if start:
        for minutes, checkpoint in SCHEDULED_PLAYABILITY_CHECKPOINTS:
            if now >= start - timedelta(minutes=minutes):
                required_checkpoint = checkpoint

    records: Dict[str, Optional[Dict[str, Any]]] = {}
    read_errors: Dict[str, str] = {}
    record_errors: Dict[str, List[str]] = {}
    valid: Dict[str, Dict[str, Any]] = {}
    for checkpoint in PLAYABILITY_CHECKPOINTS:
        item, read_error = record_reader(checkpoint)
        records[checkpoint] = copy.deepcopy(item) if isinstance(item, dict) else None
        if read_error:
            read_errors[checkpoint] = str(read_error)
            continue
        if not isinstance(item, dict):
            continue
        errors = _assessment_errors(
            item,
            checkpoint=checkpoint,
            slate=slate,
            game=game,
            locked_row=locked_row,
        )
        if errors:
            record_errors[checkpoint] = errors
        else:
            valid[checkpoint] = copy.deepcopy(item)

    release_errors: List[str] = []
    if not start:
        release_errors.append("GAME:commence_time_invalid")
    if required_checkpoint:
        if required_checkpoint in read_errors:
            release_errors.append(f"{required_checkpoint}:{read_errors[required_checkpoint]}")
        elif records.get(required_checkpoint) is None:
            release_errors.append(f"{required_checkpoint}:required_assessment_missing")
        else:
            release_errors.extend(
                f"{required_checkpoint}:{error}"
                for error in record_errors.get(required_checkpoint, [])
            )

    # Game 2 receives an immediate immutable pending assessment at lock.  It
    # keeps release blocked before T-30.  A final Game 1 event is optional until
    # it exists, but once present it is a claimed newer release decision and
    # therefore must validate or release fails closed.
    pending_checkpoint = "EVENT_GAME1_PENDING"
    final_checkpoint = "EVENT_GAME1_FINAL"
    final_valid = final_checkpoint in valid
    pending_required_now = bool(
        event_pending_required and not required_checkpoint and not final_valid
    )
    if pending_required_now:
        if pending_checkpoint in read_errors:
            release_errors.append(f"{pending_checkpoint}:{read_errors[pending_checkpoint]}")
        elif records.get(pending_checkpoint) is None:
            release_errors.append(f"{pending_checkpoint}:required_assessment_missing")
        else:
            release_errors.extend(
                f"{pending_checkpoint}:{error}"
                for error in record_errors.get(pending_checkpoint, [])
            )
    if final_checkpoint in read_errors and (required_checkpoint or event_pending_required):
        release_errors.append(f"{final_checkpoint}:{read_errors[final_checkpoint]}")
    elif records.get(final_checkpoint) is not None:
        release_errors.extend(
            f"{final_checkpoint}:{error}"
            for error in record_errors.get(final_checkpoint, [])
        )

    candidates: List[Dict[str, Any]] = []
    if required_checkpoint in valid:
        candidates.append(valid[required_checkpoint])
    for event_checkpoint in (pending_checkpoint, final_checkpoint):
        if event_checkpoint in valid:
            candidates.append(valid[event_checkpoint])
    assessment = max(
        candidates,
        key=lambda item: _parse_dt(item.get("evaluated_at_utc")) or datetime.min.replace(tzinfo=timezone.utc),
        default=None,
    )

    historical_errors: List[str] = []
    for checkpoint in PLAYABILITY_CHECKPOINTS:
        if checkpoint == required_checkpoint or checkpoint == final_checkpoint:
            continue
        if checkpoint == pending_checkpoint and pending_required_now:
            continue
        if checkpoint in read_errors:
            historical_errors.append(f"{checkpoint}:{read_errors[checkpoint]}")
        historical_errors.extend(
            f"{checkpoint}:{error}"
            for error in record_errors.get(checkpoint, [])
        )

    base_playable = bool(
        locked_row.get("playable") is True
        or locked_row.get("playablePick") is True
        or locked_row.get("actionablePick") is True
    )
    base_reasons = {
        str(reason)
        for field in (
            "playabilityBlockReasons",
            "releaseBlockReasons",
            "wagerReleaseBlockReasons",
            "blockedReasons",
        )
        for reason in (locked_row.get(field) or [])
        if reason
    }
    if assessment and not release_errors:
        playable = assessment.get("playable") is True
        reasons = {str(reason) for reason in (assessment.get("reasons") or []) if reason}
        status = str(assessment.get("status") or ("PLAYABLE" if playable else "BLOCKED"))
    elif release_errors:
        playable = False
        reasons = base_reasons | {
            f"PLAYABILITY_ASSESSMENT_INVALID:{error}" for error in release_errors
        }
        status = "BLOCKED"
    else:
        playable = base_playable
        reasons = base_reasons
        status = "PLAYABLE" if playable else "BLOCKED"

    return {
        "assessment": copy.deepcopy(assessment) if assessment else None,
        "requiredCheckpoint": required_checkpoint,
        "requiredCheckpointDue": required_checkpoint is not None,
        "eventPendingRequired": event_pending_required,
        "playable": playable,
        "blocked": not playable,
        "status": status,
        "reasons": sorted(reasons),
        "validationErrors": sorted(set(release_errors)),
        "historicalValidationErrors": sorted(set(historical_errors)),
    }


def _playability_for_public(
    module: Any,
    lock_module: Any,
    slate: str,
    game: Dict[str, Any],
    locked_row: Dict[str, Any],
    now: datetime,
    event_pending_required: bool = False,
) -> Dict[str, Any]:
    def read(checkpoint: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        sk = f"PER_GAME_PLAYABILITY#{checkpoint}#{_status_digest(game)}"
        return _status_record(module, lock_module, slate, sk)

    return resolve_playability_lifecycle(
        slate=slate,
        game=game,
        locked_row=locked_row,
        now=now,
        record_reader=read,
        event_pending_required=event_pending_required,
    )


def _overlay_playability(
    row: Dict[str, Any],
    lifecycle: Dict[str, Any],
) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    assessment = lifecycle.get("assessment")
    validation_errors = list(lifecycle.get("validationErrors") or [])
    playable = lifecycle.get("playable") is True
    reasons = list(lifecycle.get("reasons") or [])
    out.update({
        "playable": playable,
        "playablePick": playable,
        "actionablePick": playable,
        "blocked": not playable,
        "releaseBlocked": not playable,
        "wagerReleaseBlocked": not playable,
        "playabilityStatus": lifecycle.get("status") or ("PLAYABLE" if playable else "BLOCKED"),
        "playabilityBlockReasons": reasons,
        "releaseBlockReasons": reasons,
        "playabilityAssessment": copy.deepcopy(assessment) if assessment else None,
        "playabilityAssessmentValidationErrors": validation_errors,
        "historicalPlayabilityAssessmentValidationErrors": list(
            lifecycle.get("historicalValidationErrors") or []
        ),
        "requiredPlayabilityCheckpoint": lifecycle.get("requiredCheckpoint"),
        "requiredPlayabilityCheckpointDue": lifecycle.get("requiredCheckpointDue") is True,
        "eventPlayabilityAssessmentRequired": lifecycle.get("eventPendingRequired") is True,
    })
    tags = {str(tag) for tag in (out.get("tags") or [])}
    if playable:
        tags.update({"ACTIONABLE_PICK", "PLAYABLE_PREDICTION"})
        tags.difference_update({"NOT_PLAYABLE", "RELEASE_BLOCKED", "WAGER_RELEASE_BLOCKED"})
    else:
        tags.update({"NOT_PLAYABLE", "RELEASE_BLOCKED", "WAGER_RELEASE_BLOCKED"})
        tags.difference_update({"ACTIONABLE_PICK", "PLAYABLE_PREDICTION"})
    out["tags"] = sorted(tags)
    return out


def _overlay_readiness(
    row: Dict[str, Any],
    lifecycle: Dict[str, Any],
) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    out.update({
        "readiness": copy.deepcopy(lifecycle.get("checkpoints") or {}),
        "requiredReadinessCheckpoint": lifecycle.get("requiredCheckpoint"),
        "requiredReadinessCheckpointDue": lifecycle.get("requiredCheckpointDue") is True,
        "readinessValidationErrors": list(lifecycle.get("validationErrors") or []),
    })
    return out


def _overlay_terminal_outcome(row: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    reasons = sorted(set(
        list(outcome.get("playability_block_reasons") or [])
        + list(outcome.get("reasons") or [])
    ))
    out.update({
        "predictedWinner": None,
        "predictedSide": None,
        "opponent": None,
        "winProbability": None,
        "winProbabilityPct": None,
        "teamWinProbabilityPct": None,
        "confidenceTier": None,
        "score": None,
        "lockedPrediction": False,
        "officialPrediction": False,
        "officialPick": False,
        "lockOutcomeRecorded": True,
        "lockStatus": "LOCKED_NO_PREDICTION_DATA",
        "officialPredictionStatus": "LOCKED_NO_PREDICTION_DATA",
        "officialPredictionReason": "no_valid_immutable_pregame_prediction_at_tminus45",
        "recommendationStatus": "LOCKED_NO_PREDICTION_DATA",
        "displayGroup": "lock_outcome_no_prediction_data",
        "playable": False,
        "playablePick": False,
        "actionablePick": False,
        "blocked": True,
        "releaseBlocked": True,
        "wagerReleaseBlocked": True,
        "playabilityStatus": "BLOCKED",
        "playabilityBlockReasons": reasons,
        "trainingEligible": False,
        "trainingEligibilityStatus": "INELIGIBLE",
        "trainingExclusionReasons": list(outcome.get("training_exclusion_reasons") or []),
        "terminalLockOutcome": copy.deepcopy(outcome),
        "accuracyTargetEligible": False,
        "officialAccuracyEligible": False,
        "settlementEligible": False,
    })
    tags = {str(tag) for tag in (out.get("tags") or [])}
    tags.update({"LOCKED_NO_PREDICTION_DATA", "NOT_PLAYABLE", "RELEASE_BLOCKED"})
    tags.difference_update({"FINAL_LOCKED", "OFFICIAL_PREDICTION", "OFFICIAL_LOCKED_PREDICTION", "ACTIONABLE_PICK", "PLAYABLE_PREDICTION"})
    out["tags"] = sorted(tags)
    return out


def _latest_games(lock_module: Any, pulls: List[Dict[str, Any]], slate: str) -> List[Dict[str, Any]]:
    resolver = getattr(history_contract, "verified_full_slate_manifest", None)
    if callable(resolver):
        resolved = resolver(pulls, slate)
        return sorted(
            [
                game
                for game in (resolved.get("games") or [])
                if lock_module._game_day(game) == slate
            ],
            key=lock_module._game_sort,
        )

    # Compatibility fallback for legacy injected adapters.
    by_identity: Dict[str, Tuple[datetime, Dict[str, Any]]] = {}
    for pull in pulls or []:
        pulled_at = lock_module._pull_dt(pull) or datetime.min.replace(tzinfo=timezone.utc)
        for game in pull.get("games") or []:
            if lock_module._game_day(game) != slate:
                continue
            identity = game_identity(game)
            current = by_identity.get(identity)
            if current is None or pulled_at >= current[0]:
                by_identity[identity] = (pulled_at, game)
    return sorted((item[1] for item in by_identity.values()), key=lock_module._game_sort)


def _provider_manifest_for_public(
    module: Any,
    lock_module: Any,
    pulls: List[Dict[str, Any]],
    slate: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Read the verified durable full-slate schedule.

    Public completeness must never be inferred from the odds-bearing ``games``
    array. Official MLB exact-date authority preserves games without provider
    events, while the durable resolver retains the maximum prestart roster
    through same-day migration. Immutable readback is required before status
    uses the roster.
    """
    if not pulls:
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_MISSING:NO_PULL_HISTORY")
    resolver = getattr(getattr(module, "history", None), "verified_full_slate_manifest", None)
    if not callable(resolver):
        resolver = getattr(history_contract, "verified_full_slate_manifest", None)
    if not callable(resolver):
        raise RuntimeError("MLB_VERIFIED_FULL_SLATE_MANIFEST_RESOLVER_UNAVAILABLE")
    resolved = resolver(pulls, slate)
    full_pull = resolved.get("fullAuthorityPull")
    if not isinstance(full_pull, dict):
        raise RuntimeError("MLB_VERIFIED_FULL_SLATE_MANIFEST_INVALID:full_authority_missing")

    reader = getattr(getattr(module, "history", None), "provider_manifest_games_for_lock", None)
    if not callable(reader):
        reader = getattr(history_contract, "provider_manifest_games_for_lock", None)
    if not callable(reader):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_VALIDATOR_UNAVAILABLE")
    games = reader(full_pull, slate)
    if not isinstance(games, list):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:games_not_list")
    resolved_games = list(resolved.get("games") or [])
    if games != resolved_games:
        raise RuntimeError("MLB_VERIFIED_FULL_SLATE_MANIFEST_INVALID:resolved_games_mismatch")

    manifest = full_pull.get("provider_schedule_manifest")
    binding = full_pull.get("provider_manifest_binding")
    if not isinstance(manifest, dict) or not isinstance(binding, dict):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:manifest_or_binding_missing")
    declared_games = manifest.get("games")
    if not isinstance(declared_games, list):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:declared_games_not_list")
    try:
        declared_count = int(manifest.get("gameCount"))
    except Exception:
        declared_count = -1
    if declared_count != len(games) or len(declared_games) != len(games):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:game_count_mismatch")

    returned_ids = [game_identity(game) for game in games]
    declared_ids = [game_identity(game) for game in declared_games]
    if returned_ids != declared_ids:
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:returned_games_mismatch")
    if len(set(returned_ids)) != len(returned_ids):
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:duplicate_game_identity")
    wrong_slate = [
        identity
        for identity, game in zip(returned_ids, games)
        if lock_module._game_day(game) != slate
    ]
    if wrong_slate:
        raise RuntimeError(
            "MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:game_slate_mismatch:"
            + ",".join(wrong_slate)
        )
    fingerprint = str(manifest.get("fingerprint") or "")
    if not fingerprint or str(binding.get("fingerprint") or "") != fingerprint:
        raise RuntimeError("MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID:fingerprint_binding_mismatch")

    return list(games), {
        "providerManifestValidated": True,
        "providerManifestVersion": manifest.get("version"),
        "providerManifestFingerprint": fingerprint,
        "providerManifestObservedAtUtc": manifest.get("observedAtUtc"),
        "providerManifestPullId": manifest.get("pullId"),
        "providerManifestPk": binding.get("pk"),
        "providerManifestSk": binding.get("sk"),
        "providerManifestImmutable": binding.get("immutable") is True,
        "providerManifestFullProviderSchedule": binding.get("fullProviderSchedule") is True,
        "verifiedFullSlateManifestVersion": resolved.get("version"),
        "verifiedFullSlateGameCount": resolved.get("fullSlateGameCount"),
        "latestProviderFeedGameCount": resolved.get("latestFeedGameCount"),
        "latestProviderFeedContracted": resolved.get("latestFeedContracted") is True,
        "latestProviderManifestFingerprint": resolved.get("latestFeedFingerprint"),
        "latestProviderManifestObservedAtUtc": resolved.get("latestFeedObservedAtUtc"),
        "rosterAuthorityMode": resolved.get("rosterAuthorityMode"),
        "officialScheduleBacked": resolved.get("officialScheduleBacked") is True,
        "officialScheduleAuthorityVersion": resolved.get("officialScheduleAuthorityVersion"),
        "officialScheduleAuthoritySource": resolved.get("officialScheduleAuthoritySource"),
        "officialScheduleAuthorityFingerprint": resolved.get("officialScheduleAuthorityFingerprint"),
        "officialScheduleGameCount": resolved.get("officialScheduleGameCount"),
        "officialScheduleAuthoritativeStartTimes": resolved.get("officialScheduleAuthoritativeStartTimes") is True,
        "officialScheduleMissingProviderEventGameIds": list(resolved.get("officialScheduleMissingProviderEventGameIds") or []),
        "eventRosterBacked": resolved.get("eventRosterBacked") is True,
        "legacyRosterMigrationFallback": resolved.get("legacyMigrationFallback") is True,
        "latestProviderFeedAnomalyCount": int(resolved.get("latestFeedAnomalyCount") or 0),
        "latestProviderFeedAnomalies": copy.deepcopy(resolved.get("latestFeedAnomalies") or []),
        "durableRosterImmutableReadbackVerified": resolved.get("immutableReadbackVerified") is True,
    }


def _manifest_lock_state(
    lock_module: Any,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build timing observability directly from the verified durable roster.

    During feed contraction or fallback-to-provider identity migration, the
    verified roster can legitimately differ from the manifest embedded in the
    latest odds pull. Replacing only that pull's ``games`` array produces a
    mixed proof that must fail validation. The roster has already passed the
    immutable authority check above, so timing is derived from it directly.
    """
    now = _now_utc()
    starts = [
        value
        for value in (
            _parse_dt(game.get("commence_time") or game.get("commenceTime"))
            for game in manifest
        )
        if value is not None
    ]
    first_start = min(starts) if starts else None
    last_start = max(starts) if starts else None
    lock_minutes = int(getattr(lock_module, "LOCK_MINUTES", 45))
    first_cutoff = (
        first_start - timedelta(minutes=lock_minutes)
        if first_start is not None
        else None
    )
    last_cutoff = (
        last_start - timedelta(minutes=lock_minutes)
        if last_start is not None
        else None
    )
    latest = pulls[-1] if pulls else {}
    return {
        "applied": bool(first_start),
        "policyVersion": AUTHORITY_VERSION,
        "authorityVersion": AUTHORITY_VERSION,
        "slateWideLock": False,
        "perGameLock": True,
        "lockMinutesBeforeFirstGame": lock_minutes,
        "lockMinutesBeforeEachGame": lock_minutes,
        "firstGameStartUtc": first_start.isoformat() if first_start else None,
        "lastGameStartUtc": last_start.isoformat() if last_start else None,
        "firstPerGameLockAtUtc": (
            first_cutoff.isoformat() if first_cutoff else None
        ),
        "lastPerGameLockAtUtc": last_cutoff.isoformat() if last_cutoff else None,
        "lockAtUtc": None,
        "locked": False,
        "lockStatus": "AWAITING_CANONICAL_PER_GAME_ROWS",
        "source": "verified_durable_official_roster_timing",
        "minutesUntilFirstGameStart": (
            round((first_start - now).total_seconds() / 60.0, 2)
            if first_start else None
        ),
        "minutesUntilFirstPerGameLock": (
            round((first_cutoff - now).total_seconds() / 60.0, 2)
            if first_cutoff else None
        ),
        "totalPullCountAvailable": len(pulls),
        "scoringPullCount": len(pulls),
        "latestAvailablePullAt": latest.get("pulled_at"),
        "latestScoringPullAt": latest.get("pulled_at"),
    }


def _coverage(games: List[Dict[str, Any]], predictions: List[Dict[str, Any]], stored: List[Dict[str, Any]], store_requested: bool) -> Dict[str, Any]:
    expected = {game_identity(game): game for game in games}
    produced = {game_identity(row): row for row in predictions if row.get("predictedWinner")}
    missing = sorted(set(expected) - set(produced))
    extra = sorted(set(produced) - set(expected))
    stored_ok = len([row for row in stored if isinstance(row, dict) and row.get("ok")])
    matchup_counts: Dict[str, int] = {}
    for game in games:
        matchup = f"{_norm_team(game.get('away_team') or game.get('awayTeam'))}|{_norm_team(game.get('home_team') or game.get('homeTeam'))}"
        matchup_counts[matchup] = matchup_counts.get(matchup, 0) + 1
    doubleheaders = sorted(key for key, count in matchup_counts.items() if count > 1)
    complete = not missing and not extra and len(produced) == len(expected)
    if store_requested:
        complete = complete and stored_ok == len(produced)
    return {
        "applied": True,
        "version": VERSION,
        "strictCoverageRequired": True,
        "doubleheaderSafeIdentity": True,
        "manifestGameCount": len(expected),
        "predictionGameCount": len(produced),
        "storedPredictionCount": stored_ok,
        "storeRequested": bool(store_requested),
        "coverageRatio": round(len(produced) / len(expected), 4) if expected else None,
        "coverageComplete": complete,
        "operationalStatus": "COMPLETE" if complete else "INCOMPLETE_BLOCKED",
        "missingGameIdentities": missing,
        "extraGameIdentities": extra,
        "manifestGameIdentities": sorted(expected),
        "predictionGameIdentities": sorted(produced),
        "doubleheaderMatchups": doubleheaders,
        "publicAccuracyEligible": complete,
        "rules": [
            "Provider game id is the primary identity.",
            "When provider id is unavailable, game key plus commence time is required.",
            "Same-team doubleheaders must remain separate lock-manifest rows.",
            "Only validated immutable LOCKED#GAME rows count as official locks.",
        ],
    }


def _canonical_items(module: Any, slate: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    table = getattr(getattr(module, "history", None), "PULLS", None)
    if table is None:
        return [], "SNAPSHOTS_TABLE_not_configured"
    items: List[Dict[str, Any]] = []
    start_key = None
    try:
        while True:
            args: Dict[str, Any] = {
                "KeyConditionExpression": Key("PK").eq(f"GAME_WINNERS#mlb#{slate}"),
                "ConsistentRead": True,
            }
            if start_key:
                args["ExclusiveStartKey"] = start_key
            response = table.query(**args)
            items.extend(
                item
                for item in (response.get("Items") or [])
                if str(item.get("SK") or "").startswith("LOCKED#GAME#")
            )
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                break
    except Exception as exc:
        return [], f"canonical_query_failed:{exc}"
    return items, None


def _canonical_row(
    module: Any,
    item: Dict[str, Any],
    slate: str,
    manifest_game: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    if item.get("record_type") != CANONICAL_RECORD_TYPE:
        errors.append("wrong_record_type")
    if item.get("immutable_locked") is not True:
        errors.append("immutable_locked_item_flag_missing")
    if item.get("stage_authority_verified") is not True:
        errors.append("stage_authority_item_flag_missing")
    if not str(item.get("SK") or "").startswith("LOCKED#GAME#"):
        errors.append("wrong_keyspace")
    row = item.get("data")
    if not isinstance(row, dict):
        errors.append("canonical_data_missing")
        return None, errors
    row = copy.deepcopy(row)
    if str(row.get("slate_date") or row.get("slateDateEt") or "") != slate:
        errors.append("slate_mismatch")
    if row.get("immutableLockedStorage") is not True:
        errors.append("immutable_locked_row_flag_missing")
    if row.get("lockedPrediction") is not True:
        errors.append("locked_prediction_flag_missing")
    if not row.get("predictedWinner") or row.get("predictedSide") not in {"home", "away"}:
        errors.append("winner_or_side_missing")
    if game_identity(row) != game_identity(manifest_game):
        errors.append("manifest_game_identity_mismatch")
    if _norm_team(row.get("homeTeam") or row.get("home_team")) != _norm_team(
        manifest_game.get("home_team") or manifest_game.get("homeTeam")
    ):
        errors.append("manifest_home_team_mismatch")
    if _norm_team(row.get("awayTeam") or row.get("away_team")) != _norm_team(
        manifest_game.get("away_team") or manifest_game.get("awayTeam")
    ):
        errors.append("manifest_away_team_mismatch")
    manifest_start = _parse_dt(manifest_game.get("commence_time") or manifest_game.get("commenceTime"))
    row_start = _parse_dt(row.get("commenceTime") or row.get("commence_time"))
    if not manifest_start or row_start != manifest_start:
        errors.append("manifest_commence_time_mismatch")
    expected_cutoff = manifest_start - timedelta(minutes=45) if manifest_start else None
    row_cutoff = _parse_dt(
        row.get("lockedAtUtc")
        or (row.get("slatePredictionLock") or {}).get("lockAtUtc")
        or (row.get("frozenFeatureVector") or {}).get("lockAtUtc")
    )
    if not expected_cutoff or row_cutoff != expected_cutoff:
        errors.append("manifest_tminus45_cutoff_mismatch")
    try:
        import mlb_immutable_locked_storage_patch as immutable_storage

        errors.extend(
            immutable_storage.validate_canonical_stage_authority(
                module.history.PULLS,
                row,
            )
        )
    except Exception as exc:
        errors.append(f"stage_authority_validator_unavailable:{exc}")
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as vector_contract

        errors.extend(vector_contract.validate_selection_lock_vector_status(row))
    except Exception as exc:
        errors.append(f"selection_vector_status_validator_unavailable:{exc}")
    try:
        import mlb_prediction_probability_contract_v1 as probability_contract

        version = row.get("probabilityContractVersion")
        if version in (None, ""):
            row = probability_contract.suppress_legacy_probability_authority(row)
        else:
            errors.extend(probability_contract.validation_errors(row))
    except Exception as exc:
        errors.append(f"probability_contract_read_validator_unavailable:{exc}")
    return (row if not errors else None), sorted(set(errors))


def _canonical_by_identity(
    module: Any,
    slate: str,
    manifest: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[str]], Optional[str]]:
    manifest_by_id = {game_identity(game): game for game in manifest}
    manifest_ids = set(manifest_by_id)
    candidates: Dict[str, List[Dict[str, Any]]] = {}
    invalid: Dict[str, List[str]] = {}
    items, query_error = _canonical_items(module, slate)
    for item in items:
        raw = item.get("data") if isinstance(item.get("data"), dict) else item
        identity = game_identity(raw)
        if identity not in manifest_ids:
            continue
        row, errors = _canonical_row(module, item, slate, manifest_by_id[identity])
        if errors:
            invalid.setdefault(identity, []).extend(errors)
            continue
        candidates.setdefault(identity, []).append(row or {})

    canonical: Dict[str, Dict[str, Any]] = {}
    for identity, rows in candidates.items():
        if identity in invalid:
            continue
        if len(rows) != 1:
            invalid.setdefault(identity, []).append("ambiguous_multiple_canonical_rows")
            continue
        canonical[identity] = rows[0]
    invalid = {identity: sorted(set(errors)) for identity, errors in invalid.items()}
    return canonical, invalid, query_error


def _per_game_cutoff(lock_module: Any, game: Dict[str, Any]) -> Optional[str]:
    start = lock_module._parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return (start - timedelta(minutes=lock_module.LOCK_MINUTES)).isoformat() if start else None


def _prelock_row(
    row: Dict[str, Any],
    public: Dict[str, Any],
    cutoff: Optional[str],
    pending_status: str = "OPEN_PRE_LOCK",
) -> Dict[str, Any]:
    out = copy.deepcopy(row or {})
    tags = {
        str(tag)
        for tag in (out.get("tags") or [])
        if str(tag)
        not in {
            "FINAL_LOCKED",
            "SLATE_LOCKED",
            "SLATE_WIDE_45_MIN_LOCK_POLICY",
            "OFFICIAL_PREDICTION",
            "OFFICIAL_LOCKED_PREDICTION",
            "OFFICIAL_PREDICTION_NOT_PLAYABLE",
            "CANONICAL_PER_GAME_LOCK",
        }
    }
    if pending_status == "OPEN_PRE_LOCK":
        tags.update({"PRE_LOCK_PREDICTION", "PER_GAME_CANONICAL_LOCK_PENDING"})
        official_status = "PRE_LOCK_PLATFORM_PREDICTION"
        reason = "canonical_per_game_lock_not_yet_available"
        recommendation_status = "PRE_LOCK_PREDICTION"
        display_group = "pre_lock_prediction"
    else:
        tags.update({pending_status, "PER_GAME_CANONICAL_LOCK_MISSING"})
        official_status = pending_status
        reason = "required_canonical_per_game_lock_missing"
        recommendation_status = pending_status
        display_group = "lock_failure"
    out.update({
        "locked": False,
        "canonical": False,
        "lockedPrediction": False,
        "lockOutcomeRecorded": False,
        "lockStatus": pending_status,
        "officialPrediction": False,
        "officialPick": False,
        "displayPrediction": True,
        "isOfficialDisplayPick": False,
        "officialPredictionStatus": official_status,
        "officialPredictionReason": reason,
        "recommendationStatus": recommendation_status,
        "displayGroup": display_group,
        "fullDataFinalPick": False,
        "accuracyTargetEligible": False,
        "playableAccuracyEligible": False,
        "trainingEligible": False,
        "trainingEligibilityStatus": "PENDING_IMMUTABLE_LOCK",
        "trainingExclusionReasons": ["immutable_tminus45_prediction_not_available"],
        "scheduledLockAtUtc": cutoff,
        "slatePredictionLock": public,
        "perGameCanonicalLock": {
            "authorityVersion": AUTHORITY_VERSION,
            "status": pending_status,
            "lockAtUtc": cutoff,
            "canonical": False,
        },
        "tags": sorted(tags),
    })
    out.pop("lockedAtUtc", None)
    out.pop("finalGateStored", None)
    out.pop("frozenFeatureVector", None)
    out.pop("frozenFeatureVectorVersion", None)
    out.pop("frozenOutcomeFeatures", None)
    out.pop("frozenReliabilityFeatures", None)
    out.pop("featureVectorFrozenAtLock", None)
    out.pop("mlFeatureFreeze", None)
    out.pop("immutablePerGameStage", None)
    out.pop("immutableLockedStorage", None)
    out.pop("canonicalLockedStore", None)
    gate = dict(out.get("lastPossiblePredictionGate") or {})
    gate.update({
        "policyVersion": AUTHORITY_VERSION,
        "phase": "PRE_LOCK" if pending_status == "OPEN_PRE_LOCK" else pending_status,
        "finalWindowActive": False,
        "finalLocked": False,
        "slateWideLock": False,
        "perGameLock": True,
        "lockAtUtc": cutoff,
    })
    out["lastPossiblePredictionGate"] = gate
    return out


def _storage_request_active() -> bool:
    try:
        import mlb_locked_prediction_storage_finalizer_v1 as finalizer

        return bool(finalizer.storage_request_active())
    except Exception:
        return False


def _persisted_prelock_by_identity(
    module: Any,
    lock_module: Any,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    slate: str,
    now: datetime,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[str]]]:
    """Read validated immutable pre-lock snapshots without invoking a scorer."""
    try:
        import mlb_daily_per_game_lock_patch as per_game
    except Exception as exc:
        return {}, {"__runtime__": [f"persisted_prelock_validator_unavailable:{exc}"]}

    class Adapter:
        history = module.history
        # `_last_prelock_candidate` uses the installed engine attestation to
        # require the canonical probability contract even if a malformed or
        # legacy snapshot omitted its version field. Public persisted reads
        # must enforce the same contract as the scheduled T-45 lock path.
        mlb_game_winner_engine = module
        LOCK_MINUTES = lock_module.LOCK_MINUTES
        _parse_dt = staticmethod(lock_module._parse_dt)

    current: Dict[str, Dict[str, Any]] = {}
    invalid: Dict[str, List[str]] = {}
    for game in manifest:
        identity = game_identity(game)
        try:
            scoring = per_game._scoring_pulls(
                Adapter,
                pulls,
                game,
                at_or_before=now,
            )
            row, _proof, _bound, errors = per_game._last_prelock_candidate(
                Adapter,
                slate,
                game,
                scoring,
                at_or_before=now,
            )
        except Exception as exc:
            row = None
            errors = [f"persisted_prelock_read_failed:{type(exc).__name__}:{exc}"]
        if row and not errors:
            current[identity] = _bind_row_to_manifest(row, game)
        elif errors:
            invalid[identity] = sorted(set(str(error) for error in errors if error))
    return current, invalid


def _pending_status(game: Dict[str, Any], now: datetime, cutoff: Optional[str]) -> str:
    start = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    cutoff_at = _parse_dt(cutoff)
    if start and now >= start:
        return "MISSED_LOCK"
    if cutoff_at and now >= cutoff_at:
        return "LOCK_DUE_CANONICAL_MISSING"
    return "OPEN_PRE_LOCK"


def _display_card(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "gameId": row.get("gameId"),
        "gameIdentity": row.get("gameIdentity"),
        "gameKey": row.get("gameKey"),
        "officialGamePk": row.get("officialGamePk"),
        "officialGameId": row.get("officialGameId"),
        "providerEventId": row.get("providerEventId"),
        "providerCommenceTime": row.get("providerCommenceTime"),
        "providerStartDriftSeconds": row.get("providerStartDriftSeconds"),
        "canonicalStartTimeSource": row.get("canonicalStartTimeSource"),
        "sourcePredictionGameId": row.get("sourcePredictionGameId"),
        "sourcePredictionGameIdentity": row.get("sourcePredictionGameIdentity"),
        "homeTeam": row.get("homeTeam"),
        "awayTeam": row.get("awayTeam"),
        "commenceTime": row.get("commenceTime"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "selectionFingerprint": row.get("selectionFingerprint") or row.get("lastPrelockSelectionFingerprint"),
        "confidenceTier": row.get("confidenceTier"),
        "teamWinProbabilityPct": row.get("teamWinProbabilityPct", row.get("winProbabilityPct")),
        "score": row.get("score"),
        "rank": row.get("rank"),
        "officialPrediction": bool(row.get("officialPrediction")),
        "officialPick": bool(row.get("officialPick")),
        "locked": bool(row.get("locked") or row.get("lockedPrediction")),
        "lockedPrediction": bool(row.get("lockedPrediction")),
        "canonical": bool(row.get("canonical") or row.get("lockedPrediction")),
        "playable": bool(row.get("playable")),
        "playablePick": bool(row.get("playablePick")),
        "blocked": bool(row.get("blocked")),
        "playabilityBlockReasons": row.get("playabilityBlockReasons") or [],
        "trainingEligible": row.get("trainingEligible", (row.get("mlFeatureFreeze") or {}).get("trainingEligible")),
        "trainingEligibilityStatus": row.get("trainingEligibilityStatus"),
        "trainingExclusionReasons": row.get("trainingExclusionReasons") or (row.get("mlFeatureFreeze") or {}).get("trainingExclusionReasons") or [],
        "exactVectorVerified": row.get("exactVectorVerified"),
        "exactVectorValidationErrors": row.get("exactVectorValidationErrors") or [],
        "lockOutcomeRecorded": bool(row.get("lockOutcomeRecorded") or row.get("lockedPrediction")),
        "lockStatus": row.get("lockStatus") or row.get("officialPredictionStatus"),
        "scheduledLockAtUtc": row.get("scheduledLockAtUtc") or (row.get("perGameCanonicalLock") or {}).get("lockAtUtc"),
        "perGameCanonicalLock": copy.deepcopy(row.get("perGameCanonicalLock") or {}),
        "officialPredictionStatus": row.get("officialPredictionStatus"),
        "playabilityStatus": row.get("playabilityStatus"),
        "playabilityAssessment": copy.deepcopy(row.get("playabilityAssessment")),
        "requiredPlayabilityCheckpoint": row.get("requiredPlayabilityCheckpoint"),
        "requiredPlayabilityCheckpointDue": row.get("requiredPlayabilityCheckpointDue") is True,
        "eventPlayabilityAssessmentRequired": row.get("eventPlayabilityAssessmentRequired") is True,
        "playabilityAssessmentValidationErrors": row.get("playabilityAssessmentValidationErrors") or [],
        "readiness": copy.deepcopy(row.get("readiness") or {}),
        "requiredReadinessCheckpoint": row.get("requiredReadinessCheckpoint"),
        "requiredReadinessCheckpointDue": row.get("requiredReadinessCheckpointDue") is True,
        "readinessValidationErrors": row.get("readinessValidationErrors") or [],
        "recommendationStatus": row.get("recommendationStatus"),
        "tags": row.get("tags") or [],
    }


def _official_row(row: Dict[str, Any], public: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(row or {})
    tags = {
        str(tag)
        for tag in (out.get("tags") or [])
        if str(tag) != "SLATE_WIDE_45_MIN_LOCK_POLICY"
    }
    tags.update({"FINAL_LOCKED", "OFFICIAL_PREDICTION", "OFFICIAL_LOCKED_PREDICTION", "CANONICAL_PER_GAME_LOCK"})
    lock_at = out.get("lockedAtUtc") or (out.get("frozenFeatureVector") or {}).get("lockAtUtc")
    row_lock = dict(public)
    row_lock.update(out.get("slatePredictionLock") or {})
    row_lock.update({
        "policyVersion": AUTHORITY_VERSION,
        "authorityVersion": AUTHORITY_VERSION,
        "slateWideLock": False,
        "perGameLock": True,
        "locked": True,
        "lockStatus": "OFFICIAL_LOCKED_PREDICTION",
        "lockAtUtc": lock_at,
    })
    freeze = out.get("mlFeatureFreeze") if isinstance(out.get("mlFeatureFreeze"), dict) else {}
    training_eligible = out.get("trainingEligible")
    if training_eligible is None:
        training_eligible = freeze.get("trainingEligible") is True
    training_exclusions = list(
        out.get("trainingExclusionReasons")
        or freeze.get("trainingExclusionReasons")
        or []
    )
    out.update({
        "locked": True,
        "canonical": True,
        "lockedPrediction": True,
        "lockOutcomeRecorded": True,
        "lockStatus": "LOCKED_CANONICAL",
        "officialPrediction": True,
        "officialPick": True,
        "isOfficialDisplayPick": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "officialPredictionReason": "validated_immutable_canonical_per_game_lock",
        "selectionFingerprint": out.get("lastPrelockSelectionFingerprint"),
        "scheduledLockAtUtc": lock_at,
        "trainingEligible": bool(training_eligible),
        "trainingEligibilityStatus": "ELIGIBLE" if training_eligible else "INELIGIBLE",
        "trainingExclusionReasons": training_exclusions,
        "slatePredictionLock": row_lock,
        "perGameCanonicalLock": {
            "authorityVersion": AUTHORITY_VERSION,
            "status": "OFFICIAL_LOCKED_PREDICTION",
            "lockAtUtc": lock_at,
            "canonical": True,
        },
        "tags": sorted(tags),
    })
    gate = dict(out.get("lastPossiblePredictionGate") or {})
    gate.update({
        "policyVersion": AUTHORITY_VERSION,
        "phase": "FINAL_LOCKED",
        "finalWindowActive": False,
        "finalLocked": True,
        "slateWideLock": False,
        "perGameLock": True,
        "lockAtUtc": lock_at,
    })
    out["lastPossiblePredictionGate"] = gate
    return out


def _fail_closed(result: Dict[str, Any], error: str) -> Dict[str, Any]:
    out = copy.deepcopy(result or {})
    public = dict(out.get("slatePredictionLock") or {})
    public.update({
        "applied": False,
        "policyVersion": AUTHORITY_VERSION,
        "authorityVersion": AUTHORITY_VERSION,
        "slateWideLock": False,
        "perGameLock": True,
        "locked": False,
        "lockStatus": "CANONICAL_AUTHORITY_UNAVAILABLE_FAIL_CLOSED",
        "providerManifestValidated": False,
        "error": str(error),
    })
    out["slatePredictionLock"] = public
    out["locked"] = False
    out["operationalDefect"] = True
    out["canonicalPredictionComplete"] = False
    out["lockStatusComplete"] = False
    out["lockedPredictionCount"] = 0
    out["canonicalPredictionCount"] = 0
    out["lockedStatusCount"] = 0
    out["lockOutcomeCount"] = 0
    out["noPredictionDataCount"] = 0
    out["allGamesPredicted"] = False
    out["predictionCoverageComplete"] = False
    out["displayStatusCoverageComplete"] = False
    out["lifecycleCoverageComplete"] = False
    out["allGamesHaveDisplayedWinnerPrediction"] = False
    out["predictions"] = [_prelock_row(row, public, None) for row in (out.get("predictions") or []) if isinstance(row, dict)]
    cards = [_display_card(row) for row in out["predictions"]]
    out["officialPredictionCount"] = 0
    out["officialPickCount"] = 0
    out["preLockPredictionCount"] = len(out["predictions"])
    out["requiredWinnerPredictionDisplay"] = cards
    out["officialPredictionDisplay"] = []
    out["nonOfficialPredictionDisplay"] = cards
    out["nonPlayableOfficialPredictionDisplay"] = []
    coverage = dict(out.get("slateCoverage") or {})
    coverage.update({
        "applied": False,
        "version": VERSION,
        "strictCoverageRequired": True,
        "coverageComplete": False,
        "predictionCoverageComplete": False,
        "displayStatusCoverageComplete": False,
        "lifecycleCoverageComplete": False,
        "canonicalCoverageComplete": False,
        "canonicalPredictionComplete": False,
        "publicAccuracyEligible": False,
        "providerManifestValidated": False,
        "operationalStatus": "PROVIDER_MANIFEST_AUTHORITY_UNAVAILABLE_FAIL_CLOSED",
        "error": str(error),
        "canonicalReadAuthorityWriteCount": 0,
    })
    out["slateCoverage"] = coverage
    out["lastPossiblePredictionGate"] = {
        "applied": False,
        "policyVersion": AUTHORITY_VERSION,
        "slateWideLock": False,
        "perGameLock": True,
        "finalLockedCount": 0,
        "resultLocked": False,
        "error": str(error),
    }
    out["mlFeatureFreeze"] = {
        "applied": True,
        "canonicalPublicAuthorityVersion": AUTHORITY_VERSION,
        "frozenRowCount": 0,
        "trainingEligibleCount": 0,
        "coverageComplete": False,
        "pendingRowsAreNotFrozen": True,
    }
    return out


def apply(lock_module: Any):
    if getattr(lock_module, "_INQSI_MLB_SLATE_COVERAGE_PATCH_APPLIED", False):
        return lock_module

    lock_module._game_key = game_identity

    def latest_games(pulls: List[Dict[str, Any]], slate: str) -> List[Dict[str, Any]]:
        return _latest_games(lock_module, pulls, slate)

    lock_module._latest_games = latest_games
    original_lock_state = lock_module._lock_state

    def lock_state(pulls: List[Dict[str, Any]], slate: str) -> Dict[str, Any]:
        state = original_lock_state(pulls, slate)
        scoring = state.get("_scoring_pulls") or pulls
        manifest = latest_games(scoring, slate)
        public = {
            "manifestVersion": VERSION,
            "manifestGameCount": len(manifest),
            "manifestGameIdentities": [game_identity(game) for game in manifest],
            "doubleheaderSafeIdentity": True,
        }
        state.update(public)
        return state

    lock_module._lock_state = lock_state

    def locked_result(module: Any, result: Dict[str, Any], args: Tuple[Any, ...], kwargs: Dict[str, Any], store: bool) -> Dict[str, Any]:
        """Overlay canonical per-game locks without generating or storing a pick."""
        slate = str((result or {}).get("slate_date") or lock_module._slate_from_call(args, kwargs, module))
        pulls = module.history.query_pulls("mlb", slate, lock_module._limit(kwargs))
        pulls = sorted(
            pulls or [],
            key=lambda pull: lock_module._pull_dt(pull) or datetime.min.replace(tzinfo=timezone.utc),
        )
        manifest, manifest_authority = _provider_manifest_for_public(
            module,
            lock_module,
            pulls,
            slate,
        )
        # Derive every timing field from the verified full schedule rather than
        # mixing that roster with the latest pull's older manifest proof.
        state = _manifest_lock_state(lock_module, pulls, manifest)
        public = {key: value for key, value in state.items() if not key.startswith("_")}
        public.update(manifest_authority)
        canonical, invalid, query_error = _canonical_by_identity(module, slate, manifest)
        manifest_ids = [game_identity(game) for game in manifest]
        canonical_count = len(canonical)
        all_canonical = bool(manifest_ids) and canonical_count == len(manifest_ids)
        terminal_outcomes: Dict[str, Dict[str, Any]] = {}
        playability_lifecycles: Dict[str, Dict[str, Any]] = {}
        readiness_lifecycles: Dict[str, Dict[str, Any]] = {}
        lifecycle_validation_errors: Dict[str, List[str]] = {}
        readiness_validation_warnings: Dict[str, List[str]] = {}
        now = _now_utc()
        for game in manifest:
            identity = game_identity(game)
            readiness = _readiness_for_public(
                module,
                lock_module,
                slate,
                game,
                now,
            )
            readiness_lifecycles[identity] = readiness
            if readiness.get("validationErrors"):
                readiness_validation_warnings[identity] = sorted(set(
                    readiness.get("validationErrors") or []
                ))
            if identity in canonical:
                lifecycle = _playability_for_public(
                    module,
                    lock_module,
                    slate,
                    game,
                    canonical[identity],
                    now,
                    event_pending_required=_is_doubleheader_game_two(manifest, game),
                )
                playability_lifecycles[identity] = lifecycle
                lifecycle_errors = sorted(set(
                    list(lifecycle.get("validationErrors") or [])
                    + list(lifecycle.get("historicalValidationErrors") or [])
                ))
                if lifecycle_errors:
                    lifecycle_validation_errors.setdefault(identity, []).extend(
                        lifecycle_errors
                    )
                continue
            outcome, outcome_errors = _terminal_outcome_for_public(
                module,
                lock_module,
                slate,
                game,
                manifest_authority,
            )
            if outcome:
                terminal_outcomes[identity] = outcome
            if outcome_errors:
                lifecycle_validation_errors.setdefault(identity, []).extend(
                    outcome_errors
                )
        lifecycle_validation_errors = {
            identity: sorted(set(errors))
            for identity, errors in lifecycle_validation_errors.items()
            if errors
        }
        no_prediction_data_count = len(terminal_outcomes)
        locked_status_count = canonical_count + no_prediction_data_count
        lock_status_complete = bool(manifest_ids) and locked_status_count == len(manifest_ids)
        pending_states = {
            game_identity(game): (
                "LOCKED_NO_PREDICTION_DATA"
                if game_identity(game) in terminal_outcomes
                else _pending_status(
                    game,
                    now,
                    _per_game_cutoff(lock_module, game),
                )
            )
            for game in manifest
            if game_identity(game) not in canonical
        }
        lock_due_count = len([
            value for value in pending_states.values()
            if value == "LOCK_DUE_CANONICAL_MISSING"
        ])
        missed_lock_count = len([
            value for value in pending_states.values()
            if value == "MISSED_LOCK"
        ])
        lock_times = [
            _parse_dt(row.get("lockedAtUtc") or (row.get("frozenFeatureVector") or {}).get("lockAtUtc"))
            for row in canonical.values()
        ]
        lock_times = [value for value in lock_times if value]
        if all_canonical:
            lock_status = "COMPLETE_MANIFEST_ALL_CANONICAL"
        elif lock_status_complete:
            lock_status = "COMPLETE_WITH_NO_PREDICTION_DATA"
        elif missed_lock_count:
            lock_status = "MISSED_LOCK"
        elif lock_due_count:
            lock_status = "LOCK_DUE_CANONICAL_MISSING"
        elif canonical_count:
            lock_status = "PARTIAL_PER_GAME_CANONICAL"
        elif not pulls:
            lock_status = "NO_PULL_HISTORY"
        else:
            lock_status = "OPEN_PRE_LOCK"
        public.update({
            "applied": query_error is None,
            "policyVersion": AUTHORITY_VERSION,
            "authorityVersion": AUTHORITY_VERSION,
            "slateWideLock": False,
            "perGameLock": True,
            "lockMinutesBeforeEachGame": lock_module.LOCK_MINUTES,
            "locked": all_canonical,
            "lockStatus": lock_status if query_error is None else "CANONICAL_READ_FAILED_FAIL_CLOSED",
            "lockAtUtc": max(lock_times).isoformat() if all_canonical and lock_times else None,
            "source": "immutable_provider_schedule_manifest_with_validated_locked_game_rows",
            "manifestVersion": VERSION,
            "manifestGameCount": len(manifest_ids),
            "manifestGameIdentities": manifest_ids,
            "canonicalLockedGameCount": canonical_count,
            "lockedPredictionCount": canonical_count,
            "canonicalPredictionCount": canonical_count,
            "lockedStatusCount": locked_status_count,
            "lockOutcomeCount": locked_status_count,
            "noPredictionDataCount": no_prediction_data_count,
            "lockStatusComplete": lock_status_complete,
            "lockOutcomeCoveragePct": round(
                locked_status_count / len(manifest_ids) * 100.0, 2
            ) if manifest_ids else 0.0,
            "pendingCanonicalGameCount": max(len(manifest_ids) - canonical_count, 0),
            "pendingLockStatusGameCount": max(len(manifest_ids) - locked_status_count, 0),
            "lockDueCanonicalMissingCount": lock_due_count,
            "missedLockCount": missed_lock_count,
            "pendingCanonicalStatuses": pending_states,
            "canonicalCoverageComplete": all_canonical,
            "canonicalPredictionComplete": all_canonical,
            "canonicalReadOperational": query_error is None,
            "canonicalReadError": query_error,
            "invalidCanonicalRows": invalid,
            "invalidLifecycleStatusRows": lifecycle_validation_errors,
            "readinessValidationWarnings": readiness_validation_warnings,
            "readinessWarningGameCount": len(readiness_validation_warnings),
            "doubleheaderSafeIdentity": True,
            "rules": [
                "Each game locks independently 45 minutes before its own scheduled start.",
                "The last valid pre-lock prediction promoted into immutable LOCKED#GAME storage is final.",
                "Canonical rows are overlaid on public reads and are never recomputed.",
                "Games before their cutoff without a valid canonical row remain explicitly pre-lock.",
                "A missing canonical row at or after cutoff is an operational lock failure, never pre-lock or official.",
                "A game with no integrity-valid pregame prediction receives an immutable LOCKED_NO_PREDICTION_DATA outcome instead of a fabricated pick.",
                "Late playability assessments can block wagering release but cannot rewrite the immutable winner.",
                "The result is locked only after every manifest game has a canonical row.",
                "The manifest is the independently stored full provider schedule, including games without supported odds.",
            ],
        })

        persisted_prelock_required = bool(
            getattr(
                module,
                "_INQSI_MLB_PERSISTED_PRELOCK_PUBLIC_AUTHORITY_ENABLED",
                False,
            )
            and not _storage_request_active()
        )
        persisted_prelock: Dict[str, Dict[str, Any]] = {}
        persisted_prelock_invalid: Dict[str, List[str]] = {}
        if persisted_prelock_required:
            persisted_prelock, persisted_prelock_invalid = _persisted_prelock_by_identity(
                module,
                lock_module,
                pulls,
                manifest,
                slate,
                now,
            )

        current_candidates: Dict[str, List[Tuple[bool, Dict[str, Any]]]] = {}
        extra_current: List[str] = []
        ambiguous_current: List[str] = []
        manifest_by_identity = {
            game_identity(game): game for game in manifest
        }
        source_rows = (
            list(persisted_prelock.values())
            if persisted_prelock_required
            else (result or {}).get("predictions") or []
        )
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            identity = game_identity(row)
            if identity in manifest_by_identity:
                current_candidates.setdefault(identity, []).append((True, row))
                continue
            alias_matches = [
                manifest_identity
                for manifest_identity, manifest_game in manifest_by_identity.items()
                if _same_manifest_game(manifest_game, row)
            ]
            if len(alias_matches) == 1:
                current_candidates.setdefault(alias_matches[0], []).append((False, row))
            else:
                extra_current.append(identity)

        current: Dict[str, Dict[str, Any]] = {}
        for identity, candidates in current_candidates.items():
            exact = [row for is_exact, row in candidates if is_exact]
            selected = exact if exact else [row for _, row in candidates]
            if len(selected) != 1:
                ambiguous_current.append(identity)
                continue
            current[identity] = _bind_row_to_manifest(
                selected[0],
                manifest_by_identity[identity],
            )

        predictions: List[Dict[str, Any]] = []
        missing: List[str] = []
        for game in manifest:
            identity = game_identity(game)
            if identity in canonical:
                row = _official_row(canonical[identity], public)
                row = _overlay_playability(
                    row,
                    playability_lifecycles.get(identity) or {},
                )
            elif identity in terminal_outcomes:
                cutoff = _per_game_cutoff(lock_module, game)
                base = current.get(identity) or {
                    "sport": "mlb",
                    "slate_date": slate,
                    "slateDateEt": slate,
                    "gameId": game.get("game_id") or game.get("gameId") or game.get("id"),
                    "gameIdentity": game.get("game_id") or game.get("gameId") or game.get("id"),
                    "gameKey": game.get("game_key") or game.get("gameKey"),
                    "officialGamePk": game.get("official_game_pk") or game.get("officialGamePk"),
                    "officialGameId": game.get("official_game_id") or game.get("officialGameId"),
                    "providerEventId": game.get("provider_event_id") or game.get("providerEventId"),
                    "providerCommenceTime": game.get("provider_commence_time") or game.get("providerCommenceTime"),
                    "providerStartDriftSeconds": game.get("provider_start_drift_seconds") if game.get("provider_start_drift_seconds") is not None else game.get("providerStartDriftSeconds"),
                    "canonicalStartTimeSource": game.get("canonical_start_time_source") or game.get("canonicalStartTimeSource"),
                    "commenceTime": game.get("commence_time") or game.get("commenceTime"),
                    "awayTeam": game.get("away_team") or game.get("awayTeam"),
                    "homeTeam": game.get("home_team") or game.get("homeTeam"),
                    "predictedWinner": None,
                    "predictedSide": None,
                    "tags": [],
                }
                row = _prelock_row(
                    base,
                    public,
                    cutoff,
                    "LOCKED_NO_PREDICTION_DATA",
                )
                row = _overlay_terminal_outcome(
                    row,
                    terminal_outcomes[identity],
                )
            elif identity in current:
                cutoff = _per_game_cutoff(lock_module, game)
                row = _prelock_row(
                    current[identity],
                    public,
                    cutoff,
                    pending_states.get(identity, "OPEN_PRE_LOCK"),
                )
            else:
                cutoff = _per_game_cutoff(lock_module, game)
                base = _bind_row_to_manifest({
                    "sport": "mlb",
                    "slate_date": slate,
                    "slateDateEt": slate,
                    "predictedWinner": None,
                    "predictedSide": None,
                    "tags": [],
                }, game)
                row = _prelock_row(
                    base,
                    public,
                    cutoff,
                    pending_states.get(identity, "OPEN_PRE_LOCK"),
                )
            row = _overlay_readiness(
                row,
                readiness_lifecycles.get(identity) or {},
            )
            row["slateCoverageVersion"] = VERSION
            predictions.append(row)

        displayed_complete = not missing and len(predictions) == len(manifest_ids)
        winner_predictions = [row for row in predictions if row.get("predictedWinner")]
        winner_prediction_complete = bool(manifest_ids) and (
            len(winner_predictions) == len(manifest_ids)
        )
        coverage = _coverage(manifest, predictions, [], False)
        missing_winner_predictions = list(coverage.get("missingGameIdentities") or [])
        coverage.update({
            "coverageComplete": winner_prediction_complete,
            "predictionCoverageComplete": winner_prediction_complete,
            "displayStatusCoverageComplete": displayed_complete,
            "lifecycleCoverageComplete": displayed_complete,
            "operationalStatus": lock_status if query_error is None else "CANONICAL_READ_FAILED_FAIL_CLOSED",
            "publicAccuracyEligible": all_canonical,
            "canonicalAuthorityVersion": AUTHORITY_VERSION,
            "canonicalReadOperational": query_error is None,
            "canonicalReadError": query_error,
            "canonicalLockedGameCount": canonical_count,
            "lockedPredictionCount": canonical_count,
            "canonicalPredictionCount": canonical_count,
            "lockedStatusCount": locked_status_count,
            "lockOutcomeCount": locked_status_count,
            "noPredictionDataCount": no_prediction_data_count,
            "lockStatusComplete": lock_status_complete,
            "pendingCanonicalGameCount": max(len(manifest_ids) - canonical_count, 0),
            "pendingLockStatusGameCount": max(len(manifest_ids) - locked_status_count, 0),
            "lockDueCanonicalMissingCount": lock_due_count,
            "missedLockCount": missed_lock_count,
            "pendingCanonicalStatuses": pending_states,
            "canonicalCoverageComplete": all_canonical,
            "canonicalPredictionComplete": all_canonical,
            "invalidCanonicalRows": invalid,
            "invalidLifecycleStatusRows": lifecycle_validation_errors,
            "readinessValidationWarnings": readiness_validation_warnings,
            "readinessWarningGameCount": len(readiness_validation_warnings),
            "missingGameIdentities": missing_winner_predictions,
            "missingWinnerPredictionGameIdentities": missing_winner_predictions,
            "missingLifecycleDisplayGameIdentities": missing,
            "extraCurrentPredictionIdentities": sorted(set(extra_current)),
            "ambiguousCurrentPredictionIdentities": sorted(set(ambiguous_current)),
            "storeRequested": bool(store),
            "canonicalReadAuthorityWriteCount": 0,
            "prelockPredictionAuthority": (
                "validated_immutable_pregame_snapshot"
                if persisted_prelock_required
                else "scheduled_candidate_generation"
            ),
            "publicPrelockRecomputed": False if persisted_prelock_required else None,
            "invalidPersistedPrelockRows": persisted_prelock_invalid,
            **manifest_authority,
        })
        public["coverageComplete"] = winner_prediction_complete
        public["predictionCoverageComplete"] = winner_prediction_complete
        public["displayStatusCoverageComplete"] = displayed_complete
        public["lifecycleCoverageComplete"] = displayed_complete
        public["coverageStatus"] = coverage["operationalStatus"]

        official = [row for row in predictions if row.get("lockedPrediction") is True]
        exact_vector_verified_count = sum(
            row.get("exactVectorVerified") is True for row in official
        )
        vector_training_excluded_count = sum(
            row.get("exactVectorVerified") is False for row in official
        )
        public.update({
            "exactVectorVerifiedCount": exact_vector_verified_count,
            "vectorTrainingExcludedCount": vector_training_excluded_count,
            "selectionLockIndependentOfTrainingVector": True,
        })
        coverage.update({
            "exactVectorVerifiedCount": exact_vector_verified_count,
            "vectorTrainingExcludedCount": vector_training_excluded_count,
            "selectionLockIndependentOfTrainingVector": True,
        })
        terminal_no_data = [
            row for row in predictions
            if row.get("lockStatus") == "LOCKED_NO_PREDICTION_DATA"
        ]
        prelock = [
            row for row in predictions
            if row.get("lockedPrediction") is not True and row not in terminal_no_data
        ]
        playable = [row for row in predictions if row.get("playable") is True or row.get("playablePick") is True]
        non_playable_official = [row for row in official if row not in playable]
        cards = [_display_card(row) for row in winner_predictions]
        lifecycle_cards = [_display_card(row) for row in predictions]
        official_cards = [_display_card(row) for row in official]
        prelock_cards = [_display_card(row) for row in prelock]
        playable_cards = [_display_card(row) for row in playable]
        out = copy.deepcopy(result or {})
        out.update({
            "sport": "mlb",
            "slate_date": slate,
            "locked": all_canonical,
            "canonicalPredictionComplete": all_canonical,
            "lockStatusComplete": lock_status_complete,
            "lockedPredictionCount": canonical_count,
            "canonicalPredictionCount": canonical_count,
            "lockedStatusCount": locked_status_count,
            "lockOutcomeCount": locked_status_count,
            "noPredictionDataCount": no_prediction_data_count,
            "gameCount": len(manifest_ids),
            "count": len(predictions),
            "allGamesPredicted": winner_prediction_complete,
            "totalPullCountAvailable": len(pulls),
            "scoringPullCount": len(pulls),
            "latestPullAt": (pulls[-1].get("pulled_at") if pulls else None),
            "latestScoringPullAt": (pulls[-1].get("pulled_at") if pulls else None),
            "officialPredictionCount": len(official),
            "officialPickCount": len(official),
            "exactVectorVerifiedCount": exact_vector_verified_count,
            "vectorTrainingExcludedCount": vector_training_excluded_count,
            "preLockPredictionCount": len(prelock),
            "terminalNoPredictionDataDisplayCount": len(terminal_no_data),
            "playablePredictionCount": len(playable),
            "actionablePickCount": len(playable),
            "nonPlayableOfficialPredictionCount": len(non_playable_official),
            "requiredGameWinnerPredictionCount": len(winner_predictions),
            "winnerPredictionCount": len(winner_predictions),
            "displayPredictionCount": len(predictions),
            "lifecycleDisplayCount": len(predictions),
            "predictionCoverageComplete": winner_prediction_complete,
            "displayStatusCoverageComplete": displayed_complete,
            "lifecycleCoverageComplete": displayed_complete,
            "allGamesHaveDisplayedWinnerPrediction": winner_prediction_complete,
            "readinessValidationWarnings": readiness_validation_warnings,
            "readinessWarningGameCount": len(readiness_validation_warnings),
            "slatePredictionLock": public,
            "slateCoverage": coverage,
            "publicPerGameAuthority": {
                "applied": query_error is None,
                "version": AUTHORITY_VERSION,
                "canonicalLockedGameCount": canonical_count,
                "exactVectorVerifiedCount": exact_vector_verified_count,
                "vectorTrainingExcludedCount": vector_training_excluded_count,
                "selectionLockIndependentOfTrainingVector": True,
                "lockedPredictionCount": canonical_count,
                "canonicalPredictionCount": canonical_count,
                "canonicalPredictionComplete": all_canonical,
                "lockedStatusCount": locked_status_count,
                "noPredictionDataCount": no_prediction_data_count,
                "lockStatusComplete": lock_status_complete,
                "pendingCanonicalGameCount": max(len(manifest_ids) - canonical_count, 0),
                "pendingLockStatusGameCount": max(len(manifest_ids) - locked_status_count, 0),
                "lockDueCanonicalMissingCount": lock_due_count,
                "missedLockCount": missed_lock_count,
                "resultLocked": all_canonical,
                "recomputedLockedPredictions": False,
            },
            "operationalDefect": bool(
                query_error
                or invalid
                or lifecycle_validation_errors
                or ambiguous_current
                or not displayed_complete
                or lock_due_count
                or missed_lock_count
            ),
            "predictions": predictions,
            "perGameStatus": lifecycle_cards,
            "requiredWinnerPredictionDisplay": cards,
            "requiredGameLifecycleDisplay": lifecycle_cards,
            "officialPredictionDisplay": official_cards,
            "nonOfficialPredictionDisplay": prelock_cards + [
                _display_card(row) for row in terminal_no_data
            ],
            "playablePredictionDisplay": playable_cards,
            "nonPlayableOfficialPredictionDisplay": [
                _display_card(row) for row in non_playable_official
            ],
        })
        gate = dict(out.get("lastPossiblePredictionGate") or {})
        gate.update({
            "applied": True,
            "policyVersion": AUTHORITY_VERSION,
            "slateWideLock": False,
            "perGameLock": True,
            "finalLockedCount": canonical_count,
            "lockedStatusCount": locked_status_count,
            "noPredictionDataCount": no_prediction_data_count,
            "lockStatusComplete": lock_status_complete,
            "pendingCanonicalGameCount": max(len(manifest_ids) - canonical_count, 0),
            "pendingLockStatusGameCount": max(len(manifest_ids) - locked_status_count, 0),
            "lockDueCanonicalMissingCount": lock_due_count,
            "missedLockCount": missed_lock_count,
            "resultLocked": all_canonical,
        })
        out["lastPossiblePredictionGate"] = gate
        model_version = str(out.get("modelVersion") or "")
        for legacy_suffix in (
            "+slate-wide-45min-final-gate",
            "+last-possible-gate-v4-12h-individual-game-require-sportsdataio",
            "+last-possible-gate-v4-12h-individual-game-odds-api-only",
        ):
            model_version = model_version.replace(legacy_suffix, "")
        authority_suffix = "+last-prelock-promotion-authority-v1"
        if authority_suffix not in model_version:
            model_version += authority_suffix
        out["modelVersion"] = model_version
        for summary_key in (
            "winnerStackV2",
            "rolling24hAccuracyTarget",
            "accuracyTarget",
            "predictionSemantics",
            "mlOverlay",
        ):
            summary = out.get(summary_key)
            if isinstance(summary, dict):
                summary = dict(summary)
                summary.update({
                    "officialPredictionCount": len(official),
                    "officialPickCount": len(official),
                    "preLockPredictionCount": len(prelock),
                })
                if summary_key in {"rolling24hAccuracyTarget", "accuracyTarget"}:
                    summary["lastPossiblePredictionGate"] = copy.deepcopy(gate)
                out[summary_key] = summary
        freeze = dict(out.get("mlFeatureFreeze") or {})
        freeze.update({
            "applied": True,
            "canonicalPublicAuthorityVersion": AUTHORITY_VERSION,
            "frozenRowCount": canonical_count,
            "trainingEligibleCount": len([
                row
                for row in official
                if (row.get("mlFeatureFreeze") or {}).get("trainingEligible") is True
            ]),
            "coverageComplete": all_canonical,
            "pendingRowsAreNotFrozen": True,
        })
        out["mlFeatureFreeze"] = freeze
        return out

    lock_module._locked_result = locked_result
    lock_module._canonical_authority_result = locked_result
    lock_module.POLICY_VERSION = AUTHORITY_VERSION
    lock_module.PUBLIC_PER_GAME_AUTHORITY_VERSION = AUTHORITY_VERSION
    lock_module._INQSI_MLB_SLATE_COVERAGE_PATCH_APPLIED = True
    lock_module._INQSI_MLB_LAST_PRELOCK_PROMOTION_AUTHORITY_APPLIED = True
    return lock_module


def install_public_authority(module: Any, lock_module: Any) -> Any:
    """Make canonical overlay the final public-read authority wrapper."""
    if getattr(module, "_INQSI_MLB_PUBLIC_PER_GAME_AUTHORITY_APPLIED", False):
        return module
    apply(lock_module)
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        persisted_read = bool(
            getattr(
                module,
                "_INQSI_MLB_PERSISTED_PRELOCK_PUBLIC_AUTHORITY_ENABLED",
                False,
            )
            and not _storage_request_active()
        )
        if persisted_read:
            slate = str(lock_module._slate_from_call(args, kwargs, module))
            result = {
                "ok": True,
                "sport": "mlb",
                "slate_date": slate,
                "engine": getattr(module, "ENGINE", None),
                "modelVersion": getattr(module, "MODEL_VERSION", None),
                "predictions": [],
                "readAuthority": "persisted_prelock_and_canonical_locked_only",
            }
        else:
            result = original(*args, **kwargs)
        try:
            return lock_module._canonical_authority_result(
                module,
                result,
                args,
                kwargs,
                bool(kwargs.get("store")),
            )
        except Exception as exc:
            return _fail_closed(result, str(exc))

    def read_persisted_predictions(*args, **kwargs):
        # Public persisted reads replay the same immutable pull, manifest, and
        # pre-lock authorities for every game. Share only those write-once
        # readbacks within this one read-only call; the protected predict/store
        # path remains outside the scope.
        if kwargs.get("store") is not False:
            return patched_predict_all(*args, **kwargs)
        import mlb_daily_per_game_lock_patch as per_game

        with per_game._status_read_scope():
            return patched_predict_all(*args, **kwargs)

    module.predict_all = patched_predict_all
    module.read_persisted_predictions = read_persisted_predictions
    module.MLB_PUBLIC_PER_GAME_AUTHORITY_VERSION = AUTHORITY_VERSION
    module._INQSI_MLB_PUBLIC_PER_GAME_AUTHORITY_APPLIED = True
    return module
