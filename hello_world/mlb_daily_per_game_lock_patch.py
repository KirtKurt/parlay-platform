from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

import inqsi_pull_history as history_contract
import mlb_official_schedule_authority as official_schedule_contract
from mlb_slate_coverage_patch import (
    AUTHORITY_VERSION as PREGAME_PUBLIC_AUTHORITY_VERSION,
    game_identity,
    resolve_playability_lifecycle,
)

PAYLOAD_FINGERPRINT_VERSION = history_contract.CANONICAL_PAYLOAD_FINGERPRINT_VERSION
OFFICIAL_SCHEDULE_AUTHORITY_VERSION = official_schedule_contract.VERSION
OFFICIAL_SCHEDULE_AUTHORITY_SOURCE = official_schedule_contract.SOURCE
LEGACY_IDENTITY_CROSSWALK_MAX_DRIFT_SECONDS = 15 * 60


VERSION = "INQSI-MLB-DAILY-LOCK-v5-tminus45-readiness-release-status"
LOCK_POLICY = "each_mlb_game_minus_45_minutes"
REQUIRED_LOCK_MINUTES = 45
STAGE_RECORD_TYPE = "mlb_staged_per_game_tminus45_lock"
CUTOFF_STABILIZATION_SECONDS = 0
SOURCE_WINDOW_VERSION = "mlb_per_game_cutoff_source_window_v1"
ATTEMPT_DIAGNOSTICS_VERSION = "MLB-PER-GAME-LOCK-DIAGNOSTICS-v1-append-only"
ATTEMPT_RECORD_TYPE = "mlb_per_game_lock_attempt_diagnostic"
ATTEMPT_OUTCOME_RECORD_TYPE = "mlb_per_game_lock_attempt_outcome_diagnostic"
PROMOTION_POLICY_VERSION = "MLB-LAST-PRELOCK-PROMOTION-v1-at-cutoff-no-rescore"
PREGAME_SNAPSHOT_RECORD_TYPE = "mlb_immutable_prelock_prediction_snapshot"
LIVE_PREDICTION_RECORD_TYPE = "mlb_single_game_moneyline_prediction"
PREGAME_SNAPSHOT_VERSION = "MLB-PREGAME-PREDICTION-SNAPSHOT-v3-user-visible-platform-prelock"
PREGAME_PERSISTENCE_PROOF_TYPE = "DDB_LIVE_PREDICTION_PUT_SUCCESS_ACK-v1"
PREGAME_SNAPSHOT_ROLE = "USER_VISIBLE_PLATFORM_PRELOCK"
PREGAME_DISPLAY_STATUS = "PRE_LOCK_PLATFORM_PREDICTION"
PREGAME_DISPLAY_SURFACE = "nonOfficialPredictionDisplay"
READINESS_VERSION = "MLB-LOCK-READINESS-v1-tminus60-tminus50"
READINESS_RECORD_TYPE = "mlb_per_game_lock_readiness_checkpoint"
LOCK_OUTCOME_VERSION = "MLB-LOCK-OUTCOME-v1-explicit-terminal-status"
LOCK_OUTCOME_RECORD_TYPE = "mlb_immutable_per_game_lock_outcome"
RELEASE_ASSESSMENT_VERSION = "MLB-PLAYABILITY-ASSESSMENT-v1-immutable-selection-bound"
RELEASE_ASSESSMENT_RECORD_TYPE = "mlb_immutable_playability_assessment"
READINESS_CHECKPOINT_MINUTES = (60, 50)
RELEASE_CHECKPOINT_MINUTES = (30, 15)
CHECKPOINT_MAX_LATE_SECONDS = 120
PLAYABILITY_EVIDENCE_MAX_AGE_MINUTES = 20

_DIAGNOSTIC_STATES = {
    "WAITING_FOR_CUTOFF_STABILIZATION",
    "DUE_NOT_STAGED",
    "INVALID_STAGE_BLOCKED",
    "STAGED_CANONICAL_WRITE_BLOCKED",
    "MISSED_NOT_BACKFILLED",
}

_SCOPED_PULLS: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "inqsi_mlb_scoped_per_game_lock_pulls",
    default=None,
)


def _supported_payload_fingerprint_version(value: Any) -> bool:
    # Missing means a legacy snapshot written before the algorithm identifier
    # was added.  It is still required to match the canonical persisted-row
    # hash; unknown named algorithms always fail closed.
    return value in (None, "", PAYLOAD_FINGERPRINT_VERSION)


def _public_prelock_marker_errors(
    item: Dict[str, Any],
    row: Dict[str, Any],
) -> List[str]:
    """Validate the explicit public-display authority carried by a snapshot."""
    errors: List[str] = []
    if item.get("snapshot_version") != PREGAME_SNAPSHOT_VERSION:
        errors.append("persisted_prelock_snapshot_version_mismatch")
    if item.get("snapshot_role") != PREGAME_SNAPSHOT_ROLE:
        errors.append("persisted_prelock_snapshot_role_mismatch")
    if item.get("public_authority_version") != PREGAME_PUBLIC_AUTHORITY_VERSION:
        errors.append("persisted_prelock_public_authority_version_mismatch")
    if item.get("user_visible") is not True:
        errors.append("persisted_prelock_user_visible_marker_missing")
    if item.get("display_prediction") is not True:
        errors.append("persisted_prelock_display_prediction_marker_missing")
    if item.get("display_status") != PREGAME_DISPLAY_STATUS:
        errors.append("persisted_prelock_display_status_mismatch")
    if item.get("display_surface") != PREGAME_DISPLAY_SURFACE:
        errors.append("persisted_prelock_display_surface_mismatch")
    # Snapshot v3 was introduced together with the named canonical hash
    # contract.  A missing algorithm identifier can only belong to an older
    # snapshot and must not be accepted under the v3 public-authority markers.
    if item.get("prediction_payload_fingerprint_version") != PAYLOAD_FINGERPRINT_VERSION:
        errors.append("persisted_prelock_payload_fingerprint_version_mismatch")

    per_game = row.get("perGameCanonicalLock") or {}
    tags = {str(value) for value in (row.get("tags") or [])}
    if row.get("lockedPrediction") is not False:
        errors.append("persisted_prelock_row_locked_marker_invalid")
    if row.get("officialPrediction") is not False:
        errors.append("persisted_prelock_row_official_marker_invalid")
    if row.get("officialPredictionStatus") != PREGAME_DISPLAY_STATUS:
        errors.append("persisted_prelock_row_display_status_mismatch")
    if row.get("displayPrediction") is not True:
        errors.append("persisted_prelock_row_display_prediction_missing")
    if row.get("displayGroup") != "pre_lock_prediction":
        errors.append("persisted_prelock_row_display_group_mismatch")
    if "PRE_LOCK_PREDICTION" not in tags:
        errors.append("persisted_prelock_row_display_tag_missing")
    if not isinstance(per_game, dict):
        errors.append("persisted_prelock_row_public_authority_missing")
        per_game = {}
    if per_game.get("authorityVersion") != PREGAME_PUBLIC_AUTHORITY_VERSION:
        errors.append("persisted_prelock_row_public_authority_version_mismatch")
    if per_game.get("status") != "OPEN_PRE_LOCK":
        errors.append("persisted_prelock_row_public_status_not_open")
    if per_game.get("canonical") is not False:
        errors.append("persisted_prelock_row_canonical_marker_invalid")
    signal_policy = row.get("signalPolicyV13") or {}
    row_signal_version = (
        signal_policy.get("version") if isinstance(signal_policy, dict) else None
    )
    if not row_signal_version:
        errors.append("persisted_prelock_signal_policy_version_missing")
    if not item.get("signal_policy_version"):
        errors.append("persisted_prelock_snapshot_signal_policy_version_missing")
    if item.get("signal_policy_version") != row_signal_version:
        errors.append("persisted_prelock_signal_policy_version_mismatch")
    return sorted(set(errors))


def _parse_dt(module: Any, value: Any) -> Optional[datetime]:
    parsed = module._parse_dt(value)
    return parsed.astimezone(timezone.utc) if parsed else None


def _start(module: Any, game: Dict[str, Any]) -> Optional[datetime]:
    return _parse_dt(module, game.get("commence_time") or game.get("commenceTime"))


def _lock_at(module: Any, game: Dict[str, Any]) -> Optional[datetime]:
    start = _start(module, game)
    return start - timedelta(minutes=module.LOCK_MINUTES) if start else None


def _pull_at(module: Any, pull: Dict[str, Any]) -> Optional[datetime]:
    return _parse_dt(module, pull.get("pulled_at") or pull.get("asof") or pull.get("created_at"))


def _has_moneyline(game: Dict[str, Any]) -> bool:
    for payload in (game.get("books") or {}).values():
        market = (payload or {}).get("ml") or (payload or {}).get("moneyline") or {}
        if market.get("home") not in (None, "") and market.get("away") not in (None, ""):
            return True
    return False


def _official_game_pk(game: Dict[str, Any]) -> str:
    return str(game.get("official_game_pk") or game.get("officialGamePk") or "")


def _identity_start(game: Dict[str, Any]) -> Optional[datetime]:
    value = game.get("commence_time") or game.get("commenceTime")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _exact_game_match(reference: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    reference_official = _official_game_pk(reference)
    candidate_official = _official_game_pk(candidate)
    if reference_official and candidate_official:
        return reference_official == candidate_official
    return game_identity(reference) == game_identity(candidate)


def _legacy_identity_crosswalk_match(
    reference: Dict[str, Any],
    candidate: Dict[str, Any],
) -> bool:
    # This migration-only bridge is used when exactly one side predates the
    # official-game-pk fields. Exact ordered teams plus a tight start window
    # keep same-team doubleheaders distinct and fail closed on ambiguity.
    if bool(_official_game_pk(reference)) == bool(_official_game_pk(candidate)):
        return False
    if (
        _norm(reference.get("home_team") or reference.get("homeTeam"))
        != _norm(candidate.get("home_team") or candidate.get("homeTeam"))
        or _norm(reference.get("away_team") or reference.get("awayTeam"))
        != _norm(candidate.get("away_team") or candidate.get("awayTeam"))
    ):
        return False
    reference_start = _identity_start(reference)
    candidate_start = _identity_start(candidate)
    return bool(
        reference_start
        and candidate_start
        and abs((reference_start - candidate_start).total_seconds())
        <= LEGACY_IDENTITY_CROSSWALK_MAX_DRIFT_SECONDS
    )


def _same_game(reference: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    return _exact_game_match(reference, candidate) or _legacy_identity_crosswalk_match(
        reference,
        candidate,
    )


def _matching_game(pull: Dict[str, Any], reference: Any) -> Optional[Dict[str, Any]]:
    games = [game for game in pull.get("games") or [] if _has_moneyline(game)]
    if not isinstance(reference, dict):
        matches = [game for game in games if game_identity(game) == str(reference or "")]
        return matches[0] if len(matches) == 1 else None
    exact = [game for game in games if _exact_game_match(reference, game)]
    if exact:
        return exact[0] if len(exact) == 1 else None
    fallback = [
        game for game in games if _legacy_identity_crosswalk_match(reference, game)
    ]
    if not fallback:
        return None
    reference_start = _identity_start(reference)
    ranked = sorted(
        (
            abs(((_identity_start(game) or reference_start) - reference_start).total_seconds()),
            _raw_game_identity(game),
            game,
        )
        for game in fallback
        if reference_start is not None and _identity_start(game) is not None
    )
    if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        return None
    return ranked[0][2]


def _scoring_pulls(
    module: Any,
    pulls: Iterable[Dict[str, Any]],
    game: Dict[str, Any],
    *,
    at_or_before: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return only valid snapshots for one game at/before its scheduled cutoff."""
    lock_at = _lock_at(module, game)
    cutoff = (
        min(lock_at, at_or_before.astimezone(timezone.utc))
        if lock_at and at_or_before is not None
        else lock_at
    )
    if not cutoff:
        return []
    selected: List[Dict[str, Any]] = []
    for pull in sorted(pulls or [], key=lambda item: _pull_at(module, item) or datetime.min.replace(tzinfo=timezone.utc)):
        pulled_at = _pull_at(module, pull)
        if not pulled_at or pulled_at > cutoff:
            continue
        matching = _matching_game(pull, game)
        if not matching:
            continue
        scoped = copy.deepcopy(pull)
        scoped["games"] = [copy.deepcopy(matching)]
        selected.append(scoped)
    return selected


def _game_snapshot_fingerprint(game: Dict[str, Any]) -> str:
    payload = json.dumps(_plain(game), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_window_entry(module: Any, pull: Dict[str, Any], game: Dict[str, Any]) -> Dict[str, Any]:
    matching = _matching_game(pull, game)
    pulled_at = _pull_at(module, pull)
    return {
        "pullId": str(pull.get("pull_id") or ""),
        "pulledAtUtc": pulled_at.isoformat() if pulled_at else None,
        "gameSnapshotFingerprint": _game_snapshot_fingerprint(matching or {}),
    }


def _source_window_entries(module: Any, scoring: Iterable[Dict[str, Any]], game: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [_source_window_entry(module, pull, game) for pull in scoring]


def _source_window_key(entry: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(entry.get("pullId") or ""),
        str(entry.get("pulledAtUtc") or ""),
        str(entry.get("gameSnapshotFingerprint") or ""),
    )


def _cutoff_stable_at(module: Any, game: Dict[str, Any]) -> Optional[datetime]:
    lock_at = _lock_at(module, game)
    return lock_at + timedelta(seconds=CUTOFF_STABILIZATION_SECONDS) if lock_at else None


def _install_scoped_query(history: Any) -> None:
    """Install a ContextVar-based read scope without changing the normal query path."""
    if getattr(history, "_INQSI_MLB_PER_GAME_QUERY_SCOPE_V1", False):
        return
    original = history.query_pulls

    def query_pulls(sport: str, date: Optional[str] = None, limit: int = 500):
        scoped = _SCOPED_PULLS.get()
        requested_sport = str(sport or "").strip().lower()
        if scoped and requested_sport == "mlb" and str(date or "") == str(scoped.get("slate")):
            size = min(max(int(limit), 1), 500)
            return copy.deepcopy(list(scoped.get("pulls") or [])[:size])
        return original(sport, date, limit)

    history.query_pulls = query_pulls
    history._INQSI_MLB_PER_GAME_QUERY_SCOPE_V1 = True


@contextmanager
def _pull_scope(history: Any, slate: str, pulls: List[Dict[str, Any]]):
    _install_scoped_query(history)
    token = _SCOPED_PULLS.set({"slate": slate, "pulls": copy.deepcopy(pulls)})
    try:
        yield
    finally:
        _SCOPED_PULLS.reset(token)


def _stage_sk(module: Any, game: Dict[str, Any]) -> str:
    digest = hashlib.sha256(game_identity(game).encode("utf-8")).hexdigest()
    return f"PER_GAME_LOCK#TMINUS{module.LOCK_MINUTES}#{digest}"


def _stage_key(module: Any, slate: str, game: Dict[str, Any]) -> Dict[str, str]:
    return {"PK": module._lock_pk(slate), "SK": _stage_sk(module, game)}


def _game_digest(game: Dict[str, Any]) -> str:
    return hashlib.sha256(game_identity(game).encode("utf-8")).hexdigest()


def _readiness_key(module: Any, slate: str, game: Dict[str, Any], minutes: int) -> Dict[str, str]:
    return {
        "PK": module._lock_pk(slate),
        "SK": f"PER_GAME_READINESS#TMINUS{minutes}#{_game_digest(game)}",
    }


def _lock_outcome_key(module: Any, slate: str, game: Dict[str, Any]) -> Dict[str, str]:
    return {
        "PK": module._lock_pk(slate),
        "SK": f"PER_GAME_LOCK_OUTCOME#TMINUS{module.LOCK_MINUTES}#{_game_digest(game)}",
    }


def _release_key(module: Any, slate: str, game: Dict[str, Any], checkpoint: str) -> Dict[str, str]:
    return {
        "PK": module._lock_pk(slate),
        "SK": f"PER_GAME_PLAYABILITY#{checkpoint}#{_game_digest(game)}",
    }


def _get_record(module: Any, key: Dict[str, str]) -> Optional[Dict[str, Any]]:
    try:
        item = module.TABLE.get_item(Key=key, ConsistentRead=True).get("Item")
        return item if isinstance(item, dict) else None
    except Exception:
        return None


def _put_write_once_record(
    module: Any,
    item: Dict[str, Any],
    *,
    fingerprint_field: str,
) -> Dict[str, Any]:
    prepared = module.history.ddb_safe(copy.deepcopy(item))
    material = {
        str(key): value
        for key, value in _plain(prepared).items()
        if key != fingerprint_field
    }
    prepared[fingerprint_field] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    key = {"PK": prepared["PK"], "SK": prepared["SK"]}
    try:
        module.TABLE.put_item(
            Item=prepared,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return prepared
    except Exception as exc:
        if not _conditional_collision(exc):
            raise
        existing = _get_record(module, key)
        if existing and existing.get(fingerprint_field) == prepared.get(fingerprint_field):
            return existing
        # Readiness and playability rows are diagnostics bound to a semantic
        # checkpoint. Overlapping one-minute invocations can legitimately race
        # with different evaluation timestamps. The first immutable row wins as
        # long as it is for the exact same game/checkpoint contract; a race must
        # never bubble up into the T-45 lock path.
        same_checkpoint = bool(
            existing
            and existing.get("record_type") == prepared.get("record_type")
            and existing.get("version") == prepared.get("version")
            and existing.get("slate_date") == prepared.get("slate_date")
            and existing.get("game_identity") == prepared.get("game_identity")
            and existing.get("checkpoint") == prepared.get("checkpoint")
            and (
                str(prepared.get("checkpoint") or "").startswith("EVENT_GAME1_")
                or (
                    existing.get("scheduled_at_utc") == prepared.get("scheduled_at_utc")
                    and existing.get("evidence_cutoff_at_utc")
                    == prepared.get("evidence_cutoff_at_utc")
                )
            )
            and (
                prepared.get("record_type") == READINESS_RECORD_TYPE
                or (
                    prepared.get("record_type") == RELEASE_ASSESSMENT_RECORD_TYPE
                    and existing.get("canonical_selection_fingerprint")
                    == prepared.get("canonical_selection_fingerprint")
                )
            )
        )
        if not same_checkpoint:
            raise RuntimeError("write_once_record_collision_mismatch") from exc
        return existing


def _readiness_checkpoint(
    module: Any,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    game: Dict[str, Any],
    minutes: int,
    evaluated_at: datetime,
    *,
    timing_status: str = "ON_TIME",
) -> Dict[str, Any]:
    key = _readiness_key(module, slate, game, minutes)
    existing = _get_record(module, key)
    if existing:
        return existing
    lock_at = _lock_at(module, game)
    start = _start(module, game)
    scheduled_at = start - timedelta(minutes=minutes) if start else None
    scoring = (
        _scoring_pulls(
            module,
            pulls,
            game,
            at_or_before=scheduled_at,
        )
        if timing_status == "ON_TIME" and scheduled_at
        else []
    )
    source = scoring[-1] if scoring else None
    source_at = _pull_at(module, source or {})
    candidate = None
    candidate_proof = None
    candidate_errors: List[str] = []
    if scoring:
        candidate, candidate_proof, _, candidate_errors = _last_prelock_candidate(
            module,
            slate,
            game,
            scoring,
            at_or_before=scheduled_at,
        )
    reasons: List[str] = []
    if timing_status != "ON_TIME":
        reasons.append("checkpoint_window_missed")
    if game_identity(game) not in {game_identity(entry) for entry in manifest}:
        reasons.append("game_not_in_durable_manifest")
    if not start or not lock_at:
        reasons.append("invalid_game_start_or_cutoff")
    if not scoring:
        reasons.append("no_moneyline_pull_available")
    if len(scoring) < module.MIN_PULLS_PER_GAME_FOR_LOCK:
        reasons.append("insufficient_pull_depth")
    if source_at and scheduled_at and scheduled_at >= source_at:
        source_age = round((scheduled_at - source_at).total_seconds() / 60.0, 2)
        if source_age > module.MAX_LATEST_PULL_AGE_MINUTES:
            reasons.append("latest_candidate_source_stale")
    else:
        source_age = None
    reasons.extend(str(reason) for reason in candidate_errors)
    candidate_ready = bool(candidate and candidate_proof and not candidate_errors)
    status = (
        "MISSED"
        if timing_status != "ON_TIME"
        else "READY"
        if candidate_ready and not reasons
        else "AT_RISK"
        if scoring
        else "NOT_READY"
    )
    item = {
        **key,
        "record_type": READINESS_RECORD_TYPE,
        "version": READINESS_VERSION,
        "sport": "mlb",
        "slate_date": slate,
        "game_identity": game_identity(game),
        "game_id": game.get("game_id") or game.get("id"),
        "commence_time": start.isoformat() if start else None,
        "checkpoint": f"T_MINUS_{minutes}",
        "checkpoint_timing_status": timing_status,
        "scheduled_at_utc": scheduled_at.isoformat() if scheduled_at else None,
        "evaluated_at_utc": evaluated_at.isoformat(),
        "evidence_cutoff_at_utc": scheduled_at.isoformat() if scheduled_at else None,
        "scheduled_lock_at_utc": lock_at.isoformat() if lock_at else None,
        "schedule_authority_ready": bool(manifest and start and lock_at),
        "candidate_ready": candidate_ready,
        "candidate_integrity_valid": candidate_ready,
        "candidate_selection_fingerprint": (candidate_proof or {}).get("candidateSelectionFingerprint"),
        "candidate_source_at_utc": source_at.isoformat() if source_at else None,
        "source_age_minutes": source_age,
        "pull_depth": len(scoring),
        "status": status,
        "blocking_reasons": sorted(set(reasons)),
        "write_once": True,
        "created_at": evaluated_at.isoformat(),
    }
    return _put_write_once_record(module, item, fingerprint_field="readiness_fingerprint")


def _ensure_readiness_checkpoints(
    module: Any,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    now: datetime,
) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    for game in manifest:
        start = _start(module, game)
        lock_at = _lock_at(module, game)
        if not start or not lock_at:
            continue
        for minutes in READINESS_CHECKPOINT_MINUTES:
            scheduled = start - timedelta(minutes=minutes)
            if now < scheduled:
                continue
            timing_status = (
                "ON_TIME"
                if now <= scheduled + timedelta(seconds=CHECKPOINT_MAX_LATE_SECONDS)
                else "MISSED_WINDOW"
            )
            try:
                _readiness_checkpoint(
                    module,
                    slate,
                    pulls,
                    manifest,
                    game,
                    minutes,
                    now,
                    timing_status=timing_status,
                )
            except Exception as exc:
                errors.append({
                    "gameIdentity": game_identity(game),
                    "checkpoint": f"T_MINUS_{minutes}",
                    "error": f"{type(exc).__name__}:{exc}",
                })
    return errors


def _readiness_status(module: Any, slate: str, game: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for minutes in READINESS_CHECKPOINT_MINUTES:
        item = _get_record(module, _readiness_key(module, slate, game, minutes))
        result[f"tMinus{minutes}"] = {
            "recorded": bool(item),
            "status": (item or {}).get("status"),
            "timingStatus": (item or {}).get("checkpoint_timing_status"),
            "evaluatedAtUtc": (item or {}).get("evaluated_at_utc"),
            "candidateReady": (item or {}).get("candidate_ready") is True,
            "blockingReasons": list((item or {}).get("blocking_reasons") or []),
        }
    return result


def _put_no_prediction_outcome(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    now: datetime,
    reasons: Iterable[str],
    provider_manifest_authority: Dict[str, Any],
) -> Dict[str, Any]:
    key = _lock_outcome_key(module, slate, game)
    existing = _get_record(module, key)
    if existing:
        return existing
    start = _start(module, game)
    lock_at = _lock_at(module, game)
    if not isinstance(provider_manifest_authority, dict) or not provider_manifest_authority:
        raise RuntimeError("no_prediction_outcome_manifest_authority_missing")
    item = {
        **key,
        "record_type": LOCK_OUTCOME_RECORD_TYPE,
        "version": LOCK_OUTCOME_VERSION,
        "sport": "mlb",
        "slate_date": slate,
        "game_identity": game_identity(game),
        "game_id": game.get("game_id") or game.get("id"),
        "commence_time": start.isoformat() if start else None,
        "scheduled_lock_at_utc": lock_at.isoformat() if lock_at else None,
        "recorded_at_utc": now.isoformat(),
        "lock_status": "LOCKED_NO_PREDICTION_DATA",
        "lock_outcome_recorded": True,
        "locked_prediction": False,
        "canonical": False,
        "official_prediction": False,
        "playable": False,
        "blocked": True,
        "playability_block_reasons": ["NO_VALID_PREGAME_PREDICTION"],
        "training_eligible": False,
        "training_exclusion_reasons": ["missing_immutable_prediction"],
        "reasons": sorted(set(str(reason) for reason in reasons if reason)),
        "provider_manifest_authority": copy.deepcopy(provider_manifest_authority),
        "provider_manifest_fingerprint": provider_manifest_authority.get("fingerprint"),
        "provider_manifest_pk": provider_manifest_authority.get("pk"),
        "provider_manifest_sk": provider_manifest_authority.get("sk"),
        "manifest_game_count": provider_manifest_authority.get("gameCount"),
        "data": {
            "manifestGameIdentities": list(provider_manifest_authority.get("canonicalGameIdentities") or []),
            "row": {
                "homeTeam": game.get("home_team") or game.get("homeTeam"),
                "awayTeam": game.get("away_team") or game.get("awayTeam"),
            },
        },
        "write_once": True,
        "created_at": now.isoformat(),
    }
    return _put_write_once_record(module, item, fingerprint_field="lock_outcome_fingerprint")


def _get_lock_outcome(module: Any, slate: str, game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    item = _get_record(module, _lock_outcome_key(module, slate, game))
    if not item:
        return None
    material = {
        str(key): value
        for key, value in _plain(item).items()
        if key != "lock_outcome_fingerprint"
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    errors: List[str] = []
    if item.get("record_type") != LOCK_OUTCOME_RECORD_TYPE or item.get("version") != LOCK_OUTCOME_VERSION:
        errors.append("lock_outcome_contract_mismatch")
    if str(item.get("slate_date") or "") != slate or str(item.get("game_identity") or "") != game_identity(game):
        errors.append("lock_outcome_identity_mismatch")
    if item.get("lock_outcome_fingerprint") != fingerprint:
        errors.append("lock_outcome_fingerprint_mismatch")
    errors.extend(_provider_manifest_authority_errors(module.TABLE, item))
    if errors:
        return None
    return item


def _is_no_prediction_candidate_failure(errors: Iterable[str]) -> bool:
    values = {str(error) for error in errors if error}
    # A complete absence is a terminal data outcome.  A candidate that exists
    # but fails integrity validation is an operational/security failure and
    # must stay retryable and fail closed rather than being relabelled no-data.
    return values in (
        {"no_persisted_user_visible_platform_prelock_prediction_at_or_before_cutoff"},
        {"no_valid_user_visible_platform_prelock_prediction"},
    )


def _ordered_team_identity(game: Dict[str, Any]) -> Tuple[str, str]:
    """Return the provider-normalized away/home identity without swapping sides."""
    return (
        official_schedule_contract.normalize_team(
            game.get("away_team") or game.get("awayTeam")
        ),
        official_schedule_contract.normalize_team(
            game.get("home_team") or game.get("homeTeam")
        ),
    )


def _ordered_teams_match(
    reference: Dict[str, Any],
    candidate: Dict[str, Any],
) -> bool:
    expected = _ordered_team_identity(reference)
    actual = _ordered_team_identity(candidate)
    return bool(all(expected) and expected == actual)


def _doubleheader_game_one(manifest: List[Dict[str, Any]], game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    teams = _ordered_team_identity(game)
    matches = [
        entry for entry in manifest
        if _ordered_team_identity(entry) == teams
    ]
    matches.sort(key=lambda entry: (str(entry.get("commence_time") or entry.get("commenceTime") or ""), game_identity(entry)))
    if len(matches) < 2 or game_identity(game) != game_identity(matches[-1]):
        return None
    return matches[0]


def _stage_outcome_alias(
    module: Any,
    slate: str,
    game: Dict[str, Any],
) -> Tuple[Optional[str], bool]:
    """Read a provider alias only from the exact immutable Game 1 stage proof."""
    official_pk = _official_game_pk(game)
    if not official_pk:
        return None, True
    stage = _get_record(module, _stage_key(module, slate, game))
    if not stage:
        return None, True
    row = ((stage.get("data") or {}).get("row") or {})
    proof = stage.get("candidate_proof") or {}
    try:
        persisted_errors = persisted_stage_authority_errors(module.TABLE, stage)
        fingerprint_valid = stage.get("stage_fingerprint") == _stage_fingerprint(stage)
    except Exception:
        return None, False
    if (
        persisted_errors
        or stage.get("record_type") != STAGE_RECORD_TYPE
        or str(stage.get("slate_date") or "") != slate
        or str(stage.get("game_identity") or "") != game_identity(game)
        or not fingerprint_valid
        or not _ordered_teams_match(game, row)
        or _official_game_pk(stage) != official_pk
        or _official_game_pk(row) != official_pk
        or str(proof.get("stageOfficialGamePk") or "") != official_pk
        or str(proof.get("candidateOfficialGamePk") or "") != official_pk
    ):
        return None, False

    canonical_id = _raw_game_identity(game)
    proof_alias = str(proof.get("candidateGameIdentity") or "")
    explicit_aliases = {
        str(value)
        for value in (
            stage.get("provider_event_id"),
            row.get("providerEventId"),
            row.get("sourcePredictionGameId"),
            row.get("sourcePredictionGameIdentity"),
        )
        if value not in (None, "") and str(value) != canonical_id
    }
    if not proof_alias or proof_alias == canonical_id:
        return None, bool(
            proof_alias == canonical_id
            and proof.get("identityBindingMode") == "exact_identity"
            and not explicit_aliases
        )
    if proof.get("identityBindingMode") != "official_game_pk":
        return None, False
    if explicit_aliases and explicit_aliases != {proof_alias}:
        return None, False
    return proof_alias, True


def _current_official_crosswalk_alias(
    module: Any,
    slate: str,
    game: Dict[str, Any],
) -> Tuple[Optional[str], bool]:
    """Resolve one immutable whole-history official-PK/provider-ID mapping."""
    official_pk = _official_game_pk(game)
    if not official_pk:
        return None, True
    try:
        pulls = sorted(
            module._pulls_for_date(slate),
            key=lambda pull: _pull_at(module, pull)
            or datetime.min.replace(tzinfo=timezone.utc),
        )
    except Exception:
        return None, False

    authority_reader = getattr(
        module.history,
        "provider_manifest_authority_for_lock",
        None,
    )
    if not callable(authority_reader):
        return None, False

    pk_to_aliases: Dict[str, set[str]] = {}
    alias_to_pks: Dict[str, set[str]] = {}
    target_seen = False
    for pull in pulls:
        manifest = pull.get("provider_schedule_manifest")
        if not isinstance(manifest, dict):
            continue
        manifest_games = list(manifest.get("games") or [])
        try:
            # This authority reader calls validate_provider_schedule_manifest
            # with verify_immutable_storage=True before returning its proof.
            authority = authority_reader(pull, slate, manifest_games)
        except Exception:
            return None, False
        if (
            not isinstance(authority, dict)
            or authority.get("immutable") is not True
            or authority.get("writeOnce") is not True
            or authority.get("consistentReadVerified") is not True
        ):
            return None, False

        for candidate in manifest_games:
            candidate_pk = _official_game_pk(candidate)
            if not candidate_pk:
                continue
            if candidate_pk == official_pk:
                target_seen = True
                if not _ordered_teams_match(game, candidate):
                    return None, False
            official_id = str(
                candidate.get("official_game_id")
                or f"mlb_statsapi:{candidate_pk}"
            )
            canonical_id = _raw_game_identity(candidate)
            explicit_provider_id = str(
                candidate.get("provider_event_id")
                or candidate.get("providerEventId")
                or ""
            )
            provider_ids = {
                value
                for value in (explicit_provider_id, canonical_id)
                if value and value != official_id
            }
            if len(provider_ids) > 1:
                return None, False
            if not provider_ids:
                # A later provider-missing/fallback manifest does not erase an
                # earlier immutable provider alias for this official game.
                continue
            provider_id = next(iter(provider_ids))
            pk_to_aliases.setdefault(candidate_pk, set()).add(provider_id)
            alias_to_pks.setdefault(provider_id, set()).add(candidate_pk)

    if not target_seen:
        return None, True
    if any(len(values) > 1 for values in pk_to_aliases.values()):
        return None, False
    if any(len(values) > 1 for values in alias_to_pks.values()):
        return None, False
    aliases = pk_to_aliases.get(official_pk) or set()
    return (next(iter(aliases)) if aliases else None), True


def _game_outcome_aliases(
    module: Any,
    slate: str,
    game: Dict[str, Any],
) -> Optional[List[str]]:
    canonical_id = str(game.get("game_id") or game.get("id") or "")
    caller_aliases = {
        str(value)
        for value in (
            game.get("provider_event_id"),
            game.get("providerEventId"),
        )
        if value not in (None, "") and str(value) != canonical_id
    }
    stage_alias, stage_valid = _stage_outcome_alias(module, slate, game)
    current_alias, current_valid = _current_official_crosswalk_alias(
        module,
        slate,
        game,
    )
    if not stage_valid or not current_valid:
        return None
    provider_aliases = {
        alias for alias in (stage_alias, current_alias) if alias
    }
    # Caller fields are consistency assertions only. They can never create an
    # outcome alias without an immutable stage or manifest crosswalk proof.
    if caller_aliases and caller_aliases != provider_aliases:
        return None
    # Provider event IDs are stable. Conflicting aliases for one official PK
    # are ambiguous (especially in a same-team doubleheader), so never guess.
    if len(provider_aliases) > 1:
        return None
    return [
        alias
        for alias in (canonical_id, *sorted(provider_aliases))
        if alias
    ]


def _outcome_matches_alias(
    item: Any,
    alias: str,
    game: Dict[str, Any],
) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("completed") is not True or str(item.get("game_id") or "") != alias:
        return False
    if item.get("home_team") not in (None, "") or item.get("away_team") not in (None, ""):
        if not _ordered_teams_match(game, item):
            return False
    item_official_pk = _official_game_pk(item)
    return not item_official_pk or item_official_pk == _official_game_pk(game)


def _game_final(module: Any, slate: str, game: Dict[str, Any]) -> bool:
    table = getattr(module, "OUTCOMES", None)
    if table is None:
        return False
    game_key = game.get("game_key") or game.get("gameKey")
    canonical_id = str(game.get("game_id") or game.get("id") or "")
    if canonical_id:
        try:
            canonical = table.get_item(
                Key={
                    "PK": f"OUTCOME#mlb#{slate}",
                    "SK": f"GAME_ID#{canonical_id}",
                },
                ConsistentRead=True,
            ).get("Item")
        except Exception:
            return False
        if canonical is not None:
            return _outcome_matches_alias(canonical, canonical_id, game)
    aliases = _game_outcome_aliases(module, slate, game)
    if aliases is None or (not game_key and not aliases):
        return False
    try:
        found: List[Tuple[str, Dict[str, Any]]] = []
        for alias in aliases:
            item = table.get_item(
                Key={
                    "PK": f"OUTCOME#mlb#{slate}",
                    "SK": f"GAME_ID#{alias}",
                },
                ConsistentRead=True,
            ).get("Item")
            if item is not None:
                found.append((alias, item))
        if not found and game_key:
            item = table.get_item(
                Key={"PK": f"OUTCOME#mlb#{slate}", "SK": f"GAME#{game_key}"},
                ConsistentRead=True,
            ).get("Item")
            if isinstance(item, dict):
                item_id = str(item.get("game_id") or "")
                if item_id in aliases:
                    found.append((item_id, item))
    except Exception:
        return False
    return bool(found) and all(
        _outcome_matches_alias(item, alias, game)
        for alias, item in found
    )


def _latest_candidate_evidence_before(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    before: datetime,
) -> Optional[Tuple[datetime, Dict[str, Any]]]:
    selected: Optional[Tuple[datetime, Dict[str, Any]]] = None
    pulls = sorted(
        module._pulls_for_date(slate),
        key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc),
    )
    scoring = _scoring_pulls(module, pulls, game, at_or_before=before)
    aliases = {_raw_game_identity(game)}
    for pull in scoring:
        for candidate_game in pull.get("games") or []:
            if _same_game(game, candidate_game):
                aliases.add(_raw_game_identity(candidate_game))
    expected_home = _norm(game.get("home_team") or game.get("homeTeam"))
    expected_away = _norm(game.get("away_team") or game.get("awayTeam"))
    for item in _candidate_items(module, slate, game, scoring):
        row = (item.get("data") or {}).get("row") or item.get("data") or {}
        if not isinstance(row, dict):
            continue
        if not _same_game(game, row) and _raw_game_identity(row) not in aliases:
            continue
        if (
            _norm(row.get("homeTeam") or row.get("home_team")) != expected_home
            or _norm(row.get("awayTeam") or row.get("away_team")) != expected_away
        ):
            continue
        created_at = _candidate_created_at(item, row)
        persisted_at = _candidate_persisted_at(item)
        source_at = _candidate_source_at(item, row)
        if (
            not created_at
            or not persisted_at
            or not source_at
            or created_at > before
            or persisted_at > before
            or source_at > before
        ):
            continue
        if selected is None or persisted_at > selected[0]:
            selected = (persisted_at, copy.deepcopy(row))
    return selected


def _latest_candidate_row_before(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    before: datetime,
) -> Optional[Dict[str, Any]]:
    evidence = _latest_candidate_evidence_before(module, slate, game, before)
    return evidence[1] if evidence else None


def _release_block_reasons(row: Dict[str, Any]) -> List[str]:
    reasons = {
        str(value)
        for key in (
            "blockedReasons",
            "playabilityBlockReasons",
            "actionabilityRiskReasons",
            "riskReasons",
        )
        for value in (row.get(key) or [])
        if value
    }
    tags = {str(value) for value in (row.get("tags") or [])}
    for tag in ("NOT_PLAYABLE", "ML_REJECTED", "SIGNAL_RISK_GATE_BLOCKED"):
        if tag in tags:
            reasons.add(tag)
    if row.get("predictionIntentionallyBlocked") is True:
        reasons.add("PREDICTION_INTENTIONALLY_BLOCKED")
    if str(row.get("playabilityStatus") or "").upper() == "NOT_PLAYABLE":
        reasons.add("LOCKED_PREDICTION_NOT_PLAYABLE")
    return sorted(reasons)


def _source_status(row: Dict[str, Any], name: str) -> str:
    snapshot = row.get("fundamentalsSnapshot") or {}
    statuses = snapshot.get("sourceStatuses") if isinstance(snapshot, dict) else {}
    value = statuses.get(name) if isinstance(statuses, dict) else None
    if value in (None, ""):
        context = row.get("advanced_context") or row.get("advancedContext") or {}
        group = context.get(name) if isinstance(context, dict) else {}
        value = group.get("source_status") if isinstance(group, dict) else None
    return str(value or "NOT_CONNECTED_SOURCE_REQUIRED").strip().upper()


def _late_source_block_reasons(row: Dict[str, Any]) -> List[str]:
    """Verify late safety inputs without pretending missing feeds exist."""
    reasons: set[str] = set()
    lineup_status = _source_status(row, "confirmed_lineups")
    injury_status = _source_status(row, "injuries_late_scratches_news")
    context = row.get("advanced_context") or row.get("advancedContext") or {}
    lineups = context.get("confirmed_lineups") if isinstance(context, dict) else {}
    injuries = context.get("injuries_late_scratches_news") if isinstance(context, dict) else {}
    snapshot = row.get("fundamentalsSnapshot") or {}
    injury_flags = snapshot.get("injuryFlags") if isinstance(snapshot, dict) else {}

    if lineup_status != "CONNECTED":
        reasons.add("CONFIRMED_LINEUPS_SOURCE_UNVERIFIED")
    elif not (
        isinstance(lineups, dict)
        and lineups.get("home_lineup_confirmed") is True
        and lineups.get("away_lineup_confirmed") is True
    ):
        reasons.add("BOTH_STARTING_LINEUPS_NOT_CONFIRMED")

    if injury_status != "CONNECTED":
        reasons.add("INJURIES_LATE_SCRATCHES_SOURCE_UNVERIFIED")
    else:
        combined: List[Any] = []
        if isinstance(injuries, dict):
            combined.extend(injuries.get("home_key_injuries") or [])
            combined.extend(injuries.get("away_key_injuries") or [])
            combined.extend(injuries.get("late_scratch_flags") or [])
            if injuries.get("pitcher_change_flag"):
                combined.append(injuries.get("pitcher_change_flag"))
        if isinstance(injury_flags, dict):
            combined.extend(injury_flags.get("home") or [])
            combined.extend(injury_flags.get("away") or [])
            combined.extend(injury_flags.get("lateScratches") or [])
            if injury_flags.get("pitcherChange"):
                combined.append(injury_flags.get("pitcherChange"))
        if combined:
            reasons.add("CONFIRMED_INJURY_LATE_SCRATCH_OR_PITCHER_CHANGE")
    return sorted(reasons)


def _playability_assessment(
    module: Any,
    slate: str,
    manifest: List[Dict[str, Any]],
    game: Dict[str, Any],
    stage: Dict[str, Any],
    checkpoint: str,
    evaluated_at: datetime,
    *,
    timing_status: str = "ON_TIME",
) -> Dict[str, Any]:
    key = _release_key(module, slate, game, checkpoint)
    existing = _get_record(module, key)
    if existing:
        return existing
    locked_row = copy.deepcopy((stage.get("data") or {}).get("row") or {})
    selection_fingerprint = str(locked_row.get("lastPrelockSelectionFingerprint") or "")
    if not selection_fingerprint:
        raise RuntimeError("playability_assessment_missing_locked_selection_fingerprint")
    lock_at = _lock_at(module, game)
    start = _start(module, game)
    scheduled_at: Optional[datetime] = None
    if checkpoint.startswith("T_MINUS_") and start:
        try:
            scheduled_at = start - timedelta(minutes=int(checkpoint.rsplit("_", 1)[-1]))
        except (TypeError, ValueError):
            scheduled_at = None
    evidence_cutoff = scheduled_at or evaluated_at
    evidence = (
        _latest_candidate_evidence_before(module, slate, game, evidence_cutoff)
        if timing_status == "ON_TIME"
        else None
    )
    evidence_at = evidence[0] if evidence else None
    latest = evidence[1] if evidence else locked_row
    evidence_source_at = _parse_iso(
        latest.get("predictionSourcePullAt")
        or (latest.get("slatePredictionLock") or {}).get("latestScoringPullAt")
        or (latest.get("fundamentalsSnapshot") or {}).get("asOfUtc")
    )
    reasons = set(_release_block_reasons(locked_row)) | set(_release_block_reasons(latest))
    evidence_age = (
        round((evidence_cutoff - evidence_source_at).total_seconds() / 60.0, 2)
        if evidence_source_at
        else None
    )
    if timing_status != "ON_TIME":
        reasons.add("PLAYABILITY_CHECKPOINT_WINDOW_MISSED")
    elif (
        not evidence_at
        or not evidence_source_at
        or not lock_at
        or evidence_at <= lock_at
        or evidence_source_at <= lock_at
    ):
        reasons.add("NO_POST_LOCK_PLAYABILITY_EVIDENCE")
    elif evidence_age is None or evidence_age > PLAYABILITY_EVIDENCE_MAX_AGE_MINUTES:
        reasons.add("PLAYABILITY_EVIDENCE_STALE")
    else:
        reasons.update(_late_source_block_reasons(latest))
    game_one = _doubleheader_game_one(manifest, game)
    event_driven = checkpoint in {"EVENT_GAME1_PENDING", "EVENT_GAME1_FINAL"}
    if game_one is not None and not _game_final(module, slate, game_one):
        reasons.add("DOUBLEHEADER_GAME1_NOT_FINAL")
    canonical_playable = bool(
        locked_row.get("playable") is True
        or locked_row.get("playablePick") is True
        or locked_row.get("actionablePick") is True
    )
    playable = bool(canonical_playable and not reasons)
    item = {
        **key,
        "record_type": RELEASE_ASSESSMENT_RECORD_TYPE,
        "version": RELEASE_ASSESSMENT_VERSION,
        "sport": "mlb",
        "slate_date": slate,
        "game_identity": game_identity(game),
        "game_id": game.get("game_id") or game.get("id"),
        "commence_time": start.isoformat() if start else None,
        "checkpoint": checkpoint,
        "checkpoint_timing_status": timing_status,
        "scheduled_at_utc": scheduled_at.isoformat() if scheduled_at else None,
        "evaluated_at_utc": evaluated_at.isoformat(),
        "evidence_cutoff_at_utc": evidence_cutoff.isoformat(),
        "evidence_at_utc": evidence_at.isoformat() if evidence_at else None,
        "evidence_source_at_utc": evidence_source_at.isoformat() if evidence_source_at else None,
        "evidence_age_minutes": evidence_age,
        "lineup_source_status": _source_status(latest, "confirmed_lineups"),
        "injury_source_status": _source_status(latest, "injuries_late_scratches_news"),
        "canonical_selection_fingerprint": selection_fingerprint,
        "canonical_predicted_winner": locked_row.get("predictedWinner"),
        "canonical_predicted_side": locked_row.get("predictedSide"),
        "canonical_probability_pct": locked_row.get("teamWinProbabilityPct", locked_row.get("winProbabilityPct")),
        "selection_rewrite_allowed": False,
        "playable": playable,
        "blocked": not playable,
        "status": "PLAYABLE" if playable else "BLOCKED",
        "reasons": sorted(reasons),
        "doubleheader_game_2": game_one is not None,
        "game_1_identity": game_identity(game_one) if game_one else None,
        "game_1_final": _game_final(module, slate, game_one) if game_one else None,
        "event_driven": event_driven,
        "write_once": True,
        "created_at": evaluated_at.isoformat(),
    }
    return _put_write_once_record(module, item, fingerprint_field="assessment_fingerprint")


def _ensure_playability_assessments(
    module: Any,
    slate: str,
    manifest: List[Dict[str, Any]],
    stages: Dict[str, Dict[str, Any]],
    now: datetime,
) -> List[Dict[str, Any]]:
    errors: List[Dict[str, Any]] = []
    for game in manifest:
        stage = stages.get(game_identity(game))
        start = _start(module, game)
        if not stage or not start or now >= start:
            continue
        for minutes in RELEASE_CHECKPOINT_MINUTES:
            scheduled = start - timedelta(minutes=minutes)
            if now < scheduled:
                continue
            timing_status = (
                "ON_TIME"
                if now <= scheduled + timedelta(seconds=CHECKPOINT_MAX_LATE_SECONDS)
                else "MISSED_WINDOW"
            )
            try:
                _playability_assessment(
                    module,
                    slate,
                    manifest,
                    game,
                    stage,
                    f"T_MINUS_{minutes}",
                    now,
                    timing_status=timing_status,
                )
            except Exception as exc:
                errors.append({
                    "gameIdentity": game_identity(game),
                    "checkpoint": f"T_MINUS_{minutes}",
                    "error": f"{type(exc).__name__}:{exc}",
                })
        game_one = _doubleheader_game_one(manifest, game)
        if game_one is not None:
            game_one_final = _game_final(module, slate, game_one)
            event_checkpoint = (
                "EVENT_GAME1_FINAL" if game_one_final else "EVENT_GAME1_PENDING"
            )
            try:
                _playability_assessment(
                    module,
                    slate,
                    manifest,
                    game,
                    stage,
                    event_checkpoint,
                    now,
                )
            except Exception as exc:
                errors.append({
                    "gameIdentity": game_identity(game),
                    "checkpoint": event_checkpoint,
                    "error": f"{type(exc).__name__}:{exc}",
                })
    return errors


def _latest_playability_assessment(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    selection_fingerprint: str,
) -> Optional[Dict[str, Any]]:
    candidates = [
        _get_record(module, _release_key(module, slate, game, checkpoint))
        for checkpoint in (
            "T_MINUS_30",
            "T_MINUS_15",
            "EVENT_GAME1_PENDING",
            "EVENT_GAME1_FINAL",
        )
    ]
    valid: List[Dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        material = {
            str(key): value
            for key, value in _plain(item).items()
            if key != "assessment_fingerprint"
        }
        fingerprint = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        if (
            item.get("record_type") != RELEASE_ASSESSMENT_RECORD_TYPE
            or item.get("version") != RELEASE_ASSESSMENT_VERSION
            or str(item.get("slate_date") or "") != slate
            or str(item.get("game_identity") or "") != game_identity(game)
            or item.get("selection_rewrite_allowed") is not False
            or str(item.get("canonical_selection_fingerprint") or "") != selection_fingerprint
            or item.get("assessment_fingerprint") != fingerprint
        ):
            continue
        valid.append(item)
    if not valid:
        return None
    return max(valid, key=lambda item: str(item.get("evaluated_at_utc") or ""))


def _resolved_playability_lifecycle(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    locked_row: Dict[str, Any],
    now: datetime,
    event_pending_required: bool = False,
) -> Dict[str, Any]:
    def read(checkpoint: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        key = _release_key(module, slate, game, checkpoint)
        try:
            item = module.TABLE.get_item(Key=key, ConsistentRead=True).get("Item")
            return (item if isinstance(item, dict) else None), None
        except Exception as exc:
            return None, f"status_read_failed:{type(exc).__name__}:{exc}"

    return resolve_playability_lifecycle(
        slate=slate,
        game=game,
        locked_row=locked_row,
        now=now,
        record_reader=read,
        event_pending_required=event_pending_required,
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _fingerprint_material(item: Dict[str, Any]) -> Dict[str, Any]:
    row = (item.get("data") or {}).get("row") or {}
    vector = row.get("frozenFeatureVector") or {}
    selected_signal = row.get("homeSignal") if row.get("predictedSide") == "home" else row.get("awaySignal")
    selected_signal = selected_signal if isinstance(selected_signal, dict) else {}
    return {
        "version": VERSION,
        "recordType": item.get("record_type"),
        "modelVersion": item.get("model_version"),
        "lockPolicy": item.get("lock_policy"),
        "immutableStaged": item.get("immutable_staged"),
        "writeOnce": item.get("write_once"),
        "slateDateEt": item.get("slate_date"),
        "gameIdentity": item.get("game_identity"),
        "gameId": row.get("gameId"),
        "commenceTime": row.get("commenceTime"),
        "homeTeam": row.get("homeTeam"),
        "awayTeam": row.get("awayTeam"),
        "scheduledLockAtUtc": item.get("scheduled_lock_at_utc"),
        "sourcePullAtUtc": item.get("source_pull_at_utc"),
        "sourcePullId": item.get("source_pull_id"),
        "sourceWindow": item.get("source_window") or {},
        "actualStagedAtUtc": item.get("staged_at_utc"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "americanOdds": row.get("lockedAmericanOdds", row.get("americanOdds")),
        "priceBook": row.get("priceBook") or selected_signal.get("priceBook"),
        "priceSource": row.get("priceSource") or selected_signal.get("priceSource"),
        "vectorVersion": vector.get("version"),
        "vectorFingerprint": vector.get("fingerprint"),
        "promotionPolicyVersion": item.get("promotion_policy_version"),
        "candidateProof": item.get("candidate_proof") or {},
        "providerManifestAuthority": item.get("provider_manifest_authority") or {},
        "manifestGameCount": item.get("manifest_game_count"),
        "manifestGameIdentities": (item.get("data") or {}).get("manifestGameIdentities") or [],
    }


def _stage_fingerprint(item: Dict[str, Any]) -> str:
    payload = json.dumps(_plain(_fingerprint_material(item)), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _consistent_item(table: Any, key: Dict[str, str]) -> Optional[Dict[str, Any]]:
    try:
        item = table.get_item(Key=key, ConsistentRead=True).get("Item")
    except Exception:
        return None
    return item if isinstance(item, dict) else None


def _provider_manifest_authority_errors(table: Any, item: Dict[str, Any]) -> List[str]:
    """Verify the stage's full-slate pointer against its write-once record."""
    import inqsi_pull_history as history_contract

    errors: List[str] = []
    authority = item.get("provider_manifest_authority") or {}
    if not isinstance(authority, dict) or not authority:
        return ["provider_manifest_authority_missing"]
    required_true = ("immutable", "writeOnce", "fullProviderSchedule", "consistentReadVerified")
    for key in required_true:
        if authority.get(key) is not True:
            errors.append(f"provider_manifest_authority_{key}_missing")
    if authority.get("version") != history_contract.PROVIDER_MANIFEST_VERSION:
        errors.append("provider_manifest_authority_version_mismatch")
    if authority.get("recordType") != history_contract.PROVIDER_MANIFEST_RECORD_TYPE:
        errors.append("provider_manifest_authority_record_type_mismatch")
    pk = str(authority.get("pk") or "")
    sk = str(authority.get("sk") or "")
    if not pk or not sk:
        return sorted(set([*errors, "provider_manifest_authority_key_missing"]))
    stored = _consistent_item(table, {"PK": pk, "SK": sk})
    if not stored:
        return sorted(set([*errors, "immutable_provider_manifest_readback_missing"]))
    manifest = stored.get("data") or {}
    if not isinstance(manifest, dict) or not manifest:
        return sorted(set([*errors, "immutable_provider_manifest_payload_missing"]))
    fingerprint = history_contract.provider_manifest_fingerprint(manifest)
    if (
        stored.get("record_type") != history_contract.PROVIDER_MANIFEST_RECORD_TYPE
        or stored.get("write_once") is not True
        or stored.get("PK") != pk
        or stored.get("SK") != sk
        or str(stored.get("manifest_fingerprint") or "") != fingerprint
        or str(manifest.get("fingerprint") or "") != fingerprint
        or str(authority.get("fingerprint") or "") != fingerprint
    ):
        errors.append("immutable_provider_manifest_readback_mismatch")
    if (
        str(manifest.get("slateDate") or "") != str(item.get("slate_date") or "")
        or str(authority.get("slateDate") or "") != str(item.get("slate_date") or "")
    ):
        errors.append("provider_manifest_stage_slate_mismatch")
    games = list(manifest.get("games") or [])
    raw_identities = [history_contract.provider_game_identity("mlb", game) for game in games]
    canonical_identities = [game_identity(game) for game in games]
    declared_canonical = list((item.get("data") or {}).get("manifestGameIdentities") or [])
    try:
        authority_count = int(authority.get("gameCount"))
        stage_count = int(item.get("manifest_game_count"))
    except Exception:
        authority_count = stage_count = -1
    if authority_count != len(games) or stage_count != len(games):
        errors.append("provider_manifest_stage_game_count_mismatch")
    if list(authority.get("gameIdentities") or []) != raw_identities:
        errors.append("provider_manifest_authority_identity_mismatch")
    if list(authority.get("canonicalGameIdentities") or []) != canonical_identities:
        errors.append("provider_manifest_authority_canonical_identity_mismatch")
    if declared_canonical != canonical_identities:
        errors.append("provider_manifest_stage_membership_mismatch")
    if len(set(canonical_identities)) != len(canonical_identities):
        errors.append("provider_manifest_stage_duplicate_identity")

    membership_authority = authority.get("membershipAuthority") or {}
    if membership_authority:
        for key in (
            "version",
            "recordType",
            "pk",
            "sk",
            "fingerprint",
            "slateDate",
            "pullId",
            "gameCount",
            "gameIdentities",
            "canonicalGameIdentities",
        ):
            if membership_authority.get(key) != authority.get(key):
                errors.append(f"membership_authority_{key}_mismatch")

    schedule_authority = authority.get("scheduleRevisionAuthority") or {}
    schedule_manifest = manifest
    schedule_games = games
    if schedule_authority:
        for key in required_true:
            if schedule_authority.get(key) is not True:
                errors.append(f"schedule_revision_authority_{key}_missing")
        if schedule_authority.get("version") != history_contract.PROVIDER_MANIFEST_VERSION:
            errors.append("schedule_revision_authority_version_mismatch")
        if schedule_authority.get("recordType") != history_contract.PROVIDER_MANIFEST_RECORD_TYPE:
            errors.append("schedule_revision_authority_record_type_mismatch")
        schedule_pk = str(schedule_authority.get("pk") or "")
        schedule_sk = str(schedule_authority.get("sk") or "")
        schedule_stored = (
            _consistent_item(table, {"PK": schedule_pk, "SK": schedule_sk})
            if schedule_pk and schedule_sk
            else None
        )
        if not schedule_pk or not schedule_sk:
            errors.append("schedule_revision_authority_key_missing")
        elif not schedule_stored:
            errors.append("immutable_schedule_revision_manifest_readback_missing")
        else:
            schedule_manifest = schedule_stored.get("data") or {}
            schedule_fingerprint = (
                history_contract.provider_manifest_fingerprint(schedule_manifest)
                if isinstance(schedule_manifest, dict) and schedule_manifest
                else ""
            )
            if (
                schedule_stored.get("record_type")
                != history_contract.PROVIDER_MANIFEST_RECORD_TYPE
                or schedule_stored.get("write_once") is not True
                or schedule_stored.get("PK") != schedule_pk
                or schedule_stored.get("SK") != schedule_sk
                or str(schedule_stored.get("manifest_fingerprint") or "")
                != schedule_fingerprint
                or str(schedule_manifest.get("fingerprint") or "")
                != schedule_fingerprint
                or str(schedule_authority.get("fingerprint") or "")
                != schedule_fingerprint
            ):
                errors.append("immutable_schedule_revision_manifest_readback_mismatch")
            if (
                str(schedule_manifest.get("slateDate") or "")
                != str(item.get("slate_date") or "")
                or str(schedule_authority.get("slateDate") or "")
                != str(item.get("slate_date") or "")
            ):
                errors.append("schedule_revision_stage_slate_mismatch")
            schedule_games = list(schedule_manifest.get("games") or [])
            schedule_raw_identities = [
                history_contract.provider_game_identity("mlb", game)
                for game in schedule_games
            ]
            schedule_canonical_identities = [
                game_identity(game) for game in schedule_games
            ]
            if (
                list(schedule_authority.get("gameIdentities") or [])
                != schedule_raw_identities
                or list(schedule_authority.get("canonicalGameIdentities") or [])
                != schedule_canonical_identities
            ):
                errors.append("schedule_revision_authority_identity_mismatch")
            try:
                schedule_count = int(schedule_authority.get("gameCount"))
            except (TypeError, ValueError):
                schedule_count = -1
            if schedule_count != len(schedule_games) or len(schedule_games) != len(games):
                errors.append("schedule_revision_authority_game_count_mismatch")
            schedule_proof = schedule_manifest.get("scheduleAuthority") or {}
            if (
                schedule_proof.get("fingerprint")
                != schedule_authority.get("officialScheduleAuthorityFingerprint")
                or authority.get("officialScheduleAuthorityFingerprint")
                != schedule_authority.get("officialScheduleAuthorityFingerprint")
            ):
                errors.append("schedule_revision_official_authority_fingerprint_mismatch")

    membership_by_pk: Dict[str, Dict[str, Any]] = {}
    schedule_by_pk: Dict[str, Dict[str, Any]] = {}
    if schedule_authority:
        for label, source_games, target in (
            ("membership", games, membership_by_pk),
            ("schedule_revision", schedule_games, schedule_by_pk),
        ):
            for source_game in source_games:
                official_pk = _official_game_pk(source_game)
                if not official_pk:
                    errors.append(f"{label}_official_game_pk_missing")
                elif official_pk in target:
                    errors.append(f"{label}_duplicate_official_game_pk:{official_pk}")
                else:
                    target[official_pk] = source_game
        if set(membership_by_pk) != set(schedule_by_pk):
            errors.append("schedule_revision_official_membership_mismatch")
        for official_pk in sorted(set(membership_by_pk) & set(schedule_by_pk)):
            if not _ordered_teams_match(
                membership_by_pk[official_pk],
                schedule_by_pk[official_pk],
            ):
                errors.append(f"schedule_revision_ordered_teams_mismatch:{official_pk}")

    stage_identity = str(item.get("game_identity") or "")
    try:
        index = canonical_identities.index(stage_identity)
        manifest_game = games[index]
    except ValueError:
        manifest_game = None
        errors.append("stage_game_missing_from_provider_manifest")
    row = ((item.get("data") or {}).get("row") or {})
    if manifest_game:
        stage_official_pk = (
            _official_game_pk(item)
            or _official_game_pk(row)
            or _official_game_pk(manifest_game)
        )
        schedule_game = schedule_by_pk.get(stage_official_pk) if schedule_authority else manifest_game
        if (
            not schedule_game
            or _parse_iso(schedule_game.get("commence_time"))
            != _parse_iso(item.get("commence_time"))
        ):
            errors.append("provider_manifest_stage_commence_mismatch")
        if _norm(manifest_game.get("home_team")) != _norm(row.get("homeTeam") or row.get("home_team")):
            errors.append("provider_manifest_stage_home_team_mismatch")
        if _norm(manifest_game.get("away_team")) != _norm(row.get("awayTeam") or row.get("away_team")):
            errors.append("provider_manifest_stage_away_team_mismatch")
    return sorted(set(errors))


def _candidate_snapshot_authority_errors(table: Any, item: Dict[str, Any]) -> List[str]:
    """Read back the exact pre-lock prediction selected by the stage."""
    errors: List[str] = []
    proof = item.get("candidate_proof") or {}
    pk = str(proof.get("pk") or "")
    sk = str(proof.get("sk") or "")
    if not pk or not sk:
        return ["candidate_snapshot_key_missing"]
    candidate = _consistent_item(table, {"PK": pk, "SK": sk})
    if not candidate:
        return ["candidate_snapshot_consistent_readback_missing"]
    candidate_row = candidate.get("data") or {}
    if not isinstance(candidate_row, dict) or not candidate_row:
        return ["candidate_snapshot_row_missing"]
    stage_row = ((item.get("data") or {}).get("row") or {})
    expected_pk = f"GAME_WINNERS#mlb#{item.get('slate_date')}"
    stage_identity = _raw_game_identity(stage_row)
    candidate_identity = str(
        candidate.get("game_identity")
        or _raw_game_identity(candidate_row)
        or ""
    )
    candidate_row_identity = _raw_game_identity(candidate_row)
    stage_official_game_pk = _official_game_pk(stage_row)
    candidate_official_game_pk = _official_game_pk(candidate_row)
    exact_identity_binding = bool(
        candidate_identity
        and candidate_identity == candidate_row_identity == stage_identity
    )
    official_identity_binding = bool(
        candidate_identity
        and candidate_identity == candidate_row_identity
        and stage_official_game_pk
        and candidate_official_game_pk
        and stage_official_game_pk == candidate_official_game_pk
    )
    legacy_identity_binding = bool(
        candidate_identity
        and candidate_identity == candidate_row_identity
        and _legacy_identity_crosswalk_match(stage_row, candidate_row)
    )
    expected_binding_mode = (
        "exact_identity"
        if exact_identity_binding
        else "official_game_pk"
        if official_identity_binding
        else "legacy_team_start_crosswalk"
        if legacy_identity_binding
        else "unverified"
    )
    if candidate.get("PK") != expected_pk or candidate.get("SK") != sk:
        errors.append("candidate_snapshot_key_mismatch")
    if not candidate_identity or not sk.startswith(
        f"PREGAME#GAME#{candidate_identity}#"
    ):
        errors.append("candidate_snapshot_sk_identity_mismatch")
    if (
        candidate.get("record_type") != PREGAME_SNAPSHOT_RECORD_TYPE
        or proof.get("recordType") != PREGAME_SNAPSHOT_RECORD_TYPE
        or candidate.get("immutable_pregame") is not True
        or candidate.get("write_once") is not True
    ):
        errors.append("candidate_snapshot_not_immutable_write_once")
    if (
        str(candidate.get("slate_date") or "") != str(item.get("slate_date") or "")
        or not (
            exact_identity_binding
            or official_identity_binding
            or legacy_identity_binding
        )
    ):
        errors.append("candidate_snapshot_game_binding_mismatch")
    identity_proof_fields = {
        "candidateGameIdentity": candidate_identity,
        "stageGameIdentity": stage_identity,
        "candidateOfficialGamePk": candidate_official_game_pk or None,
        "stageOfficialGamePk": stage_official_game_pk or None,
        "identityBindingMode": expected_binding_mode,
    }
    for proof_key, expected_value in identity_proof_fields.items():
        if proof.get(proof_key) != expected_value:
            errors.append(f"candidate_snapshot_{proof_key}_proof_mismatch")
    timestamp_fields = (
        ("prediction_created_at_utc", "predictionCreatedAtUtc"),
        ("prediction_persisted_at_utc", "predictionPersistedAtUtc"),
        ("prediction_source_pull_at_utc", "predictionSourcePullAtUtc"),
    )
    for stored_key, proof_key in timestamp_fields:
        if _parse_iso(candidate.get(stored_key)) != _parse_iso(proof.get(proof_key)):
            errors.append(f"candidate_snapshot_{stored_key}_mismatch")
    if str(candidate.get("prediction_source_pull_id") or "") != str(proof.get("predictionSourcePullId") or ""):
        errors.append("candidate_snapshot_source_pull_id_mismatch")
    expected_live_pk = f"GAME_WINNERS#mlb#{item.get('slate_date')}"
    expected_live_sk = (
        f"GAME#{candidate_row.get('commenceTime') or 'unknown'}#"
        f"{candidate.get('game_identity') or candidate_row.get('gameIdentity') or candidate_row.get('gameId')}"
    )
    if (
        candidate.get("snapshot_version") != PREGAME_SNAPSHOT_VERSION
        or proof.get("snapshotVersion") != PREGAME_SNAPSHOT_VERSION
    ):
        errors.append("candidate_snapshot_version_mismatch")
    errors.extend(_public_prelock_marker_errors(candidate, candidate_row))
    marker_proof_fields = (
        ("snapshot_role", "snapshotRole"),
        ("public_authority_version", "publicAuthorityVersion"),
        ("user_visible", "userVisible"),
        ("display_prediction", "displayPrediction"),
        ("display_status", "displayStatus"),
        ("display_surface", "displaySurface"),
        ("signal_policy_version", "signalPolicyVersion"),
    )
    for stored_key, proof_key in marker_proof_fields:
        if candidate.get(stored_key) != proof.get(proof_key):
            errors.append(f"candidate_snapshot_{stored_key}_proof_mismatch")
    candidate_fingerprint_version = candidate.get("prediction_payload_fingerprint_version")
    proof_fingerprint_version = proof.get("predictionPayloadFingerprintVersion")
    fingerprint_contract_ready = bool(
        _supported_payload_fingerprint_version(candidate_fingerprint_version)
        and _supported_payload_fingerprint_version(proof_fingerprint_version)
        and (candidate_fingerprint_version or None)
        == (proof_fingerprint_version or None)
    )
    if not fingerprint_contract_ready:
        errors.append("candidate_snapshot_payload_fingerprint_version_mismatch")
    if (
        candidate.get("prediction_persistence_proof_type") != PREGAME_PERSISTENCE_PROOF_TYPE
        or proof.get("persistenceProofType") != PREGAME_PERSISTENCE_PROOF_TYPE
    ):
        errors.append("candidate_snapshot_post_write_ack_missing")
    if (
        candidate.get("prediction_persistence_write_pk") != expected_live_pk
        or candidate.get("prediction_persistence_write_sk") != expected_live_sk
        or proof.get("persistenceWritePk") != expected_live_pk
        or proof.get("persistenceWriteSk") != expected_live_sk
    ):
        errors.append("candidate_snapshot_live_write_binding_mismatch")
    fingerprint_version = candidate_fingerprint_version or None
    if fingerprint_contract_ready:
        row_fingerprint = _payload_fingerprint(candidate_row, fingerprint_version)
        if row_fingerprint != str(proof.get("candidateRowFingerprint") or ""):
            errors.append("candidate_snapshot_row_fingerprint_mismatch")
        if (
            str(candidate.get("prediction_payload_fingerprint") or "")
            != row_fingerprint
            or str(proof.get("predictionPayloadFingerprint") or "")
            != row_fingerprint
        ):
            errors.append("candidate_snapshot_persisted_payload_fingerprint_mismatch")
        if _payload_fingerprint(candidate, fingerprint_version) != str(
            proof.get("candidateSnapshotFingerprint") or ""
        ):
            errors.append("candidate_snapshot_item_fingerprint_mismatch")
        if _payload_fingerprint(
            _selection_material(candidate_row), fingerprint_version
        ) != str(proof.get("candidateSelectionFingerprint") or ""):
            errors.append("candidate_snapshot_selection_fingerprint_mismatch")
    if (candidate_row.get("frozenFeatureVector") or {}).get("fingerprint") != proof.get("candidateVectorFingerprint"):
        errors.append("candidate_snapshot_vector_fingerprint_mismatch")
    if stage_row.get("lastPrelockSelectionFingerprint") != proof.get("candidateSelectionFingerprint"):
        errors.append("stage_selection_not_candidate_snapshot_selection")
    for key in ("winner", "correct", "success", "homeWon", "pickCorrect", "outcome", "finalScore"):
        if key in candidate_row:
            errors.append(f"candidate_snapshot_contains_{key}")
    labels = (candidate_row.get("frozenFeatureVector") or {}).get("labels") or {}
    if labels.get("homeWon") is not None or labels.get("pickCorrect") is not None:
        errors.append("candidate_snapshot_vector_contains_outcome")
    return sorted(set(errors))


def _source_window_authority_errors(table: Any, item: Dict[str, Any]) -> List[str]:
    """Verify every bound pull entry against the persisted pull history."""
    errors: List[str] = []
    source_window = item.get("source_window") or {}
    entries = source_window.get("pulls") or []
    cutoff = _parse_iso(item.get("scheduled_lock_at_utc"))
    if not isinstance(entries, list) or not entries:
        return ["bound_source_window_missing"]
    timestamps: List[datetime] = []
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append("bound_source_window_entry_invalid")
            continue
        pull_id = str(entry.get("pullId") or "")
        pulled_at = _parse_iso(entry.get("pulledAtUtc"))
        fingerprint = str(entry.get("gameSnapshotFingerprint") or "")
        key_tuple = (pull_id, str(entry.get("pulledAtUtc") or ""), fingerprint)
        if not pull_id or not pulled_at or not fingerprint or key_tuple in seen:
            errors.append("bound_source_window_entry_incomplete_or_duplicate")
            continue
        seen.add(key_tuple)
        timestamps.append(pulled_at)
        if not cutoff or pulled_at > cutoff:
            errors.append("bound_source_window_pull_after_cutoff")
        pull_item = _consistent_item(table, {
            "PK": f"PULLS#mlb#{item.get('slate_date')}",
            "SK": f"PULL#{entry.get('pulledAtUtc')}#{pull_id}",
        })
        if not pull_item:
            errors.append(f"bound_source_window_pull_readback_missing:{index}")
            continue
        pull = pull_item.get("data") or {}
        if (
            pull_item.get("record_type") != "pull_run"
            or str(pull.get("pull_id") or "") != pull_id
            or _parse_iso(pull.get("pulled_at")) != pulled_at
            or str(pull.get("slate_date") or "") != str(item.get("slate_date") or "")
        ):
            errors.append(f"bound_source_window_pull_readback_mismatch:{index}")
            continue
        staged_row = (item.get("data") or {}).get("row") or {}
        matching = _matching_game(
            pull,
            {
                "game_id": item.get("game_id") or item.get("game_identity"),
                "official_game_pk": item.get("official_game_pk"),
                "commence_time": item.get("commence_time"),
                "home_team": staged_row.get("homeTeam") or staged_row.get("home_team"),
                "away_team": staged_row.get("awayTeam") or staged_row.get("away_team"),
            },
        )
        if not matching or _game_snapshot_fingerprint(matching) != fingerprint:
            errors.append(f"bound_source_window_game_fingerprint_mismatch:{index}")
    if timestamps != sorted(timestamps):
        errors.append("bound_source_window_not_chronological")
    latest = entries[-1] if entries and isinstance(entries[-1], dict) else {}
    if (
        _parse_iso(latest.get("pulledAtUtc")) != _parse_iso(item.get("source_pull_at_utc"))
        or str(latest.get("pullId") or "") != str(item.get("source_pull_id") or "")
        or int(item.get("pull_depth") or 0) != len(entries)
    ):
        errors.append("bound_source_window_terminal_pull_mismatch")
    return sorted(set(errors))


def _selection_lock_authority_errors(row: Dict[str, Any]) -> List[str]:
    """Validate winner-selection facts independently of ML-vector eligibility."""
    errors: List[str] = []
    side = row.get("predictedSide")
    if side not in {"home", "away"} or not row.get("predictedWinner"):
        errors.append("selected_side_or_winner_missing")
    else:
        expected_winner = (
            (row.get("homeTeam") or row.get("home_team"))
            if side == "home"
            else (row.get("awayTeam") or row.get("away_team"))
        )
        if _norm(row.get("predictedWinner")) != _norm(expected_winner):
            errors.append("selected_winner_side_team_mismatch")
    if not _selected_price_proven(row):
        errors.append("selected_side_locked_price_not_proven")
    if _team_win_probability_pct(row) in (None, ""):
        errors.append("selected_team_win_probability_missing")
    return sorted(set(errors))


def persisted_stage_authority_errors(table: Any, item: Dict[str, Any]) -> List[str]:
    """Validate the persisted authority chain required for canonical promotion."""
    errors: List[str] = []
    if item.get("model_version") != VERSION:
        errors.append("stage_model_version_mismatch")
    if item.get("lock_policy") != LOCK_POLICY:
        errors.append("stage_lock_policy_mismatch")
    if item.get("record_type") != STAGE_RECORD_TYPE:
        errors.append("stage_record_type_mismatch")
    if item.get("immutable_staged") is not True or item.get("write_once") is not True:
        errors.append("stage_not_immutable_write_once")
    commence = _parse_iso(item.get("commence_time"))
    cutoff = _parse_iso(item.get("scheduled_lock_at_utc"))
    staged_at = _parse_iso(item.get("staged_at_utc"))
    created_at = _parse_iso(item.get("created_at"))
    stable_at = cutoff + timedelta(seconds=CUTOFF_STABILIZATION_SECONDS) if cutoff else None
    if not commence or not cutoff or cutoff != commence - timedelta(minutes=REQUIRED_LOCK_MINUTES):
        errors.append("stage_not_exact_commence_minus_45_cutoff")
    if not staged_at or not stable_at or staged_at < stable_at or not commence or staged_at >= commence:
        errors.append("stage_not_after_stabilization_and_before_start")
    if created_at != staged_at:
        errors.append("stage_created_at_not_staged_at")
    source_window = item.get("source_window") or {}
    if (
        source_window.get("version") != SOURCE_WINDOW_VERSION
        or _parse_iso(source_window.get("scheduledCutoffAtUtc")) != cutoff
        or _parse_iso(source_window.get("closedAtUtc")) != staged_at
        or int(source_window.get("stabilizationSeconds", -1)) != CUTOFF_STABILIZATION_SECONDS
    ):
        errors.append("stage_bound_source_window_metadata_mismatch")
    errors.extend(_provider_manifest_authority_errors(table, item))
    errors.extend(_candidate_snapshot_authority_errors(table, item))
    errors.extend(_source_window_authority_errors(table, item))
    stage_row = ((item.get("data") or {}).get("row") or {})
    errors.extend(_selection_lock_authority_errors(stage_row))
    return sorted(set(errors))


def _get_stage(module: Any, slate: str, game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    response = module.TABLE.get_item(Key=_stage_key(module, slate, game), ConsistentRead=True)
    item = response.get("Item")
    return item if isinstance(item, dict) else None


def _conditional_collision(exc: Exception) -> bool:
    response = getattr(exc, "response", {}) or {}
    return str((response.get("Error") or {}).get("Code") or "") == "ConditionalCheckFailedException"


def _diagnostic_prefix(module: Any, game: Dict[str, Any]) -> str:
    digest = hashlib.sha256(game_identity(game).encode("utf-8")).hexdigest()
    return f"PER_GAME_LOCK_DIAGNOSTIC#TMINUS{module.LOCK_MINUTES}#{digest}"


def _diagnostic_sk(
    module: Any,
    game: Dict[str, Any],
    attempted_at: datetime,
    attempt_id: str,
    event: str,
) -> str:
    timestamp = attempted_at.astimezone(timezone.utc).isoformat()
    return f"{_diagnostic_prefix(module, game)}#ATTEMPT#{timestamp}#{attempt_id}#{event}"


def _diagnostic_fingerprint(item: Dict[str, Any]) -> str:
    material = {
        str(key): value
        for key, value in _plain(item).items()
        if key != "diagnostic_fingerprint"
    }
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _put_diagnostic(module: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    """Append one immutable diagnostic event without affecting lock authority."""
    prepared = module.history.ddb_safe(copy.deepcopy(item))
    prepared["diagnostic_fingerprint"] = _diagnostic_fingerprint(prepared)
    key = {"PK": prepared["PK"], "SK": prepared["SK"]}
    try:
        module.TABLE.put_item(
            Item=prepared,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return {
            "ok": True,
            "created": True,
            "pk": prepared["PK"],
            "sk": prepared["SK"],
            "fingerprint": prepared["diagnostic_fingerprint"],
        }
    except Exception as exc:
        if not _conditional_collision(exc):
            return {
                "ok": False,
                "created": False,
                "pk": prepared["PK"],
                "sk": prepared["SK"],
                "error": f"diagnostic_write_failed:{type(exc).__name__}:{exc}",
            }
        try:
            existing = module.TABLE.get_item(Key=key, ConsistentRead=True).get("Item")
        except Exception as read_exc:
            return {
                "ok": False,
                "created": False,
                "pk": prepared["PK"],
                "sk": prepared["SK"],
                "error": f"diagnostic_collision_readback_failed:{type(read_exc).__name__}:{read_exc}",
            }
        if (
            isinstance(existing, dict)
            and existing.get("diagnostic_fingerprint") == prepared["diagnostic_fingerprint"]
        ):
            return {
                "ok": True,
                "created": False,
                "immutableExisting": True,
                "pk": prepared["PK"],
                "sk": prepared["SK"],
                "fingerprint": prepared["diagnostic_fingerprint"],
            }
        return {
            "ok": False,
            "created": False,
            "pk": prepared["PK"],
            "sk": prepared["SK"],
            "error": "diagnostic_write_once_collision_mismatch",
        }


def _diagnostic_base(
    module: Any,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    game: Dict[str, Any],
    status: Dict[str, Any],
    attempted_at: datetime,
    attempt_id: str,
    force: bool,
) -> Dict[str, Any]:
    scoring = _scoring_pulls(module, pulls, game)
    source = scoring[-1] if scoring else {}
    source_at = _pull_at(module, source)
    latest_at = _pull_at(module, pulls[-1]) if pulls else None
    start = _start(module, game)
    lock_at = _lock_at(module, game)
    stable_at = _cutoff_stable_at(module, game)
    return {
        "PK": module._lock_pk(slate),
        "diagnostics_version": ATTEMPT_DIAGNOSTICS_VERSION,
        "sport": "mlb",
        "slate_date": slate,
        "model_version": VERSION,
        "lock_policy": LOCK_POLICY,
        "write_once": True,
        "attempt_id": attempt_id,
        "attempted_at_utc": attempted_at.astimezone(timezone.utc).isoformat(),
        "game_identity": game_identity(game),
        "game_id": game.get("game_id") or game.get("gameId") or game.get("id"),
        "commence_time": start.isoformat() if start else None,
        "scheduled_lock_at_utc": lock_at.isoformat() if lock_at else None,
        "source_window_stable_at_utc": stable_at.isoformat() if stable_at else None,
        "state_at_attempt": status.get("state"),
        "state_errors_at_attempt": list(status.get("errors") or []),
        "force_requested": bool(force),
        "pull_count_available": len(pulls),
        "pull_depth_at_cutoff": len(scoring),
        "latest_available_pull_at_utc": latest_at.isoformat() if latest_at else None,
        "latest_cutoff_pull_at_utc": source_at.isoformat() if source_at else None,
        "latest_cutoff_pull_id": source.get("pull_id"),
        "manifest_game_count": len(manifest),
        "manifest_game_identities": [game_identity(entry) for entry in manifest],
    }


def _begin_attempt_diagnostics(
    module: Any,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    statuses: Iterable[Dict[str, Any]],
    attempted_at: datetime,
    force: bool,
) -> List[Dict[str, Any]]:
    by_identity = {str(status.get("gameIdentity") or ""): status for status in statuses or []}
    attempts: List[Dict[str, Any]] = []
    for game in manifest:
        identity = game_identity(game)
        status = by_identity.get(identity) or {}
        if status.get("state") not in _DIAGNOSTIC_STATES:
            continue
        if status.get("state") == "MISSED_NOT_BACKFILLED":
            history = _diagnostic_history(module, slate, game, limit=20)
            latest = history.get("latestAttempt") or {}
            if latest.get("outcome") == "MISSED_NOT_BACKFILLED":
                # A missed lock is terminal. Preserve the first immutable proof,
                # but do not append two more records every minute until midnight.
                continue
        attempt_id = uuid4().hex
        try:
            base = _diagnostic_base(
                module,
                slate,
                pulls,
                manifest,
                game,
                status,
                attempted_at,
                attempt_id,
                force,
            )
        except Exception as exc:
            base = {
                "PK": module._lock_pk(slate),
                "diagnostics_version": ATTEMPT_DIAGNOSTICS_VERSION,
                "sport": "mlb",
                "slate_date": slate,
                "model_version": VERSION,
                "lock_policy": LOCK_POLICY,
                "write_once": True,
                "attempt_id": attempt_id,
                "attempted_at_utc": attempted_at.astimezone(timezone.utc).isoformat(),
                "game_identity": identity,
                "game_id": game.get("game_id") or game.get("gameId") or game.get("id"),
                "commence_time": (_start(module, game) or attempted_at).isoformat(),
                "scheduled_lock_at_utc": (_lock_at(module, game) or attempted_at).isoformat(),
                "state_at_attempt": status.get("state"),
                "state_errors_at_attempt": list(status.get("errors") or []),
                "force_requested": bool(force),
                "diagnostic_context_error": f"{type(exc).__name__}:{exc}",
            }
        start_item = {
            **base,
            "SK": _diagnostic_sk(module, game, attempted_at, attempt_id, "START"),
            "record_type": ATTEMPT_RECORD_TYPE,
            "diagnostic_event": "ATTEMPT_STARTED",
            "created_at": attempted_at.astimezone(timezone.utc).isoformat(),
        }
        attempts.append({
            "attemptId": attempt_id,
            "game": game,
            "base": base,
            "startWrite": _put_diagnostic(module, start_item),
        })
    return attempts


def _attempt_outcome(
    status: Dict[str, Any],
    failures: List[Dict[str, Any]],
    exception: Optional[Exception],
) -> Tuple[str, str]:
    if exception is not None:
        return "INVOCATION_EXCEPTION", f"{type(exception).__name__}:{exception}"
    state = str(status.get("state") or "UNKNOWN")
    failure_reasons = [str(item.get("reason") or "") for item in failures if item.get("reason")]
    if state == "LOCKED_CANONICAL":
        return (
            "LOCKED_CANONICAL_WITH_ERRORS" if failure_reasons else "LOCKED_CANONICAL",
            ",".join(sorted(set(failure_reasons))) if failure_reasons else state,
        )
    if state == "LOCKED_NO_PREDICTION_DATA":
        return "LOCKED_NO_PREDICTION_DATA", state
    if state == "WAITING_FOR_CUTOFF_STABILIZATION":
        return "WAITING_FOR_CUTOFF_STABILIZATION", state
    if state == "MISSED_NOT_BACKFILLED":
        return "MISSED_NOT_BACKFILLED", state
    if failure_reasons:
        return "FAILED", ",".join(sorted(set(failure_reasons)))
    if state in {"INVALID_STAGE_BLOCKED", "STAGED_CANONICAL_WRITE_BLOCKED", "DUE_NOT_STAGED"}:
        return "FAILED", state
    return "NO_ACTION", state


def _finish_attempt_diagnostics(
    module: Any,
    attempts: List[Dict[str, Any]],
    progress: Optional[Dict[str, Any]],
    failures: Optional[List[Dict[str, Any]]] = None,
    exception: Optional[Exception] = None,
) -> Dict[str, Any]:
    statuses = {
        str(status.get("gameIdentity") or ""): status
        for status in ((progress or {}).get("games") or [])
    }
    all_failures = list(failures or [])
    finished_at = module._now_utc().astimezone(timezone.utc)
    summaries: List[Dict[str, Any]] = []
    for attempt in attempts:
        base = attempt["base"]
        identity = str(base.get("game_identity") or "")
        status = statuses.get(identity) or {
            "gameIdentity": identity,
            "state": base.get("state_at_attempt"),
            "errors": base.get("state_errors_at_attempt") or [],
        }
        related_failures = [
            copy.deepcopy(item)
            for item in all_failures
            if str(item.get("gameIdentity") or "") == identity
        ]
        outcome, reason = _attempt_outcome(status, related_failures, exception)
        attempted_at = _parse_iso(base.get("attempted_at_utc")) or finished_at
        outcome_item = {
            **base,
            "SK": _diagnostic_sk(
                module,
                attempt["game"],
                attempted_at,
                attempt["attemptId"],
                "OUTCOME",
            ),
            "record_type": ATTEMPT_OUTCOME_RECORD_TYPE,
            "diagnostic_event": "ATTEMPT_OUTCOME",
            "outcome": outcome,
            "reason": reason,
            "state_after_attempt": status.get("state"),
            "state_errors_after_attempt": list(status.get("errors") or []),
            "failure_details": related_failures,
            "exception_type": type(exception).__name__ if exception is not None else None,
            "exception_message": str(exception) if exception is not None else None,
            "stage_present_after_attempt": status.get("sourcePullAtUtc") not in (None, ""),
            "canonical_proven_after_attempt": status.get("state") == "LOCKED_CANONICAL",
            "finished_at_utc": finished_at.isoformat(),
            "elapsed_milliseconds": max(
                int((finished_at - attempted_at).total_seconds() * 1000),
                0,
            ),
            "created_at": finished_at.isoformat(),
        }
        outcome_write = _put_diagnostic(module, outcome_item)
        summaries.append({
            "attemptId": attempt["attemptId"],
            "gameIdentity": identity,
            "stateAtAttempt": base.get("state_at_attempt"),
            "stateAfterAttempt": status.get("state"),
            "outcome": outcome,
            "reason": reason,
            "startWrite": attempt.get("startWrite"),
            "outcomeWrite": outcome_write,
        })
    return {
        "version": ATTEMPT_DIAGNOSTICS_VERSION,
        "appendOnly": True,
        "writeOnce": True,
        "attemptedGameCount": len(summaries),
        "attempts": summaries,
    }


def _diagnostic_history(module: Any, slate: str, game: Dict[str, Any], limit: int = 20) -> Dict[str, Any]:
    prefix = _diagnostic_prefix(module, game)
    try:
        response = module.TABLE.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
            ExpressionAttributeValues={
                ":pk": module._lock_pk(slate),
                ":prefix": prefix,
            },
            ConsistentRead=True,
            ScanIndexForward=False,
            Limit=limit,
        )
    except Exception as exc:
        return {
            "ok": False,
            "version": ATTEMPT_DIAGNOSTICS_VERSION,
            "error": f"diagnostic_query_failed:{type(exc).__name__}:{exc}",
        }
    items = [item for item in (response.get("Items") or []) if isinstance(item, dict)]
    by_attempt: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        attempt_id = str(item.get("attempt_id") or item.get("SK") or "")
        by_attempt.setdefault(attempt_id, []).append(item)
    latest_attempt_records = max(
        by_attempt.values(),
        key=lambda records: max(
            (
                str(item.get("attempted_at_utc") or ""),
                str(item.get("finished_at_utc") or ""),
                str(item.get("SK") or ""),
            )
            for item in records
        ),
        default=[],
    )
    latest_outcomes = [
        item
        for item in latest_attempt_records
        if item.get("diagnostic_event") == "ATTEMPT_OUTCOME"
    ]
    latest = max(
        latest_outcomes or latest_attempt_records,
        key=lambda item: (
            str(item.get("finished_at_utc") or ""),
            str(item.get("SK") or ""),
        ),
        default=None,
    )
    return {
        "ok": True,
        "version": ATTEMPT_DIAGNOSTICS_VERSION,
        "appendOnly": True,
        "writeOnce": True,
        "queriedRecordCount": len(items),
        "historyTruncated": bool(response.get("LastEvaluatedKey")),
        "latestAttempt": ({
            "attemptId": latest.get("attempt_id"),
            "attemptedAtUtc": latest.get("attempted_at_utc"),
            "finishedAtUtc": latest.get("finished_at_utc"),
            "stateAtAttempt": latest.get("state_at_attempt"),
            "stateAfterAttempt": latest.get("state_after_attempt"),
            "outcome": latest.get("outcome"),
            "reason": latest.get("reason"),
            "failureDetails": latest.get("failure_details") or [],
            "startObserved": any(
                item.get("attempt_id") == latest.get("attempt_id")
                and item.get("diagnostic_event") == "ATTEMPT_STARTED"
                for item in latest_attempt_records
            ),
            "outcomeObserved": latest.get("diagnostic_event") == "ATTEMPT_OUTCOME",
        } if latest else None),
    }


def _manifest_coverage(manifest: List[Dict[str, Any]], locked_count: int) -> Dict[str, Any]:
    identities = [game_identity(game) for game in manifest]
    return {
        "applied": True,
        "version": VERSION,
        "strictCoverageRequired": True,
        "doubleheaderSafeIdentity": True,
        "coverageComplete": bool(identities),
        "coverageMeaning": "complete_slate_manifest_membership_at_each_game_lock",
        "manifestCoverageComplete": bool(identities),
        "lockCoverageComplete": locked_count == len(identities) and bool(identities),
        "manifestGameCount": len(identities),
        "manifestGameIdentities": identities,
        "lockedGameCountAtWrite": locked_count,
        "pendingGameCountAtWrite": max(len(identities) - locked_count, 0),
        "publicAccuracyEligible": bool(identities),
        "operationalStatus": "COMPLETE_MANIFEST_ALL_LOCKED" if locked_count == len(identities) else "COMPLETE_MANIFEST_STAGED_LOCKING",
    }


def _per_game_lock(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    source_pull: Dict[str, Any],
    staged_at: datetime,
    manifest: List[Dict[str, Any]],
    locked_count: int,
) -> Dict[str, Any]:
    start = _start(module, game)
    lock_at = _lock_at(module, game)
    source_at = _pull_at(module, source_pull)
    return {
        "applied": True,
        "policyVersion": VERSION,
        "lockPolicy": LOCK_POLICY,
        "slateWideLock": False,
        "perGameLock": True,
        "locked": True,
        "finalLocked": True,
        "phase": "GAME_LOCKED",
        "lockStatus": "LOCKED_AT_OWN_TMINUS45",
        "lockMinutesBeforeGame": module.LOCK_MINUTES,
        "gameIdentity": game_identity(game),
        "gameStartUtc": start.isoformat() if start else None,
        "lockAtUtc": lock_at.isoformat() if lock_at else None,
        "scheduledLockAtUtc": lock_at.isoformat() if lock_at else None,
        "actualStagedAtUtc": staged_at.isoformat(),
        "latestScoringPullAt": source_at.isoformat() if source_at else None,
        "latestScoringPullId": source_pull.get("pull_id"),
        "source": "persisted_last_prelock_prediction_at_own_tminus45",
        "sourceAtOrBeforeLock": bool(source_at and lock_at and source_at <= lock_at),
        "manifestVersion": VERSION,
        "manifestGameCount": len(manifest),
        "manifestGameIdentities": [game_identity(item) for item in manifest],
        "lockedGameCountAtWrite": locked_count,
        "rules": [
            "Each game locks independently 45 minutes before its own scheduled start.",
            "The last persisted pre-lock prediction at or before that game's cutoff becomes the final lock.",
            "No model, signal, optimizer, or direction scoring is rerun at lock.",
            "A missing stage is never created at or after the game start.",
            "The immutable full-slate manifest is bound to every game-level lock.",
        ],
    }


def _remove_outcomes(row: Dict[str, Any]) -> None:
    for key in ("winner", "correct", "success", "homeWon", "pickCorrect", "outcome", "finalScore"):
        row.pop(key, None)


def _prepare_row(
    module: Any,
    row: Dict[str, Any],
    slate: str,
    game: Dict[str, Any],
    source_pull: Dict[str, Any],
    staged_at: datetime,
    manifest: List[Dict[str, Any]],
    locked_count: int,
    fingerprint_version: Optional[str] = PAYLOAD_FINGERPRINT_VERSION,
    reliability_block_reasons: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    out = copy.deepcopy(row)
    reliability_reasons = sorted({
        str(reason) for reason in (reliability_block_reasons or []) if reason
    })
    # Older persisted pre-lock rows may carry the selected-team probability
    # under one of the already-authoritative probability aliases.  Promote that
    # exact persisted value before freezing; this is field normalization only,
    # never a model/signal recomputation.  _selection_material applies the same
    # normalization so the candidate selection fingerprint remains unchanged.
    team_probability = _team_win_probability_pct(out)
    if team_probability in (None, ""):
        raise RuntimeError("LAST_PRELOCK_TEAM_WIN_PROBABILITY_MISSING")
    out["teamWinProbabilityPct"] = team_probability
    selection_fingerprint = _payload_fingerprint(
        _selection_material(out), fingerprint_version
    )
    source_game = _matching_game(source_pull, game) or {}
    canonical_game_id = _raw_game_identity(game)
    source_prediction_game_id = str(
        out.get("gameId") or out.get("gameIdentity") or _raw_game_identity(source_game) or ""
    )
    provider_event_id = (
        game.get("provider_event_id")
        or source_game.get("provider_event_id")
        or (
            _raw_game_identity(source_game)
            if source_game and _raw_game_identity(source_game) != canonical_game_id
            else None
        )
    )
    lock = _per_game_lock(module, slate, game, source_pull, staged_at, manifest, locked_count)
    coverage = _manifest_coverage(manifest, locked_count)
    out.update({
        "slate_date": slate,
        "slateDateEt": slate,
        "gameIdentity": canonical_game_id,
        "gameId": canonical_game_id,
        "sourcePredictionGameId": source_prediction_game_id or None,
        "sourcePredictionGameIdentity": out.get("gameIdentity") or source_prediction_game_id or None,
        "officialGamePk": game.get("official_game_pk") or game.get("officialGamePk"),
        "officialGameId": game.get("official_game_id") or game.get("officialGameId"),
        "providerEventId": provider_event_id,
        "providerCommenceTime": game.get("provider_commence_time") or source_game.get("provider_commence_time"),
        "providerStartDriftSeconds": (
            game.get("provider_start_drift_seconds")
            if game.get("provider_start_drift_seconds") is not None
            else source_game.get("provider_start_drift_seconds")
        ),
        "canonicalStartTimeSource": game.get("canonical_start_time_source"),
        "commenceTime": game.get("commence_time") or game.get("commenceTime") or out.get("commenceTime"),
        "homeTeam": game.get("home_team") or game.get("homeTeam") or out.get("homeTeam"),
        "awayTeam": game.get("away_team") or game.get("awayTeam") or out.get("awayTeam"),
        "lockedPrediction": True,
        "officialPrediction": True,
        "officialPick": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "lockedAtUtc": lock.get("lockAtUtc"),
        "scheduledLockAtUtc": lock.get("scheduledLockAtUtc"),
        "actualStagedAtUtc": lock.get("actualStagedAtUtc"),
        "predictionSourcePullAt": lock.get("latestScoringPullAt"),
        "predictionSourcePullId": lock.get("latestScoringPullId"),
        "lockedAmericanOdds": out.get("lockedAmericanOdds") if out.get("lockedAmericanOdds") not in (None, "") else out.get("americanOdds"),
        "slatePredictionLock": lock,
        "lastPossiblePredictionGate": {
            **lock,
            "gateWindowMinutesBeforeStart": {
                "opensAt": module.LOCK_MINUTES,
                "closesAt": module.LOCK_MINUTES,
                "meaning": "individual_game_tminus_cutoff",
            },
            "finalWindowActive": True,
        },
        "slateCoverage": coverage,
        "lockedCardAudit": {
            "applied": True,
            "version": VERSION,
            "selectionPolicy": "last_persisted_prelock_prediction_at_individual_game_tminus45",
            "lockedFlag": True,
            "lockAtUtc": lock.get("lockAtUtc"),
            "explicitSourceAtUtc": lock.get("latestScoringPullAt"),
            "actualStagedAtUtc": lock.get("actualStagedAtUtc"),
            "createdAtNotUsedAsScoringSource": True,
            "preventsLateRows": True,
            "perGameLock": True,
            "manifestGameCount": len(manifest),
            "manifestGameIdentities": [game_identity(item) for item in manifest],
        },
        "perGameLockVersion": VERSION,
        "lastPrelockPromotionVersion": PROMOTION_POLICY_VERSION,
        "modelOrSignalRecomputedAtLock": False,
        "immutablePerGameStage": True,
    })
    tags = {
        str(tag) for tag in (out.get("tags") or [])
        if str(tag) not in {"SLATE_WIDE_45_MIN_LOCK_POLICY", "SLATE_LOCKED"}
    }
    tags.update({"FINAL_LOCKED", "PER_GAME_TMINUS45_LOCKED", "COMPLETE_SLATE_MANIFEST_BOUND"})
    if reliability_reasons:
        existing_release_reasons = {
            str(reason)
            for reason in (
                out.get("playabilityBlockReasons")
                or out.get("releaseBlockReasons")
                or []
            )
            if reason
        }
        existing_release_reasons.update(reliability_reasons)
        out.update({
            "playable": False,
            "playablePick": False,
            "actionablePick": False,
            "blocked": True,
            "releaseBlocked": True,
            "wagerReleaseBlocked": True,
            "playabilityStatus": "BLOCKED",
            "playabilityBlockReasons": sorted(existing_release_reasons),
            "releaseBlockReasons": sorted(existing_release_reasons),
        })
        tags.update({"NOT_PLAYABLE", "RELEASE_BLOCKED", "WAGER_RELEASE_BLOCKED"})
        tags.difference_update({"ACTIONABLE_PICK", "PLAYABLE_PREDICTION"})
    out["tags"] = sorted(tags)
    freeze = dict(out.get("mlFeatureFreeze") or {})
    freeze.update({
        "completeSlateCoverage": True,
        "completeSlateCoverageMeaning": "complete_slate_manifest_membership_at_game_lock",
        "perGameLockVersion": VERSION,
        "scheduledLockAtUtc": lock.get("lockAtUtc"),
        "actualStagedAtUtc": lock.get("actualStagedAtUtc"),
    })
    exclusions = [str(reason) for reason in (freeze.get("trainingExclusionReasons") or []) if str(reason) != "incomplete_slate_coverage"]
    freeze["trainingExclusionReasons"] = exclusions
    if not exclusions and freeze.get("exactVectorCreated") is not False:
        freeze["trainingEligible"] = True
    out["mlFeatureFreeze"] = freeze
    _remove_outcomes(out)
    try:
        import mlb_ml_exact_lock_vector_patch
        import mlb_ml_frozen_features
        import mlb_official_prediction_semantics

        mlb_ml_exact_lock_vector_patch.apply(mlb_ml_frozen_features)
        normalized = mlb_official_prediction_semantics.enhance_result({
            "predictions": [out],
            "slateCoverage": coverage,
            "slatePredictionLock": lock,
        })
        normalized_rows = normalized.get("predictions") or []
        if len(normalized_rows) != 1:
            raise RuntimeError("official_semantics_did_not_return_one_row")
        out = normalized_rows[0]
    except Exception as exc:
        raise RuntimeError(f"LAST_PRELOCK_FINALIZATION_FAILED:{exc}") from exc

    try:
        out = mlb_ml_frozen_features.freeze_row(
            out,
            coverage_complete=True,
        )
    except Exception as exc:
        # Exact-vector creation is ML preparation, not selection authority. Keep
        # the source-authentic persisted winner and record the failure so the row
        # can never enter the training cohort.
        freeze = dict(out.get("mlFeatureFreeze") or {})
        exclusions = {
            str(reason)
            for reason in (freeze.get("trainingExclusionReasons") or [])
            if reason
        }
        exclusions.add("exact_lock_vector_freeze_failed")
        freeze.update({
            "exactVectorApplied": False,
            "exactVectorCreated": False,
            "exactVectorError": f"{type(exc).__name__}:{exc}",
            "trainingEligible": False,
            "trainingExclusionReasons": sorted(exclusions),
        })
        out["mlFeatureFreeze"] = freeze
        out["trainingEligible"] = False
        out["trainingEligibilityStatus"] = "INELIGIBLE"
    if reliability_reasons:
        freeze = dict(out.get("mlFeatureFreeze") or {})
        exclusions = {
            str(reason)
            for reason in (freeze.get("trainingExclusionReasons") or [])
            if reason
        }
        exclusions.update(
            f"lock_reliability:{reason.lower()}" for reason in reliability_reasons
        )
        freeze["trainingEligible"] = False
        freeze["trainingExclusionReasons"] = sorted(exclusions)
        out["mlFeatureFreeze"] = freeze
        out["trainingEligible"] = False
        out["trainingEligibilityStatus"] = "INELIGIBLE"
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as vector_contract

        out = vector_contract.apply_exact_vector_training_status(out)
    except Exception as exc:
        # Vector-status tooling is not selection authority. Persist an explicit
        # fail-closed training exclusion and allow the immutable stage checks to
        # continue validating the winner, source window, and write-once proof.
        vector_status_error = (
            f"exact_vector_status_unavailable:{type(exc).__name__}:{exc}"
        )
        vector_exclusion = f"exact_lock_vector_validation:{vector_status_error}"
        freeze = dict(out.get("mlFeatureFreeze") or {})
        exclusions = {
            str(reason)
            for reason in (freeze.get("trainingExclusionReasons") or [])
            if reason
        }
        exclusions.add(vector_exclusion)
        freeze.update({
            "exactVectorVerified": False,
            "exactVectorValidationErrors": [vector_status_error],
            "trainingEligible": False,
            "trainingExclusionReasons": sorted(exclusions),
            "selectionLockIndependentOfTrainingVector": True,
        })
        out.update({
            "exactVectorVerified": False,
            "exactVectorValidationErrors": [vector_status_error],
            "exactVectorStatusUnavailableAtLock": True,
            "trainingEligible": False,
            "trainingEligibilityStatus": "INELIGIBLE",
            "trainingExclusionReasons": sorted(exclusions),
            "selectionTrainingSeparationVersion": getattr(
                locals().get("vector_contract"),
                "VERSION",
                "MLB-SELECTION-TRAINING-SEPARATION-FALLBACK-v1",
            ),
            "mlFeatureFreeze": freeze,
        })
    if _payload_fingerprint(_selection_material(out), fingerprint_version) != selection_fingerprint:
        raise RuntimeError("LAST_PRELOCK_SELECTION_CHANGED_DURING_FINALIZATION")
    out["lastPrelockSelectionFingerprint"] = selection_fingerprint
    out["lastPrelockPromotionVersion"] = PROMOTION_POLICY_VERSION
    out["modelOrSignalRecomputedAtLock"] = False
    return out


def _vector_errors(row: Dict[str, Any], game: Dict[str, Any], lock_at: datetime, source_at: datetime) -> List[str]:
    errors: List[str] = []
    vector = row.get("frozenFeatureVector") or {}
    if not isinstance(vector, dict) or not vector:
        return ["missing_exact_frozen_feature_vector"]
    try:
        import mlb_ml_clean_cohort_v1 as cohort
        import mlb_ml_feature_missingness_v1 as missingness
        import mlb_temporal_features_v1 as temporal

        if vector.get("version") != cohort.FEATURE_SNAPSHOT_VERSION:
            errors.append("wrong_frozen_feature_vector_version")
        if vector.get("fingerprintVersion") != cohort.FINGERPRINT_VERSION:
            errors.append("wrong_frozen_feature_fingerprint_version")
        if vector.get("fingerprint") != cohort.fingerprint_for_vector(vector):
            errors.append("frozen_feature_fingerprint_mismatch")
        if vector.get("temporalFeatureVersion") != temporal.VERSION:
            errors.append("wrong_temporal_feature_version")
        if vector.get("temporalFeaturesAtOrBeforeLock") is not True:
            errors.append("temporal_features_not_lock_safe")
        temporal_source = _parse_iso(vector.get("temporalSourcePullAtUtc"))
        if not temporal_source or temporal_source > source_at or temporal_source > lock_at:
            errors.append("temporal_source_after_or_missing_lock_source")
        if not temporal.provenance_is_lock_safe(
            (row.get("homeSignal") or {}).get("temporalFeatures"), source_at, lock_at
        ):
            errors.append("home_temporal_provenance_not_lock_safe")
        if not temporal.provenance_is_lock_safe(
            (row.get("awaySignal") or {}).get("temporalFeatures"), source_at, lock_at
        ):
            errors.append("away_temporal_provenance_not_lock_safe")
        if vector.get("missingnessFeatureVersion") != missingness.VERSION:
            errors.append("wrong_fundamental_missingness_version")
        if vector.get("fundamentalMasksAtOrBeforeLock") is not True:
            errors.append("fundamental_masks_not_lock_safe")
        if not missingness.provenance_is_lock_safe(
            row.get("fundamentalsSnapshot"), source_at, lock_at, _parse_iso
        ):
            errors.append("fundamentals_snapshot_provenance_not_lock_safe")
        snapshot = row.get("fundamentalsSnapshot") or {}
        if vector.get("fundamentalsSnapshotVersion") != snapshot.get("version"):
            errors.append("fundamentals_snapshot_version_mismatch")
        if _parse_iso(vector.get("fundamentalsSnapshotAsOfUtc")) != _parse_iso(snapshot.get("asOfUtc")):
            errors.append("fundamentals_snapshot_timestamp_mismatch")
    except Exception as exc:
        errors.append(f"frozen_vector_verifier_unavailable:{exc}")
    if str(vector.get("gameId") or "") != str(row.get("gameId") or ""):
        errors.append("vector_game_identity_mismatch")
    if _parse_iso(vector.get("lockAtUtc")) != lock_at:
        errors.append("vector_scheduled_lock_mismatch")
    if _parse_iso(vector.get("sourcePullAtUtc")) != source_at:
        errors.append("vector_source_pull_mismatch")
    labels = vector.get("labels") or {}
    if labels.get("homeWon") is not None or labels.get("pickCorrect") is not None:
        errors.append("pregame_vector_contains_outcome")
    if str(vector.get("predictedWinner") or "").strip().lower() != str(row.get("predictedWinner") or "").strip().lower():
        errors.append("vector_predicted_winner_mismatch")
    if vector.get("predictedSide") != row.get("predictedSide"):
        errors.append("vector_predicted_side_mismatch")
    if game_identity(row) != game_identity(game):
        errors.append("row_manifest_game_identity_mismatch")
    return errors


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _selected_price_proven(row: Dict[str, Any]) -> bool:
    side = row.get("predictedSide")
    signal = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    signal = signal if isinstance(signal, dict) else {}
    price = row.get("lockedAmericanOdds")
    if price in (None, "", 0, 0.0):
        price = row.get("americanOdds") or signal.get("americanOdds")
    book = row.get("priceBook") or signal.get("priceBook")
    source = str(row.get("priceSource") or signal.get("priceSource") or "").lower()
    return bool(price not in (None, "", 0, 0.0) and book and source in {"real_book", "locked_real_book"})


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _raw_game_identity(game: Dict[str, Any]) -> str:
    identity = game_identity(game)
    return identity.replace("provider:", "", 1) if identity.startswith("provider:") else identity


def _query_prediction_items(module: Any, slate: str, prefix: str, limit: int = 500) -> List[Dict[str, Any]]:
    table = module.history.PULLS
    response = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={
            ":pk": f"GAME_WINNERS#mlb#{slate}",
            ":prefix": prefix,
        },
        ConsistentRead=True,
        ScanIndexForward=False,
        Limit=limit,
    )
    return [copy.deepcopy(item) for item in (response.get("Items") or []) if isinstance(item, dict)]


def _candidate_created_at(item: Dict[str, Any], row: Dict[str, Any]) -> Optional[datetime]:
    return _parse_iso(
        item.get("prediction_created_at_utc")
        or item.get("created_at")
        or row.get("createdAt")
        or row.get("created_at")
    )


def _candidate_persisted_at(item: Dict[str, Any]) -> Optional[datetime]:
    # This must be sampled after a successful DynamoDB live-row put.  A client
    # timestamp sampled before the write is not persistence authority.
    return _parse_iso(item.get("prediction_persisted_at_utc"))


def _candidate_source_at(item: Dict[str, Any], row: Dict[str, Any]) -> Optional[datetime]:
    lock = row.get("slatePredictionLock") or row.get("lastPossiblePredictionGate") or {}
    return _parse_iso(
        item.get("prediction_source_pull_at_utc")
        or row.get("predictionSourcePullAt")
        or lock.get("latestScoringPullAt")
        or (row.get("frozenFeatureVector") or {}).get("sourcePullAtUtc")
    )


def _candidate_source_id(item: Dict[str, Any], row: Dict[str, Any]) -> str:
    lock = row.get("slatePredictionLock") or row.get("lastPossiblePredictionGate") or {}
    return str(
        item.get("prediction_source_pull_id")
        or row.get("predictionSourcePullId")
        or lock.get("latestScoringPullId")
        or ""
    )


def _selection_material(row: Dict[str, Any]) -> Dict[str, Any]:
    material = {
        key: copy.deepcopy(row.get(key))
        for key in (
            "predictedWinner",
            "predictedSide",
            "opponent",
            "americanOdds",
            "priceBook",
            "priceSource",
            "marketSide",
            "fairProbabilityPct",
            "winProbability",
            "winProbabilityPct",
            "teamWinProbabilityPct",
            "edgeVsBook",
            "edgeVsBookPct",
            "expectedValue",
            "expectedValuePct",
            "promoted",
            "promotionStatus",
            "blockedReasons",
            "score",
            "confidenceTier",
            "pickQuality",
            "homeSignal",
            "awaySignal",
            "fundamentalsSnapshot",
        )
    }
    material["teamWinProbabilityPct"] = copy.deepcopy(
        _team_win_probability_pct(row)
    )
    return material


def _team_win_probability_pct(row: Dict[str, Any]) -> Any:
    """Return an existing persisted selected-team probability as a percent.

    The aliases are ordered from the explicit modern field to legacy fields.
    Deriving percent from a 0..1 probability is a unit conversion, and the
    chosen side's ``probLatest`` is used only when that exact persisted signal
    is the sole available probability.  No scoring inputs are consulted.
    """
    direct = row.get("teamWinProbabilityPct")
    if direct not in (None, ""):
        return direct
    legacy_pct = row.get("winProbabilityPct")
    if legacy_pct not in (None, ""):
        return legacy_pct

    probability = row.get("winProbability")
    if probability in (None, ""):
        probability = _frozen_selected_team_probability(row)
    if probability in (None, ""):
        side = row.get("predictedSide")
        signal = row.get("homeSignal") if side == "home" else row.get("awaySignal")
        signal = signal if isinstance(signal, dict) else {}
        probability = signal.get("probLatest")
    if probability in (None, "") or isinstance(probability, bool):
        return None
    try:
        if probability < 0 or probability > 1:
            return None
        return probability * 100
    except (TypeError, ValueError):
        return None


def _frozen_selected_team_probability(row: Dict[str, Any]) -> Any:
    """Read the selected-team value only from a fingerprint-valid frozen vector."""
    vector = row.get("frozenFeatureVector") or {}
    if not isinstance(vector, dict) or not vector:
        return None
    try:
        import mlb_ml_clean_cohort_v1 as cohort

        labels = vector.get("labels") or {}
        if (
            vector.get("version") != cohort.FEATURE_SNAPSHOT_VERSION
            or vector.get("fingerprintVersion") != cohort.FINGERPRINT_VERSION
            or vector.get("fingerprint") != cohort.fingerprint_for_vector(vector)
            or str(vector.get("gameId") or "") != str(row.get("gameId") or "")
            or vector.get("predictedSide") != row.get("predictedSide")
            or _norm(vector.get("predictedWinner")) != _norm(row.get("predictedWinner"))
            or not {"homeWon", "pickCorrect"}.issubset(labels)
            or labels.get("homeWon") is not None
            or labels.get("pickCorrect") is not None
        ):
            return None
        features = vector.get("features") or {}
        value = features.get("selectedTeamWinProbability")
        if value in (None, ""):
            # The canonical clean-cohort vector calls this same selected-side
            # persisted probability ``selectedMarketProb``.
            value = features.get("selectedMarketProb")
        if value in (None, "") or isinstance(value, bool) or value < 0 or value > 1:
            return None
        return value
    except (ImportError, AttributeError, TypeError, ValueError):
        return None


def _payload_fingerprint(
    payload: Any,
    version: Optional[str] = PAYLOAD_FINGERPRINT_VERSION,
) -> str:
    if version in (None, ""):
        return history_contract.legacy_payload_fingerprint(payload)
    if version == PAYLOAD_FINGERPRINT_VERSION:
        return history_contract.canonical_payload_fingerprint(payload)
    raise ValueError(f"unsupported payload fingerprint version: {version}")


def _candidate_items(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    scoring: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    # A game initially absent from the market feed is rostered under its stable
    # Stats API fallback ID. If the provider later adds it, query both immutable
    # identities and bind them through official_game_pk rather than orphaning
    # the valid provider-ID prediction snapshots.
    aliases = {_raw_game_identity(game)}
    for pull in scoring or []:
        for candidate in pull.get("games") or []:
            if _same_game(game, candidate):
                aliases.add(_raw_game_identity(candidate))
    items: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for raw_identity in sorted(alias for alias in aliases if alias):
        for item in _query_prediction_items(
            module,
            slate,
            f"PREGAME#GAME#{raw_identity}#",
        ):
            key = (str(item.get("PK") or ""), str(item.get("SK") or ""))
            items[key] = item
    return list(items.values())


def _source_pull_for_candidate(
    module: Any,
    scoring: List[Dict[str, Any]],
    source_at: datetime,
    source_id: str,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    earlier: List[Dict[str, Any]] = []
    same_timestamp: List[Dict[str, Any]] = []
    for pull in scoring:
        pulled_at = _pull_at(module, pull)
        if not pulled_at:
            continue
        if pulled_at < source_at:
            earlier.append(pull)
        elif pulled_at == source_at:
            same_timestamp.append(pull)

    if source_id:
        exact = [
            pull
            for pull in same_timestamp
            if str(pull.get("pull_id") or "") == source_id
        ]
    else:
        # A timestamp alone is authoritative only when it identifies one pull.
        # Otherwise two provider responses could share the same completion time
        # while carrying different prices.
        exact = same_timestamp if len(same_timestamp) == 1 else []
    if len(exact) != 1:
        return None, earlier
    matching = exact[0]
    return matching, earlier + [matching]


def _candidate_price_matches_source(
    row: Dict[str, Any],
    source_pull: Dict[str, Any],
    game: Dict[str, Any],
) -> bool:
    side = row.get("predictedSide")
    selected_signal = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    selected_signal = selected_signal if isinstance(selected_signal, dict) else {}
    book = str(row.get("priceBook") or selected_signal.get("priceBook") or "").lower().strip()
    price = row.get("lockedAmericanOdds")
    if price in (None, "", 0, 0.0):
        price = row.get("americanOdds") or selected_signal.get("americanOdds")
    source_game = _matching_game(source_pull, game)
    book_payload = ((source_game or {}).get("books") or {}).get(book) or {}
    market = book_payload.get("ml") or book_payload.get("moneyline") or {}
    source_price = market.get(side) if side in {"home", "away"} else None
    try:
        return bool(book and price not in (None, "", 0, 0.0) and float(price) == float(source_price))
    except Exception:
        return False


def _last_prelock_candidate(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    scoring: List[Dict[str, Any]],
    *,
    at_or_before: Optional[datetime] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    lock_at = _lock_at(module, game)
    if not lock_at:
        return None, None, [], ["scheduled_lock_missing"]
    selection_cutoff = (
        min(lock_at, at_or_before.astimezone(timezone.utc))
        if at_or_before is not None
        else lock_at
    )
    expected_identity = game_identity(game)
    candidate_aliases = {_raw_game_identity(game)}
    for pull in scoring:
        for candidate_game in pull.get("games") or []:
            if _same_game(game, candidate_game):
                candidate_aliases.add(_raw_game_identity(candidate_game))
    expected_home = _norm(game.get("home_team") or game.get("homeTeam"))
    expected_away = _norm(game.get("away_team") or game.get("awayTeam"))
    eligible: List[Tuple[datetime, datetime, datetime, Dict[str, Any], Dict[str, Any]]] = []
    observed_prelock = 0
    for item in _candidate_items(module, slate, game, scoring):
        if item.get("record_type") != PREGAME_SNAPSHOT_RECORD_TYPE:
            continue
        row = item.get("data") or {}
        if not isinstance(row, dict):
            continue
        if (
            not _same_game(game, row)
            and _raw_game_identity(row) not in candidate_aliases
        ):
            continue
        if (
            _norm(row.get("homeTeam") or row.get("home_team")) != expected_home
            or _norm(row.get("awayTeam") or row.get("away_team")) != expected_away
        ):
            continue
        created_at = _candidate_created_at(item, row)
        persisted_at = _candidate_persisted_at(item)
        source_at = _candidate_source_at(item, row)
        if (
            not created_at
            or not persisted_at
            or not source_at
            or created_at > selection_cutoff
            or persisted_at > selection_cutoff
            or source_at > selection_cutoff
        ):
            continue
        observed_prelock += 1
        eligible.append((persisted_at, created_at, source_at, item, row))
    if not eligible:
        return None, None, [], [
            "no_persisted_user_visible_platform_prelock_prediction_at_or_before_cutoff"
            if observed_prelock == 0
            else "no_valid_user_visible_platform_prelock_prediction"
        ]

    rejected: List[Dict[str, Any]] = []
    for persisted_at, created_at, source_at, item, row in sorted(
        eligible,
        key=lambda entry: (entry[0], entry[1], entry[2]),
        reverse=True,
    ):
        errors: List[str] = _public_prelock_marker_errors(item, row)
        raw_fingerprint_version = item.get("prediction_payload_fingerprint_version")
        fingerprint_version = (
            None
            if raw_fingerprint_version in (None, "")
            else raw_fingerprint_version
        )
        fingerprint_version_supported = _supported_payload_fingerprint_version(
            raw_fingerprint_version
        )
        if not fingerprint_version_supported:
            errors.append("persisted_prelock_payload_fingerprint_version_unsupported")
        if item.get("prediction_persistence_proof_type") != PREGAME_PERSISTENCE_PROOF_TYPE:
            errors.append("persisted_prelock_post_write_ack_missing")
        expected_live_pk = f"GAME_WINNERS#mlb#{slate}"
        persisted_identity = str(
            item.get("game_identity")
            or row.get("gameIdentity")
            or row.get("gameId")
            or "unknown"
        )
        expected_live_sk = f"GAME#{row.get('commenceTime') or 'unknown'}#{persisted_identity}"
        if item.get("prediction_persistence_write_pk") != expected_live_pk:
            errors.append("persisted_prelock_write_pk_mismatch")
        if item.get("prediction_persistence_write_sk") != expected_live_sk:
            errors.append("persisted_prelock_write_sk_mismatch")
        if fingerprint_version_supported and item.get(
            "prediction_payload_fingerprint"
        ) != _payload_fingerprint(row, fingerprint_version):
            errors.append("persisted_prelock_payload_fingerprint_mismatch")
        if not row.get("predictedWinner") or row.get("predictedSide") not in {"home", "away"}:
            errors.append("persisted_prelock_selection_missing")
        if row.get("predictedSide") == "home":
            expected_winner = game.get("home_team") or game.get("homeTeam")
        else:
            expected_winner = game.get("away_team") or game.get("awayTeam")
        if _norm(row.get("predictedWinner")) != _norm(expected_winner):
            errors.append("persisted_prelock_winner_side_mismatch")
        if not _selected_price_proven(row):
            errors.append("persisted_prelock_selected_side_real_book_price_missing")
        source_id = _candidate_source_id(item, row)
        source_pull, bound_scoring = _source_pull_for_candidate(module, scoring, source_at, source_id)
        if source_pull is None:
            errors.append("persisted_prelock_source_pull_not_found_in_cutoff_history")
        elif not _candidate_price_matches_source(row, source_pull, game):
            errors.append("persisted_prelock_selected_price_not_in_source_pull")
        if created_at < source_at:
            errors.append("persisted_prelock_created_before_source_pull")
        if persisted_at < created_at:
            errors.append("persisted_prelock_written_before_prediction_creation")
        candidate_identity = str(
            item.get("game_identity")
            or _raw_game_identity(row)
            or ""
        )
        stage_identity = _raw_game_identity(game)
        candidate_official_game_pk = _official_game_pk(row)
        stage_official_game_pk = _official_game_pk(game)
        if candidate_identity == stage_identity:
            identity_binding_mode = "exact_identity"
        elif (
            candidate_official_game_pk
            and stage_official_game_pk
            and candidate_official_game_pk == stage_official_game_pk
        ):
            identity_binding_mode = "official_game_pk"
        elif _legacy_identity_crosswalk_match(game, row):
            identity_binding_mode = "legacy_team_start_crosswalk"
        else:
            identity_binding_mode = "unverified"
            errors.append("persisted_prelock_official_identity_binding_missing")
        source_age = (selection_cutoff - source_at).total_seconds() / 60.0
        # Age is a release/training reliability gate, not an integrity error.
        # A source-authentic pre-cutoff winner still receives its T-45 lock.
        for key in ("winner", "correct", "success", "homeWon", "pickCorrect", "outcome", "finalScore"):
            if key in row:
                errors.append(f"persisted_prelock_contains_{key}")
        labels = (row.get("frozenFeatureVector") or {}).get("labels") or {}
        if labels.get("homeWon") is not None or labels.get("pickCorrect") is not None:
            errors.append("persisted_prelock_vector_contains_outcome")
        try:
            import mlb_ml_feature_missingness_v1 as missingness
            import mlb_temporal_features_v1 as temporal

            if not temporal.provenance_is_lock_safe(
                (row.get("homeSignal") or {}).get("temporalFeatures"), source_at, lock_at
            ):
                errors.append("persisted_prelock_home_temporal_provenance_not_lock_safe")
            if not temporal.provenance_is_lock_safe(
                (row.get("awaySignal") or {}).get("temporalFeatures"), source_at, lock_at
            ):
                errors.append("persisted_prelock_away_temporal_provenance_not_lock_safe")
            if not missingness.provenance_is_lock_safe(
                row.get("fundamentalsSnapshot"), source_at, lock_at, _parse_iso
            ):
                errors.append("persisted_prelock_fundamentals_provenance_not_lock_safe")
        except Exception as exc:
            errors.append(f"persisted_prelock_provenance_verifier_unavailable:{exc}")
        if errors:
            rejected.append({
                "sk": item.get("SK"),
                "predictionPersistedAtUtc": persisted_at.isoformat(),
                "errors": sorted(set(errors)),
            })
            continue

        proof = {
            "version": PROMOTION_POLICY_VERSION,
            "pk": item.get("PK"),
            "sk": item.get("SK"),
            "recordType": item.get("record_type"),
            "snapshotVersion": item.get("snapshot_version"),
            "snapshotRole": item.get("snapshot_role"),
            "publicAuthorityVersion": item.get("public_authority_version"),
            "userVisible": item.get("user_visible"),
            "displayPrediction": item.get("display_prediction"),
            "displayStatus": item.get("display_status"),
            "displaySurface": item.get("display_surface"),
            "signalPolicyVersion": item.get("signal_policy_version"),
            "predictionCreatedAtUtc": created_at.isoformat(),
            "predictionPersistedAtUtc": persisted_at.isoformat(),
            "persistenceProofType": item.get("prediction_persistence_proof_type"),
            "persistenceWritePk": item.get("prediction_persistence_write_pk"),
            "persistenceWriteSk": item.get("prediction_persistence_write_sk"),
            "predictionPayloadFingerprint": item.get("prediction_payload_fingerprint"),
            "predictionPayloadFingerprintVersion": item.get("prediction_payload_fingerprint_version"),
            "candidateSnapshotFingerprint": _payload_fingerprint(item, fingerprint_version),
            "predictionSourcePullAtUtc": source_at.isoformat(),
            "predictionSourcePullId": source_pull.get("pull_id") if source_pull else source_id or None,
            "sourceAgeAtCutoffMinutes": round(source_age, 4),
            "evaluationCutoffAtUtc": selection_cutoff.isoformat(),
            "sourceAtOrBeforeCutoff": source_at <= selection_cutoff,
            "createdAtOrBeforeCutoff": created_at <= selection_cutoff,
            "persistedAtOrBeforeCutoff": persisted_at <= selection_cutoff,
            "candidateRowFingerprint": _payload_fingerprint(row, fingerprint_version),
            "candidateSelectionFingerprint": _payload_fingerprint(
                _selection_material(row), fingerprint_version
            ),
            "candidateVectorFingerprint": (row.get("frozenFeatureVector") or {}).get("fingerprint"),
            "candidateGameIdentity": candidate_identity,
            "stageGameIdentity": stage_identity,
            "candidateOfficialGamePk": candidate_official_game_pk or None,
            "stageOfficialGamePk": stage_official_game_pk or None,
            "identityBindingMode": identity_binding_mode,
            "rejectedNewerCandidateCount": len(rejected),
            "rejectedNewerCandidates": rejected,
            "promotionRule": "last_valid_persisted_prediction_at_or_before_own_tminus45_becomes_final_lock",
            "modelOrSignalRecomputedAtLock": False,
        }
        return copy.deepcopy(row), proof, bound_scoring, []

    rejection_errors = sorted({
        error
        for rejection in rejected
        for error in (rejection.get("errors") or [])
    })
    return None, None, [], [
        "no_valid_user_visible_platform_prelock_prediction",
        *rejection_errors,
    ]


def _validate_stage(
    module: Any,
    item: Dict[str, Any],
    slate: str,
    game: Dict[str, Any],
    manifest: List[Dict[str, Any]],
    scoring: List[Dict[str, Any]],
) -> List[str]:
    errors: List[str] = []
    row = (item.get("data") or {}).get("row") or {}
    expected_manifest = _canonical_manifest_identities(manifest)
    actual_manifest = list((item.get("data") or {}).get("manifestGameIdentities") or [])
    # Once a stage exists, its own immutable schedule revision remains the
    # temporal authority for validation. A later official reschedule may
    # change pending-game lifecycle timing, but cannot rewrite or invalidate
    # an already locked winner.
    start = _parse_iso(item.get("commence_time"))
    lock_at = _parse_iso(item.get("scheduled_lock_at_utc"))
    staged_at = _parse_iso(item.get("staged_at_utc"))
    source_at = _parse_iso(item.get("source_pull_at_utc"))
    stable_at = (
        lock_at + timedelta(seconds=CUTOFF_STABILIZATION_SECONDS)
        if lock_at
        else None
    )
    source_window = item.get("source_window") or {}
    candidate_proof = item.get("candidate_proof") or {}
    raw_bound_entries = source_window.get("pulls") or []
    bound_entries = raw_bound_entries if isinstance(raw_bound_entries, list) else []
    current_entries = _source_window_entries(module, scoring, game)
    bound_keys = {_source_window_key(entry) for entry in bound_entries if isinstance(entry, dict)}
    current_keys = {_source_window_key(entry) for entry in current_entries}
    source_window_closed_at = _parse_iso(source_window.get("closedAtUtc"))
    if item.get("model_version") != VERSION:
        errors.append("stage_model_version_mismatch")
    if item.get("lock_policy") != LOCK_POLICY:
        errors.append("stage_lock_policy_mismatch")
    if item.get("record_type") != STAGE_RECORD_TYPE or item.get("immutable_staged") is not True:
        errors.append("not_immutable_per_game_stage")
    if item.get("write_once") is not True:
        errors.append("stage_not_write_once")
    if item.get("promotion_policy_version") != PROMOTION_POLICY_VERSION:
        errors.append("wrong_last_prelock_promotion_policy")
    if candidate_proof.get("version") != PROMOTION_POLICY_VERSION:
        errors.append("missing_last_prelock_candidate_proof")
    if candidate_proof.get("modelOrSignalRecomputedAtLock") is not False:
        errors.append("lock_rescore_not_explicitly_disabled")
    if candidate_proof.get("sourceAtOrBeforeCutoff") is not True:
        errors.append("candidate_source_not_proven_prelock")
    if candidate_proof.get("createdAtOrBeforeCutoff") is not True:
        errors.append("candidate_creation_not_proven_prelock")
    if candidate_proof.get("persistedAtOrBeforeCutoff") is not True:
        errors.append("candidate_persistence_not_proven_prelock")
    if candidate_proof.get("persistenceProofType") != PREGAME_PERSISTENCE_PROOF_TYPE:
        errors.append("candidate_post_write_ack_proof_missing")
    if candidate_proof.get("snapshotVersion") != PREGAME_SNAPSHOT_VERSION:
        errors.append("candidate_snapshot_version_mismatch")
    if candidate_proof.get("snapshotRole") != PREGAME_SNAPSHOT_ROLE:
        errors.append("candidate_snapshot_role_mismatch")
    if candidate_proof.get("publicAuthorityVersion") != PREGAME_PUBLIC_AUTHORITY_VERSION:
        errors.append("candidate_public_authority_version_mismatch")
    if candidate_proof.get("userVisible") is not True:
        errors.append("candidate_user_visible_marker_missing")
    if candidate_proof.get("displayPrediction") is not True:
        errors.append("candidate_display_prediction_marker_missing")
    if candidate_proof.get("displayStatus") != PREGAME_DISPLAY_STATUS:
        errors.append("candidate_display_status_mismatch")
    if candidate_proof.get("displaySurface") != PREGAME_DISPLAY_SURFACE:
        errors.append("candidate_display_surface_mismatch")
    if (
        candidate_proof.get("predictionPayloadFingerprintVersion")
        != PAYLOAD_FINGERPRINT_VERSION
    ):
        errors.append("candidate_payload_fingerprint_version_mismatch")
    if row.get("modelOrSignalRecomputedAtLock") is not False:
        errors.append("row_lock_rescore_not_explicitly_disabled")
    if row.get("lastPrelockPromotionVersion") != PROMOTION_POLICY_VERSION:
        errors.append("row_last_prelock_promotion_version_mismatch")
    if row.get("lastPrelockSelectionFingerprint") != candidate_proof.get("candidateSelectionFingerprint"):
        errors.append("promoted_selection_differs_from_last_prelock_candidate")
    if str(item.get("slate_date") or "") != slate:
        errors.append("stage_slate_mismatch")
    if str(item.get("game_identity") or "") != game_identity(game):
        errors.append("stage_game_identity_mismatch")
    if actual_manifest != expected_manifest:
        errors.append("manifest_changed_after_game_lock")
    if not start or not lock_at or lock_at != start - timedelta(minutes=REQUIRED_LOCK_MINUTES):
        errors.append("scheduled_lock_mismatch")
    if not start or not staged_at or staged_at >= start:
        errors.append("late_stage_at_or_after_game_start")
    if source_window.get("version") != SOURCE_WINDOW_VERSION:
        errors.append("missing_or_wrong_bound_source_window_version")
    if not source_window_closed_at or source_window_closed_at != staged_at:
        errors.append("source_window_close_timestamp_mismatch")
    if stable_at and (not staged_at or staged_at < stable_at):
        errors.append("cutoff_source_window_not_stabilized")
    if not isinstance(raw_bound_entries, list) or not bound_entries:
        errors.append("missing_bound_source_window_pulls")
    elif len(bound_keys) != len(bound_entries):
        errors.append("duplicate_bound_source_window_pull")
    if bound_keys - current_keys:
        errors.append("bound_source_window_pull_missing_or_changed")
    if not source_at or not lock_at or source_at > lock_at:
        errors.append("source_after_or_missing_scheduled_lock")
    candidate_created_at = _parse_iso(candidate_proof.get("predictionCreatedAtUtc"))
    candidate_persisted_at = _parse_iso(candidate_proof.get("predictionPersistedAtUtc"))
    if (
        not candidate_created_at
        or not candidate_persisted_at
        or not lock_at
        or candidate_created_at > candidate_persisted_at
        or candidate_persisted_at > lock_at
    ):
        errors.append("candidate_post_write_ack_timestamp_invalid")
    latest_bound = bound_entries[-1] if bound_entries and isinstance(bound_entries[-1], dict) else {}
    if source_at != _parse_iso(latest_bound.get("pulledAtUtc")):
        errors.append("source_pull_timestamp_mismatch_bound_window")
    if str(item.get("source_pull_id") or "") != str(latest_bound.get("pullId") or ""):
        errors.append("source_pull_id_mismatch_bound_window")
    if int(item.get("pull_depth") or 0) != len(bound_entries):
        errors.append("pull_depth_mismatch")
    if item.get("stage_fingerprint") != _stage_fingerprint(item):
        errors.append("stage_fingerprint_mismatch")
    errors.extend(persisted_stage_authority_errors(module.TABLE, item))
    if not _selected_price_proven(row):
        errors.append("selected_side_real_book_price_missing")
    for key in ("winner", "correct", "success", "homeWon", "pickCorrect", "outcome", "finalScore"):
        if key in row:
            errors.append(f"pregame_stage_contains_{key}")
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as vector_contract

        errors.extend(vector_contract.validate_selection_lock_vector_status(row))
    except Exception as exc:
        errors.append(f"selection_vector_status_validator_unavailable:{exc}")
    return sorted(set(errors))


def _provider_manifest_authority(
    module: Any,
    pull: Dict[str, Any],
    slate: str,
    manifest: List[Dict[str, Any]],
) -> Dict[str, Any]:
    reader = getattr(module.history, "provider_manifest_authority_for_lock", None)
    if not callable(reader):
        raise RuntimeError("provider_manifest_authority_reader_unavailable")
    authority = reader(pull, slate, manifest)
    if not isinstance(authority, dict) or not authority:
        raise RuntimeError("provider_manifest_authority_missing")
    out = copy.deepcopy(authority)
    out["canonicalGameIdentities"] = _canonical_manifest_identities(manifest)
    return out


def _provider_schedule_material(games: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize schedule-only fields exactly as the immutable provider manifest does."""
    return sorted(
        [history_contract._provider_manifest_game("mlb", game) for game in games or []],
        key=history_contract._manifest_sort_key,
    )


def _canonical_manifest_identities(games: Iterable[Dict[str, Any]]) -> List[str]:
    """Canonical identities in the immutable provider manifest's exact order."""
    # Sort the caller's original schedule rows rather than the normalized
    # material: normalization intentionally synthesizes ``game_id`` from a
    # fallback key, which would change ``key:...`` identities into
    # ``provider:...`` for legacy provider rows without an ID.
    ordered = sorted(
        list(games or []),
        key=history_contract._manifest_sort_key,
    )
    return [game_identity(game) for game in ordered]


def _manifest_authority_identity(authority: Dict[str, Any]) -> Tuple[str, ...]:
    schedule_authority = authority.get("scheduleRevisionAuthority") or {}
    return tuple(
        str(authority.get(key) or "")
        for key in ("version", "pk", "sk", "fingerprint", "pullId", "observedAtUtc")
    ) + tuple(
        str(schedule_authority.get(key) or "")
        for key in ("version", "pk", "sk", "fingerprint", "pullId", "observedAtUtc")
    )


def _pull_history_identity(module: Any, pulls: Iterable[Dict[str, Any]]) -> Tuple[Tuple[str, ...], ...]:
    """Cheap immutable-history identity used to avoid redundant full-table reads."""
    return tuple(
        (
            str(pull.get("pull_id") or ""),
            (_pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc)).isoformat(),
            str((pull.get("provider_schedule_manifest") or {}).get("fingerprint") or ""),
            str((pull.get("provider_manifest_binding") or {}).get("fingerprint") or ""),
        )
        for pull in pulls or []
    )


def _select_provider_manifest_authority(
    module: Any,
    pulls: Iterable[Dict[str, Any]],
    slate: str,
    manifest: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Bind a lock to the resolver's durable immutable full-slate pull."""
    ordered_pulls = sorted(
        list(pulls or []),
        key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc),
    )
    if not ordered_pulls:
        raise RuntimeError("provider_manifest_pull_history_missing")
    if not manifest:
        raise RuntimeError("provider_manifest_complete_slate_missing")
    resolver = getattr(module.history, "verified_full_slate_manifest", None)
    if not callable(resolver):
        # Narrow compatibility path for injected/test adapters. Production's
        # history module always exposes the durable resolver.
        expected_material = _provider_schedule_material(manifest)
        for candidate_pull in reversed(ordered_pulls):
            candidate_games = list(
                (candidate_pull.get("provider_schedule_manifest") or {}).get("games") or []
            )
            if _provider_schedule_material(candidate_games) == expected_material:
                return _provider_manifest_authority(
                    module,
                    candidate_pull,
                    slate,
                    manifest,
                )
        raise RuntimeError("verified_full_slate_manifest_resolver_unavailable")

    resolved = resolver(ordered_pulls, slate)
    full_pull = resolved.get("fullAuthorityPull") if isinstance(resolved, dict) else None
    schedule_pull = (
        resolved.get("scheduleAuthorityPull")
        if isinstance(resolved, dict)
        else None
    ) or full_pull
    resolved_games = list((resolved or {}).get("games") or [])
    if not isinstance(full_pull, dict) or not isinstance(schedule_pull, dict):
        raise RuntimeError("provider_manifest_full_slate_authority_pull_missing")
    if _provider_schedule_material(resolved_games) != _provider_schedule_material(manifest):
        raise RuntimeError("provider_manifest_resolved_slate_membership_mismatch")
    official_errors: List[str] = []
    if resolved.get("officialScheduleBacked") is not True:
        official_errors.append("official_schedule_not_backed")
    if resolved.get("rosterAuthorityMode") != "MLB_STATS_API_EXACT_DATE":
        official_errors.append("official_schedule_roster_mode_mismatch")
    if resolved.get("officialScheduleAuthorityVersion") != OFFICIAL_SCHEDULE_AUTHORITY_VERSION:
        official_errors.append("official_schedule_version_mismatch")
    if resolved.get("officialScheduleAuthoritySource") != OFFICIAL_SCHEDULE_AUTHORITY_SOURCE:
        official_errors.append("official_schedule_source_mismatch")
    if resolved.get("officialScheduleAuthoritativeStartTimes") is not True:
        official_errors.append("official_schedule_start_authority_missing")
    try:
        official_count = int(resolved.get("officialScheduleGameCount"))
    except (TypeError, ValueError):
        official_count = -1
    if official_count != len(manifest):
        official_errors.append("official_schedule_game_count_mismatch")
    if not resolved.get("officialScheduleAuthorityFingerprint"):
        official_errors.append("official_schedule_fingerprint_missing")
    for resolved_game in resolved_games:
        if not _official_game_pk(resolved_game):
            official_errors.append("official_schedule_game_pk_missing")
        if resolved_game.get("canonical_start_time_source") != "MLB_STATS_API_EXACT_DATE":
            official_errors.append("official_schedule_game_start_source_mismatch")
        if _parse_iso(resolved_game.get("official_commence_time")) != _parse_iso(
            resolved_game.get("commence_time")
        ):
            official_errors.append("official_schedule_game_start_mismatch")
    if official_errors:
        raise RuntimeError(
            "official_schedule_authority_required:"
            + ",".join(sorted(set(official_errors)))
        )
    authority = _provider_manifest_authority(module, full_pull, slate, manifest)
    schedule_manifest_games = list(
        (schedule_pull.get("provider_schedule_manifest") or {}).get("games") or []
    )
    schedule_revision_authority = _provider_manifest_authority(
        module,
        schedule_pull,
        slate,
        schedule_manifest_games,
    )
    membership_authority = {
        key: copy.deepcopy(authority.get(key))
        for key in (
            "version",
            "recordType",
            "pk",
            "sk",
            "fingerprint",
            "slateDate",
            "observedAtUtc",
            "pullId",
            "gameCount",
            "gameIdentities",
            "canonicalGameIdentities",
            "immutable",
            "writeOnce",
            "fullProviderSchedule",
            "consistentReadVerified",
            "officialScheduleBacked",
            "officialScheduleAuthorityVersion",
            "officialScheduleAuthoritySource",
            "officialScheduleAuthorityFingerprint",
            "officialScheduleGameCount",
            "officialScheduleAuthoritativeRoster",
            "officialScheduleAuthoritativeStartTimes",
        )
    }
    authority.update({
        "membershipAuthority": membership_authority,
        "scheduleRevisionAuthority": schedule_revision_authority,
        "scheduleRevisionApplied": (
            _manifest_authority_identity(authority)
            != _manifest_authority_identity(schedule_revision_authority)
        ),
        "verifiedFullSlateManifestVersion": resolved.get("version"),
        "rosterAuthorityMode": resolved.get("rosterAuthorityMode"),
        "officialScheduleBacked": resolved.get("officialScheduleBacked") is True,
        "officialScheduleAuthorityVersion": resolved.get("officialScheduleAuthorityVersion"),
        "officialScheduleAuthoritySource": resolved.get("officialScheduleAuthoritySource"),
        "officialScheduleAuthorityFingerprint": resolved.get("officialScheduleAuthorityFingerprint"),
        "officialScheduleGameCount": resolved.get("officialScheduleGameCount"),
        "officialScheduleAuthoritativeStartTimes": resolved.get("officialScheduleAuthoritativeStartTimes") is True,
        "officialScheduleMissingProviderEventGameIds": list(resolved.get("officialScheduleMissingProviderEventGameIds") or []),
        "eventRosterBacked": resolved.get("eventRosterBacked") is True,
        "legacyMigrationFallback": resolved.get("legacyMigrationFallback") is True,
        "latestFeedAnomalyCount": int(resolved.get("latestFeedAnomalyCount") or 0),
    })
    return authority


def _generate_stage(
    module: Any,
    slate: str,
    game: Dict[str, Any],
    manifest: List[Dict[str, Any]],
    scoring: List[Dict[str, Any]],
    locked_count: int,
    provider_manifest_authority: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    start = _start(module, game)
    now = module._now_utc().astimezone(timezone.utc)
    if not start or now >= start:
        return None, ["late_stage_blocked_game_already_started"]
    stable_at = _cutoff_stable_at(module, game)
    if not stable_at or now < stable_at:
        return None, ["cutoff_source_window_still_stabilizing"]
    candidate, candidate_proof, bound_scoring, candidate_errors = _last_prelock_candidate(
        module,
        slate,
        game,
        scoring,
    )
    if candidate_errors or not candidate or not candidate_proof or not bound_scoring:
        return None, candidate_errors or ["persisted_prelock_prediction_missing"]
    source = bound_scoring[-1]
    source_at = _pull_at(module, source)
    lock_at = _lock_at(module, game)
    reliability_block_reasons: List[str] = []
    if len(bound_scoring) < module.MIN_PULLS_PER_GAME_FOR_LOCK:
        reliability_block_reasons.append("INSUFFICIENT_PULL_DEPTH_AT_LOCK")
    if (
        source_at is None
        or lock_at is None
        or (lock_at - source_at).total_seconds() / 60.0
        > module.MAX_LATEST_PULL_AGE_MINUTES
    ):
        reliability_block_reasons.append("STALE_OR_MISSING_SOURCE_AT_LOCK")
    try:
        row = _prepare_row(
            module,
            candidate,
            slate,
            game,
            source,
            now,
            manifest,
            locked_count,
            candidate_proof.get("predictionPayloadFingerprintVersion") or None,
            reliability_block_reasons,
        )
    except Exception as exc:
        return None, [str(exc)]
    if row.get("lastPrelockSelectionFingerprint") != candidate_proof.get("candidateSelectionFingerprint"):
        return None, ["last_prelock_selection_fingerprint_changed"]
    source_window = {
        "version": SOURCE_WINDOW_VERSION,
        "closedAtUtc": now.isoformat(),
        "scheduledCutoffAtUtc": (_lock_at(module, game) or now).isoformat(),
        "stabilizationSeconds": CUTOFF_STABILIZATION_SECONDS,
        "pulls": _source_window_entries(module, bound_scoring, game),
    }
    if not isinstance(provider_manifest_authority, dict) or not provider_manifest_authority:
        return None, ["provider_manifest_authority_missing"]
    item = module.history.ddb_safe({
        **_stage_key(module, slate, game),
        "record_type": STAGE_RECORD_TYPE,
        "sport": "mlb",
        "slate_date": slate,
        "model_version": VERSION,
        "lock_policy": LOCK_POLICY,
        "promotion_policy_version": PROMOTION_POLICY_VERSION,
        "immutable_staged": True,
        "write_once": True,
        "game_identity": game_identity(game),
        "game_id": row.get("gameId"),
        "official_game_pk": game.get("official_game_pk") or game.get("officialGamePk"),
        "provider_event_id": row.get("providerEventId"),
        "commence_time": row.get("commenceTime"),
        "scheduled_lock_at_utc": (_lock_at(module, game) or now).isoformat(),
        "staged_at_utc": now.isoformat(),
        "source_pull_at_utc": (_pull_at(module, source) or now).isoformat(),
        "source_pull_id": source.get("pull_id"),
        "pull_depth": len(bound_scoring),
        "source_window": source_window,
        "candidate_proof": candidate_proof,
        "provider_manifest_authority": provider_manifest_authority,
        "manifest_game_count": len(manifest),
        "data": {
            "row": row,
            "manifestGameIdentities": _canonical_manifest_identities(manifest),
        },
        "created_at": now.isoformat(),
    })
    item["stage_fingerprint"] = _stage_fingerprint(item)
    errors = _validate_stage(module, item, slate, game, manifest, bound_scoring)
    return (item if not errors else None), errors


def _put_stage(module: Any, item: Dict[str, Any], slate: str, game: Dict[str, Any]) -> Dict[str, Any]:
    try:
        module.TABLE.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return item
    except Exception as exc:
        if not _conditional_collision(exc):
            raise
        existing = _get_stage(module, slate, game)
        if not existing:
            raise RuntimeError("PER_GAME_STAGE_CONDITIONAL_COLLISION_WITHOUT_READBACK") from exc
        return existing


def _canonical_store(module: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    response = module.mlb_game_winner_engine._store_prediction(copy.deepcopy(row))
    vector_status_explicit = isinstance(response, dict) and isinstance(
        response.get("exactVectorVerified"), bool
    )
    vector_training_safe = bool(
        response.get("exactVectorVerified") is True
        or (
            response.get("exactVectorVerified") is False
            and response.get("trainingEligible") is False
            and response.get("trainingExclusionReasons")
        )
    ) if isinstance(response, dict) else False
    required = bool(
        isinstance(response, dict)
        and response.get("ok") is True
        and response.get("storageClass") == "LOCKED_IMMUTABLE"
        and response.get("writeOnce") is True
        and response.get("selectionLockVerified") is True
        and vector_status_explicit
        and vector_training_safe
    )
    if not required:
        raise RuntimeError(f"CANONICAL_PER_GAME_WRITE_NOT_PROVEN:{response}")
    return response


def _canonical_readback(module: Any, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    identity = str(row.get("gameIdentity") or row.get("gameId") or row.get("game_id") or "unknown")
    commence = str(row.get("commenceTime") or row.get("commence_time") or "unknown")
    slate = str(row.get("slate_date") or row.get("slateDateEt") or "unknown")
    response = module.history.PULLS.get_item(
        Key={
            "PK": f"GAME_WINNERS#mlb#{slate}",
            "SK": f"LOCKED#GAME#{commence}#{identity}",
        },
        ConsistentRead=True,
    )
    item = response.get("Item")
    stored = (item or {}).get("data") if isinstance((item or {}).get("data"), dict) else None
    if not stored:
        return None
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as vector_contract
        import mlb_immutable_locked_storage_patch as immutable_storage

        if immutable_storage.validate_canonical_stage_authority(module.history.PULLS, stored):
            return None
        if vector_contract.validate_selection_lock_vector_status(stored):
            return None
        vector_errors = vector_contract.effective_selection_lock_vector_errors(stored)
    except Exception:
        return None
    if (
        row.get("lastPrelockSelectionFingerprint")
        != stored.get("lastPrelockSelectionFingerprint")
        or row.get("predictedWinner") != stored.get("predictedWinner")
        or row.get("predictedSide") != stored.get("predictedSide")
    ):
        return None
    training = stored.get("mlFeatureFreeze") or {}
    return {
        "ok": True,
        "pk": (item or {}).get("PK"),
        "sk": (item or {}).get("SK"),
        "storageClass": "LOCKED_IMMUTABLE",
        "writeOnce": True,
        "selectionLockVerified": True,
        "exactVectorVerified": not vector_errors,
        "exactVectorValidationErrors": vector_errors,
        "trainingEligible": bool(training.get("trainingEligible")),
        "trainingExclusionReasons": list(training.get("trainingExclusionReasons") or []),
        "immutableExisting": True,
    }


def _late_backfill_count(module: Any, stage: Optional[Dict[str, Any]], scoring: List[Dict[str, Any]], game: Dict[str, Any]) -> int:
    if not stage:
        return 0
    source_window = stage.get("source_window") or {}
    bound_entries = source_window.get("pulls") or []
    if not isinstance(bound_entries, list):
        return 0
    bound_keys = {
        _source_window_key(entry)
        for entry in bound_entries
        if isinstance(entry, dict)
    }
    current_keys = {
        _source_window_key(entry)
        for entry in _source_window_entries(module, scoring, game)
    }
    return len(current_keys - bound_keys)


def _progress(
    module: Any,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    now: datetime,
    *,
    ensure_canonical: bool,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    valid_stages: Dict[str, Dict[str, Any]] = {}
    canonical: Dict[str, Dict[str, Any]] = {}
    outcomes: Dict[str, Dict[str, Any]] = {}
    for game in manifest:
        identity = game_identity(game)
        start = _start(module, game)
        lock_at = _lock_at(module, game)
        errors: List[str] = []
        try:
            scoring = _scoring_pulls(module, pulls, game)
        except Exception as exc:
            scoring = []
            errors.append(f"scoring_pull_read_failed:{type(exc).__name__}:{exc}")
        try:
            stage = _get_stage(module, slate, game)
        except Exception as exc:
            stage = None
            errors.append(f"stage_read_failed:{type(exc).__name__}:{exc}")
        try:
            outcome = _get_lock_outcome(module, slate, game)
        except Exception as exc:
            outcome = None
            errors.append(f"lock_outcome_read_failed:{type(exc).__name__}:{exc}")
        if stage:
            try:
                errors.extend(
                    _validate_stage(module, stage, slate, game, manifest, scoring)
                )
            except Exception as exc:
                errors.append(f"stage_validation_failed:{type(exc).__name__}:{exc}")
        try:
            late_backfill_count = _late_backfill_count(module, stage, scoring, game)
        except Exception as exc:
            late_backfill_count = 0
            errors.append(f"late_backfill_check_failed:{type(exc).__name__}:{exc}")
        errors = sorted(set(errors))
        state = "LOCKED_STAGED" if stage and not errors else "PENDING"
        if errors:
            state = "INVALID_STAGE_BLOCKED"
        elif outcome and not stage:
            outcomes[identity] = outcome
            state = "LOCKED_NO_PREDICTION_DATA"
        elif not stage and start and now >= start:
            state = "MISSED_NOT_BACKFILLED"
        elif not stage and lock_at and now >= lock_at and now < (_cutoff_stable_at(module, game) or lock_at):
            state = "WAITING_FOR_CUTOFF_STABILIZATION"
        elif not stage and lock_at and now >= lock_at:
            state = "DUE_NOT_STAGED"
        if stage and not errors:
            valid_stages[identity] = stage
            try:
                stage_row = (stage.get("data") or {}).get("row") or {}
                stored = _canonical_store(module, stage_row) if ensure_canonical else _canonical_readback(module, stage_row)
                if not stored:
                    raise RuntimeError("canonical_immutable_game_row_missing_or_invalid")
                canonical[identity] = stored
                state = "LOCKED_CANONICAL"
            except Exception as exc:
                errors = [f"canonical_write_failed:{exc}"]
                state = "STAGED_CANONICAL_WRITE_BLOCKED"
        stage_row = copy.deepcopy((stage.get("data") or {}).get("row") or {}) if stage else {}
        locked_prediction = identity in canonical
        lock_outcome_recorded = bool(locked_prediction or outcome)
        base_playable = bool(
            stage_row.get("playable") is True
            or stage_row.get("playablePick") is True
            or stage_row.get("actionablePick") is True
        )
        lifecycle = (
            _resolved_playability_lifecycle(
                module,
                slate,
                game,
                stage_row,
                now,
                event_pending_required=_doubleheader_game_one(manifest, game) is not None,
            )
            if locked_prediction
            else {}
        )
        assessment = lifecycle.get("assessment")
        playable = (
            lifecycle.get("playable") is True
            if locked_prediction
            else base_playable
        )
        blocked = (
            lifecycle.get("blocked") is True
            if locked_prediction
            else bool(lock_outcome_recorded and not playable)
        )
        playability_reasons = (
            list(lifecycle.get("reasons") or [])
            if locked_prediction
            else list((outcome or {}).get("playability_block_reasons") or [])
        )
        training = stage_row.get("mlFeatureFreeze") or {}
        latest_scoring_game = (
            _matching_game(scoring[-1], game)
            if scoring
            else None
        ) or {}
        latest_scoring_identity = (
            _raw_game_identity(latest_scoring_game)
            if latest_scoring_game
            else ""
        )
        latest_provider_event_id = (
            latest_scoring_game.get("provider_event_id")
            or latest_scoring_game.get("providerEventId")
            or (
                latest_scoring_identity
                if latest_scoring_identity
                and latest_scoring_identity != _raw_game_identity(game)
                and not latest_scoring_identity.startswith("mlb_statsapi:")
                else None
            )
        )
        rows.append({
            "gameIdentity": identity,
            "gameId": game.get("game_id") or game.get("gameId") or game.get("id"),
            "officialGamePk": stage_row.get("officialGamePk") or game.get("official_game_pk"),
            "officialGameId": stage_row.get("officialGameId") or game.get("official_game_id"),
            "providerEventId": stage_row.get("providerEventId") or latest_provider_event_id or game.get("provider_event_id"),
            "providerCommenceTime": stage_row.get("providerCommenceTime") or latest_scoring_game.get("provider_commence_time") or game.get("provider_commence_time"),
            "providerStartDriftSeconds": (
                stage_row.get("providerStartDriftSeconds")
                if stage_row.get("providerStartDriftSeconds") is not None
                else latest_scoring_game.get("provider_start_drift_seconds")
                if latest_scoring_game.get("provider_start_drift_seconds") is not None
                else game.get("provider_start_drift_seconds")
            ),
            "canonicalStartTimeSource": stage_row.get("canonicalStartTimeSource") or game.get("canonical_start_time_source"),
            "commenceTime": (start.isoformat() if start else None),
            "scheduledLockAtUtc": (lock_at.isoformat() if lock_at else None),
            "state": state,
            "lockStatus": state,
            "lockOutcomeRecorded": lock_outcome_recorded,
            "locked": locked_prediction,
            "lockedPrediction": locked_prediction,
            "canonical": locked_prediction,
            "officialPrediction": locked_prediction,
            "predictedWinner": stage_row.get("predictedWinner") if locked_prediction else None,
            "predictedSide": stage_row.get("predictedSide") if locked_prediction else None,
            "selectionFingerprint": stage_row.get("lastPrelockSelectionFingerprint") if locked_prediction else None,
            "playable": playable,
            "playablePick": playable,
            "actionablePick": playable,
            "blocked": blocked,
            "releaseBlocked": blocked,
            "wagerReleaseBlocked": blocked,
            "playabilityStatus": lifecycle.get("status") or ("PLAYABLE" if playable else "BLOCKED" if lock_outcome_recorded else "PENDING"),
            "playabilityBlockReasons": playability_reasons,
            "releaseBlockReasons": playability_reasons,
            "playabilityAssessment": copy.deepcopy(assessment) if assessment else None,
            "playabilityAssessmentValidationErrors": list(lifecycle.get("validationErrors") or []),
            "historicalPlayabilityAssessmentValidationErrors": list(
                lifecycle.get("historicalValidationErrors") or []
            ),
            "requiredPlayabilityCheckpoint": lifecycle.get("requiredCheckpoint"),
            "requiredPlayabilityCheckpointDue": lifecycle.get("requiredCheckpointDue") is True,
            "eventPlayabilityAssessmentRequired": lifecycle.get("eventPendingRequired") is True,
            "trainingEligible": bool(training.get("trainingEligible")) if locked_prediction else False,
            "trainingExclusionReasons": list(training.get("trainingExclusionReasons") or (outcome or {}).get("training_exclusion_reasons") or []),
            "exactVectorVerified": stage_row.get("exactVectorVerified") if locked_prediction else None,
            "exactVectorValidationErrors": list(stage_row.get("exactVectorValidationErrors") or []),
            "readiness": _readiness_status(module, slate, game),
            "sourcePullAtUtc": stage.get("source_pull_at_utc") if stage else None,
            "actualStagedAtUtc": stage.get("staged_at_utc") if stage else None,
            "pullDepthAtCutoff": len(scoring),
            "sourceWindowStabilizationSeconds": CUTOFF_STABILIZATION_SECONDS,
            "sourceWindowStableAtUtc": (_cutoff_stable_at(module, game) or lock_at).isoformat() if lock_at else None,
            "lateBackfillDetected": late_backfill_count > 0,
            "lateBackfillPullCount": late_backfill_count,
            "errors": errors,
        })
    return {
        "games": rows,
        "stages": valid_stages,
        "canonical": canonical,
        "outcomes": outcomes,
        "stagedCount": len(valid_stages),
        "canonicalCount": len(canonical),
        "lockedPredictionCount": len(canonical),
        "lockOutcomeCount": len(canonical) + len(outcomes),
        "noPredictionDataCount": len(outcomes),
        "playableCount": len([row for row in rows if row.get("playable") is True]),
        "blockedCount": len([row for row in rows if row.get("blocked") is True]),
        "trainingEligibleCount": len([row for row in rows if row.get("trainingEligible") is True]),
        "playabilityValidationErrorCount": len([
            row
            for row in rows
            if row.get("playabilityAssessmentValidationErrors")
        ]),
        "playabilityLifecycleErrorCount": len([
            row
            for row in rows
            if row.get("playabilityAssessmentValidationErrors")
            or row.get("historicalPlayabilityAssessmentValidationErrors")
        ]),
        "pendingCount": len([row for row in rows if row["state"] in {"PENDING", "WAITING_FOR_CUTOFF_STABILIZATION"}]),
        "stabilizingCount": len([row for row in rows if row["state"] == "WAITING_FOR_CUTOFF_STABILIZATION"]),
        "dueMissingCount": len([row for row in rows if row["state"] in {"DUE_NOT_STAGED", "STAGED_CANONICAL_WRITE_BLOCKED", "INVALID_STAGE_BLOCKED"}]),
        "missedCount": len([row for row in rows if row["state"] == "MISSED_NOT_BACKFILLED"]),
    }


def _daily_item(
    module: Any,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    progress: Dict[str, Any],
) -> Dict[str, Any]:
    rows = [copy.deepcopy(((progress["stages"][game_identity(game)].get("data") or {}).get("row") or {})) for game in manifest]
    ranked = sorted(rows, key=lambda row: (float(row.get("score") or 0), float(row.get("winProbability") or 0)), reverse=True)
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
        coverage = dict(row.get("slateCoverage") or {})
        coverage.update({"lockCoverageComplete": True, "lockedGameCountAtWrite": len(manifest), "pendingGameCountAtWrite": 0, "operationalStatus": "COMPLETE_MANIFEST_ALL_LOCKED"})
        row["slateCoverage"] = coverage
    picks = module._sort_picks([module._compact_pick(row) for row in ranked])
    starts = [_start(module, game) for game in manifest]
    starts = [value for value in starts if value]
    cutoffs = [_lock_at(module, game) for game in manifest]
    cutoffs = [value for value in cutoffs if value]
    stages = list(progress["stages"].values())
    sources = [_parse_iso(item.get("source_pull_at_utc")) for item in stages]
    sources = [value for value in sources if value]
    now = module._now_utc().astimezone(timezone.utc)
    coverage = _manifest_coverage(manifest, len(manifest))
    return module.history.ddb_safe({
        "PK": module._lock_pk(slate),
        "SK": module._lock_sk(),
        "record_type": "mlb_daily_locked_individual_game_moneyline_picks",
        "sport": "mlb",
        "slate_date": slate,
        "model_version": VERSION,
        "single_game_model": module.mlb_game_winner_engine.MODEL_VERSION,
        "locked": True,
        "locked_at": now.isoformat(),
        "locked_at_et": now.astimezone(module.EASTERN).isoformat(),
        "first_game_start_et": min(starts).astimezone(module.EASTERN).isoformat(),
        "first_game_start_utc": min(starts).isoformat(),
        "lock_time_et": min(cutoffs).astimezone(module.EASTERN).isoformat(),
        "last_game_lock_time_et": max(cutoffs).astimezone(module.EASTERN).isoformat(),
        "lock_minutes_before_first_game": module.LOCK_MINUTES,
        "lock_minutes_before_each_game": module.LOCK_MINUTES,
        "lock_policy": LOCK_POLICY,
        "per_game_lock": True,
        "slate_wide_lock": False,
        "source": "canonical_write_once_game_rows_from_each_game_own_tminus45",
        "latest_pull_at": max(sources).isoformat(),
        "latest_pull_id": max(stages, key=lambda item: str(item.get("source_pull_at_utc") or "")).get("source_pull_id"),
        "pull_count": len(pulls),
        "game_count": len(manifest),
        "manifest_version": VERSION,
        "manifest_game_count": len(manifest),
        "prediction_count": len(picks),
        "promoted_count": len([pick for pick in picks if pick.get("promoted")]),
        "all_games_predicted": True,
        "coverage_complete": True,
        "coverage_status": "COMPLETE",
        "doubleheader_safe_identity": True,
        "canonical_immutable_game_row_count": progress.get("canonicalCount"),
        "data": {
            "picks": picks,
            "manifestGameIdentities": [game_identity(game) for game in manifest],
            "slateCoverage": coverage,
            "perGameLockProof": [
                {
                    "gameIdentity": item.get("game_identity"),
                    "gameId": item.get("game_id"),
                    "commenceTime": item.get("commence_time"),
                    "scheduledLockAtUtc": item.get("scheduled_lock_at_utc"),
                    "actualStagedAtUtc": item.get("staged_at_utc"),
                    "sourcePullAtUtc": item.get("source_pull_at_utc"),
                    "sourcePullId": item.get("source_pull_id"),
                    "sourceWindow": item.get("source_window") or {},
                    "stageFingerprint": item.get("stage_fingerprint"),
                    "writeOnce": True,
                    "canonicalImmutableGameRow": True,
                }
                for item in stages
            ],
            "predictionSummary": {
                "allGamesPredicted": True,
                "perGameLock": True,
                "canonicalImmutableGameRowCount": progress.get("canonicalCount"),
            },
        },
        "created_at": now.isoformat(),
    })


def _daily_authority_errors(
    module: Any,
    slate: str,
    item: Any,
    manifest: List[Dict[str, Any]],
    progress: Dict[str, Any],
) -> List[str]:
    """Prove that a DAILY_LOCK row is only an index over valid per-game locks.

    The daily key predates the per-game authority model, so key presence alone is
    not authority.  A current row is accepted only when its version/policy fields
    are exact and every aggregate proof resolves to a currently valid immutable
    stage plus canonical game row.
    """
    if not isinstance(item, dict):
        return ["daily_lock_item_missing_or_not_object"]

    errors: List[str] = []
    expected_identities = [game_identity(game) for game in manifest]
    expected_identity_set = set(expected_identities)
    expected_count = len(expected_identities)
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    picks = data.get("picks") if isinstance(data.get("picks"), list) else []
    proofs = data.get("perGameLockProof") if isinstance(data.get("perGameLockProof"), list) else []
    coverage = data.get("slateCoverage") if isinstance(data.get("slateCoverage"), dict) else {}
    summary = data.get("predictionSummary") if isinstance(data.get("predictionSummary"), dict) else {}
    stages = progress.get("stages") if isinstance(progress.get("stages"), dict) else {}
    canonical = progress.get("canonical") if isinstance(progress.get("canonical"), dict) else {}

    def exact_count(value: Any, expected: int) -> bool:
        try:
            return int(value) == expected
        except (TypeError, ValueError):
            return False

    if item.get("PK") != module._lock_pk(slate) or item.get("SK") != module._lock_sk():
        errors.append("daily_lock_key_mismatch")
    if item.get("record_type") != "mlb_daily_locked_individual_game_moneyline_picks":
        errors.append("daily_lock_record_type_not_per_game_authority")
    if str(item.get("sport") or "").lower() != "mlb" or str(item.get("slate_date") or "") != slate:
        errors.append("daily_lock_sport_or_slate_mismatch")
    if item.get("model_version") != VERSION or item.get("manifest_version") != VERSION:
        errors.append("daily_lock_model_or_manifest_version_mismatch")
    if item.get("single_game_model") != module.mlb_game_winner_engine.MODEL_VERSION:
        errors.append("daily_lock_single_game_model_version_mismatch")
    if (
        item.get("locked") is not True
        or item.get("per_game_lock") is not True
        or item.get("slate_wide_lock") is not False
    ):
        errors.append("daily_lock_not_explicit_per_game_authority")
    if (
        item.get("lock_policy") != LOCK_POLICY
        or not exact_count(item.get("lock_minutes_before_each_game"), module.LOCK_MINUTES)
    ):
        errors.append("daily_lock_policy_mismatch")
    if item.get("source") != "canonical_write_once_game_rows_from_each_game_own_tminus45":
        errors.append("daily_lock_source_authority_mismatch")
    if (
        item.get("all_games_predicted") is not True
        or item.get("coverage_complete") is not True
        or item.get("coverage_status") != "COMPLETE"
        or item.get("doubleheader_safe_identity") is not True
    ):
        errors.append("daily_lock_coverage_claim_incomplete")

    for field in (
        "game_count",
        "manifest_game_count",
        "prediction_count",
        "canonical_immutable_game_row_count",
    ):
        if not exact_count(item.get(field), expected_count):
            errors.append(f"daily_lock_{field}_mismatch")
    if expected_count == 0:
        errors.append("daily_lock_manifest_empty")
    if not exact_count(progress.get("stagedCount"), expected_count):
        errors.append("daily_lock_valid_stage_count_mismatch")
    if not exact_count(progress.get("canonicalCount"), expected_count):
        errors.append("daily_lock_canonical_readback_count_mismatch")
    if set(stages) != expected_identity_set:
        errors.append("daily_lock_stage_identity_coverage_mismatch")
    if set(canonical) != expected_identity_set:
        errors.append("daily_lock_canonical_identity_coverage_mismatch")

    manifest_proof = data.get("manifestGameIdentities")
    if not isinstance(manifest_proof, list) or manifest_proof != expected_identities:
        errors.append("daily_lock_manifest_identity_proof_mismatch")
    pick_identities = [game_identity(pick) for pick in picks if isinstance(pick, dict)]
    if len(picks) != expected_count or len(set(pick_identities)) != expected_count or set(pick_identities) != expected_identity_set:
        errors.append("daily_lock_pick_identity_coverage_mismatch")

    if len(proofs) != expected_count:
        errors.append("daily_lock_per_game_proof_count_mismatch")
    proof_identities = [
        str(proof.get("gameIdentity") or "")
        for proof in proofs
        if isinstance(proof, dict)
    ]
    if len(set(proof_identities)) != expected_count or set(proof_identities) != expected_identity_set:
        errors.append("daily_lock_per_game_proof_identity_mismatch")
    for proof in proofs:
        if not isinstance(proof, dict):
            errors.append("daily_lock_per_game_proof_not_object")
            continue
        identity = str(proof.get("gameIdentity") or "")
        stage = stages.get(identity)
        if not isinstance(stage, dict):
            errors.append(f"daily_lock_stage_missing_for_proof:{identity}")
            continue
        if (
            stage.get("model_version") != VERSION
            or stage.get("lock_policy") != LOCK_POLICY
            or stage.get("promotion_policy_version") != PROMOTION_POLICY_VERSION
        ):
            errors.append(f"daily_lock_stage_authority_version_mismatch:{identity}")
        if stage.get("stage_fingerprint") != _stage_fingerprint(stage):
            errors.append(f"daily_lock_stage_fingerprint_invalid:{identity}")
        expected_proof = {
            "gameIdentity": stage.get("game_identity"),
            "gameId": stage.get("game_id"),
            "commenceTime": stage.get("commence_time"),
            "scheduledLockAtUtc": stage.get("scheduled_lock_at_utc"),
            "actualStagedAtUtc": stage.get("staged_at_utc"),
            "sourcePullAtUtc": stage.get("source_pull_at_utc"),
            "sourcePullId": stage.get("source_pull_id"),
            "sourceWindow": stage.get("source_window") or {},
            "stageFingerprint": stage.get("stage_fingerprint"),
            "writeOnce": True,
            "canonicalImmutableGameRow": True,
        }
        if _plain(proof) != _plain(expected_proof):
            errors.append(f"daily_lock_per_game_proof_payload_mismatch:{identity}")

    if (
        coverage.get("version") != VERSION
        or coverage.get("lockCoverageComplete") is not True
        or coverage.get("manifestGameIdentities") != expected_identities
        or not exact_count(coverage.get("manifestGameCount"), expected_count)
        or not exact_count(coverage.get("lockedGameCountAtWrite"), expected_count)
        or not exact_count(coverage.get("pendingGameCountAtWrite"), 0)
        or coverage.get("operationalStatus") != "COMPLETE_MANIFEST_ALL_LOCKED"
    ):
        errors.append("daily_lock_slate_coverage_proof_mismatch")
    if (
        summary.get("allGamesPredicted") is not True
        or summary.get("perGameLock") is not True
        or not exact_count(summary.get("canonicalImmutableGameRowCount"), expected_count)
    ):
        errors.append("daily_lock_prediction_summary_proof_mismatch")
    return sorted(set(errors))


def apply(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_DAILY_PER_GAME_LOCK_V1", False):
        return module

    original_lock_response = module._lock_response

    def lock_response(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        response = original_lock_response(item)
        if not response or not item:
            return response
        data = item.get("data") or {}
        response.update({
            "lockPolicy": item.get("lock_policy") or LOCK_POLICY,
            "perGameLock": item.get("per_game_lock") is True,
            "slateWideLock": item.get("slate_wide_lock") is True,
            "lockMinutesBeforeEachGame": item.get("lock_minutes_before_each_game"),
            "lastGameLockTimeEt": item.get("last_game_lock_time_et"),
            "canonicalImmutableGameRowCount": item.get("canonical_immutable_game_row_count"),
            "perGameLockProof": data.get("perGameLockProof") or [],
            "writeOnce": True,
        })
        return response

    module._lock_response = lock_response

    def status_payload(slate_date: Optional[str] = None) -> Dict[str, Any]:
        slate = slate_date or module._today_et()
        base = {
            "ok": True,
            "sport": "mlb",
            "modelVersion": VERSION,
            "lockAttemptDiagnosticsVersion": ATTEMPT_DIAGNOSTICS_VERSION,
            "readinessVersion": READINESS_VERSION,
            "lockOutcomeVersion": LOCK_OUTCOME_VERSION,
            "playabilityAssessmentVersion": RELEASE_ASSESSMENT_VERSION,
            "singleGameModelVersion": module.mlb_game_winner_engine.MODEL_VERSION,
            "slateDateEt": slate,
            "lockPolicy": LOCK_POLICY,
            "perGameLock": True,
            "slateWideLock": False,
            "lockMinutesBeforeEachGame": module.LOCK_MINUTES,
            "minPullsPerGameForLock": module.MIN_PULLS_PER_GAME_FOR_LOCK,
            "maxSourceAgeAtGameLockMinutes": module.MAX_LATEST_PULL_AGE_MINUTES,
            "readinessCheckpointsMinutesBeforeGame": list(READINESS_CHECKPOINT_MINUTES),
            "playabilityCheckpointsMinutesBeforeGame": list(RELEASE_CHECKPOINT_MINUTES),
            "lockStateDimensions": {
                "lockedPrediction": "immutable winner exists",
                "lockOutcomeRecorded": "terminal per-game cutoff status exists",
                "playable": "wagering release is allowed",
                "blocked": "wagering release is blocked without erasing the winner",
                "trainingEligible": "immutable provenance and feature vector qualify for training",
            },
        }
        if module.TABLE is None:
            return {**base, "ok": False, "error": "SNAPSHOTS_TABLE not configured", "locked": False, "lock": None}
        raw_existing = module._get_lock_item(slate)
        pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
        manifest = module._latest_games_for_date(slate, pulls)
        roster_observability: Dict[str, Any] = {
            "verifiedFullSlateManifestVersion": None,
            "rosterAuthorityMode": "INJECTED_ADAPTER_OR_LEGACY",
            "officialScheduleBacked": False,
            "officialScheduleAuthorityVersion": None,
            "officialScheduleAuthoritySource": None,
            "officialScheduleAuthorityFingerprint": None,
            "officialScheduleGameCount": None,
            "officialScheduleAuthoritativeStartTimes": False,
            "officialScheduleMissingProviderEventGameIds": [],
            "eventRosterBacked": False,
            "legacyRosterMigrationFallback": True,
            "latestProviderFeedGameCount": len(manifest),
            "latestProviderFeedAnomalyCount": 0,
            "latestProviderFeedAnomalies": [],
        }
        resolver = getattr(module.history, "verified_full_slate_manifest", None)
        if callable(resolver) and pulls:
            try:
                resolved_roster = resolver(pulls, slate)
                roster_observability.update({
                    "verifiedFullSlateManifestVersion": resolved_roster.get("version"),
                    "rosterAuthorityMode": resolved_roster.get("rosterAuthorityMode"),
                    "officialScheduleBacked": resolved_roster.get("officialScheduleBacked") is True,
                    "officialScheduleAuthorityVersion": resolved_roster.get("officialScheduleAuthorityVersion"),
                    "officialScheduleAuthoritySource": resolved_roster.get("officialScheduleAuthoritySource"),
                    "officialScheduleAuthorityFingerprint": resolved_roster.get("officialScheduleAuthorityFingerprint"),
                    "officialScheduleGameCount": resolved_roster.get("officialScheduleGameCount"),
                    "officialScheduleAuthoritativeStartTimes": resolved_roster.get("officialScheduleAuthoritativeStartTimes") is True,
                    "officialScheduleMissingProviderEventGameIds": list(resolved_roster.get("officialScheduleMissingProviderEventGameIds") or []),
                    "eventRosterBacked": resolved_roster.get("eventRosterBacked") is True,
                    "legacyRosterMigrationFallback": resolved_roster.get("legacyMigrationFallback") is True,
                    "latestProviderFeedGameCount": resolved_roster.get("latestFeedGameCount"),
                    "latestProviderFeedAnomalyCount": int(resolved_roster.get("latestFeedAnomalyCount") or 0),
                    "latestProviderFeedAnomalies": copy.deepcopy(resolved_roster.get("latestFeedAnomalies") or []),
                })
            except Exception as exc:
                roster_observability["rosterAuthorityReadError"] = f"{type(exc).__name__}:{exc}"
        now = module._now_utc().astimezone(timezone.utc)
        progress = _progress(module, slate, pulls, manifest, now, ensure_canonical=False) if manifest else {"games": [], "stagedCount": 0, "canonicalCount": 0, "lockedPredictionCount": 0, "lockOutcomeCount": 0, "noPredictionDataCount": 0, "playableCount": 0, "blockedCount": 0, "trainingEligibleCount": 0, "playabilityValidationErrorCount": 0, "playabilityLifecycleErrorCount": 0, "pendingCount": 0, "stabilizingCount": 0, "dueMissingCount": 0, "missedCount": 0}
        daily_authority_errors = (
            _daily_authority_errors(module, slate, raw_existing, manifest, progress)
            if raw_existing
            else []
        )
        existing = module._lock_response(raw_existing) if raw_existing and not daily_authority_errors else None
        manifest_by_identity = {game_identity(game): game for game in manifest}
        per_game_status: List[Dict[str, Any]] = []
        for raw_status in progress.get("games") or []:
            status = copy.deepcopy(raw_status)
            game = manifest_by_identity.get(str(status.get("gameIdentity") or ""))
            status["attemptDiagnostics"] = (
                _diagnostic_history(module, slate, game)
                if game is not None
                else {
                    "ok": False,
                    "version": ATTEMPT_DIAGNOSTICS_VERSION,
                    "error": "diagnostic_game_identity_not_in_manifest",
                }
            )
            per_game_status.append(status)
        cutoffs = sorted([value for value in (_lock_at(module, game) for game in manifest) if value])
        future = [value for value in cutoffs if value > now]
        game_count = len(manifest)
        outcome_count = int(progress.get("lockOutcomeCount") or 0)
        locked_prediction_count = int(progress.get("lockedPredictionCount") or 0)
        daily_complete = bool(game_count and outcome_count == game_count)
        canonical_prediction_complete = bool(
            game_count and locked_prediction_count == game_count
        )
        playability_lifecycle_error_count = int(
            progress.get("playabilityLifecycleErrorCount") or 0
        )
        operational_defect = bool(
            roster_observability.get("rosterAuthorityReadError")
            or daily_authority_errors
            or progress.get("dueMissingCount")
            or progress.get("missedCount")
            or playability_lifecycle_error_count
            or len(per_game_status) != game_count
        )
        slate_status = (
            "COMPLETE" if daily_complete and canonical_prediction_complete
            else "COMPLETE_WITH_NO_PREDICTION_DATA" if daily_complete
            else "MISSED" if progress.get("missedCount")
            else "PARTIAL" if outcome_count
            else "PRELOCK"
        )
        return {
            **base,
            **roster_observability,
            "locked": bool(existing),
            "dailyCardComplete": daily_complete,
            "lockStatusComplete": daily_complete,
            "canonicalPredictionComplete": canonical_prediction_complete,
            "allGamesPredicted": canonical_prediction_complete,
            "lockedAny": locked_prediction_count > 0,
            "partiallyLocked": bool(0 < locked_prediction_count < game_count),
            "slateLockStatus": slate_status,
            "lock": existing,
            "pullCount": len(pulls),
            "gameCount": game_count,
            "manifestGameCount": game_count,
            "latestPullAt": pulls[-1].get("pulled_at") if pulls else None,
            "stagedGameCount": progress.get("stagedCount"),
            "canonicalImmutableGameRowCount": progress.get("canonicalCount"),
            "lockedPredictionCount": locked_prediction_count,
            "lockedStatusCount": outcome_count,
            "lockOutcomeCount": outcome_count,
            "noPredictionDataCount": progress.get("noPredictionDataCount"),
            "playablePredictionCount": progress.get("playableCount"),
            "blockedPredictionCount": progress.get("blockedCount"),
            "trainingEligibleCount": progress.get("trainingEligibleCount"),
            "playabilityValidationErrorCount": progress.get("playabilityValidationErrorCount"),
            "playabilityLifecycleErrorCount": playability_lifecycle_error_count,
            "lockOutcomeCoveragePct": round(outcome_count / game_count * 100.0, 2) if game_count else 0.0,
            "officialPredictionCoveragePct": round(locked_prediction_count / game_count * 100.0, 2) if game_count else 0.0,
            "pendingGameCount": progress.get("pendingCount"),
            "stabilizingGameCount": progress.get("stabilizingCount"),
            "dueMissingGameCount": progress.get("dueMissingCount"),
            "missedGameCount": progress.get("missedCount"),
            "perGameStatus": per_game_status,
            "firstGameStartEt": min([value for value in (_start(module, game) for game in manifest) if value]).astimezone(module.EASTERN).isoformat() if any(_start(module, game) for game in manifest) else None,
            "lockTimeEt": min(cutoffs).astimezone(module.EASTERN).isoformat() if cutoffs else None,
            "lastGameLockTimeEt": max(cutoffs).astimezone(module.EASTERN).isoformat() if cutoffs else None,
            "nextGameLockAtUtc": future[0].isoformat() if future else None,
            "nowEt": now.astimezone(module.EASTERN).isoformat(),
            "lockDue": bool(not existing and (progress.get("dueMissingCount") or progress.get("missedCount"))),
            "predictionDataUnavailable": bool(progress.get("noPredictionDataCount")),
            "operationalDefect": operational_defect,
            "minutesUntilLock": round((future[0] - now).total_seconds() / 60.0, 2) if future else 0,
            "invalidExistingDailyLock": bool(raw_existing and daily_authority_errors),
            "dailyLockAuthorityErrors": daily_authority_errors,
        }

    def run_lock(slate_date: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        slate = slate_date or module._today_et()
        if module.TABLE is None:
            return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "error": "SNAPSHOTS_TABLE not configured"}
        raw_existing = module._get_lock_item(slate)
        pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
        if not pulls:
            if raw_existing:
                authority_errors = _daily_authority_errors(module, slate, raw_existing, [], {})
                return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "EXISTING_DAILY_LOCK_NOT_PER_GAME_AUTHORITY", "failClosed": True, "dailyLockAuthorityErrors": authority_errors}
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "NO_STORED_ODDS_API_PULL_HISTORY"}
        manifest = module._latest_games_for_date(slate, pulls)
        if not manifest:
            if raw_existing:
                authority_errors = _daily_authority_errors(module, slate, raw_existing, [], {})
                return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "EXISTING_DAILY_LOCK_NOT_PER_GAME_AUTHORITY", "failClosed": True, "dailyLockAuthorityErrors": authority_errors, "pullCount": len(pulls)}
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "NO_MLB_GAMES_FOR_SLATE_DATE", "pullCount": len(pulls)}

        now = module._now_utc().astimezone(timezone.utc)
        lifecycle_diagnostic_errors: List[Dict[str, Any]] = []

        def record_lifecycle_diagnostics(
            progress: Dict[str, Any],
            evaluated_at: datetime,
        ) -> None:
            """Best-effort diagnostics that can never interrupt lock writes."""
            try:
                lifecycle_diagnostic_errors.extend(
                    _ensure_readiness_checkpoints(
                        module,
                        slate,
                        pulls,
                        manifest,
                        evaluated_at,
                    )
                )
            except Exception as exc:
                lifecycle_diagnostic_errors.append({
                    "checkpoint": "READINESS_ALL_GAMES",
                    "error": f"{type(exc).__name__}:{exc}",
                })
            try:
                lifecycle_diagnostic_errors.extend(
                    _ensure_playability_assessments(
                        module,
                        slate,
                        manifest,
                        progress.get("stages") or {},
                        evaluated_at,
                    )
                )
            except Exception as exc:
                lifecycle_diagnostic_errors.append({
                    "checkpoint": "PLAYABILITY_ALL_GAMES",
                    "error": f"{type(exc).__name__}:{exc}",
                })

        # Read current authority before any write. Readiness and release
        # diagnostics run only after the immutable lock path has had its chance.
        observed = _progress(module, slate, pulls, manifest, now, ensure_canonical=False)
        if raw_existing:
            authority_errors = _daily_authority_errors(
                module,
                slate,
                raw_existing,
                manifest,
                observed,
            )
            if authority_errors:
                return {
                    "ok": False,
                    "sport": "mlb",
                    "modelVersion": VERSION,
                    "slateDateEt": slate,
                    "locked": False,
                    "reason": "EXISTING_DAILY_LOCK_NOT_PER_GAME_AUTHORITY",
                    "failClosed": True,
                    "dailyLockAuthorityErrors": authority_errors,
                    "perGameLockProgress": observed,
                }
            record_lifecycle_diagnostics(observed, now)
            try:
                observed = _progress(
                    module,
                    slate,
                    pulls,
                    manifest,
                    now,
                    ensure_canonical=False,
                )
            except Exception as exc:
                lifecycle_diagnostic_errors.append({
                    "checkpoint": "POST_DIAGNOSTIC_PROGRESS_READ",
                    "error": f"{type(exc).__name__}:{exc}",
                })
            existing = module._lock_response(raw_existing)
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": True, "lock": existing, "perGameLockProgress": observed, "lifecycleDiagnosticErrors": lifecycle_diagnostic_errors}
        try:
            attempts = _begin_attempt_diagnostics(
                module,
                slate,
                pulls,
                manifest,
                observed.get("games") or [],
                now,
                force,
            )
        except Exception as exc:
            attempts = []
            lifecycle_diagnostic_errors.append({
                "checkpoint": "LOCK_ATTEMPT_DIAGNOSTIC_START",
                "error": f"{type(exc).__name__}:{exc}",
            })
        failures: List[Dict[str, Any]] = []
        terminal_outcomes: List[Dict[str, Any]] = []
        latest_progress = observed
        manifest_authority_cache: Dict[Tuple[Any, ...], Dict[str, Any]] = {}

        def manifest_authority_for(
            current_pulls: List[Dict[str, Any]],
            current_manifest: List[Dict[str, Any]],
        ) -> Dict[str, Any]:
            cache_key: Tuple[Any, ...] = (
                _pull_history_identity(module, current_pulls),
                tuple(game_identity(entry) for entry in current_manifest),
            )
            if cache_key not in manifest_authority_cache:
                manifest_authority_cache[cache_key] = _select_provider_manifest_authority(
                    module,
                    current_pulls,
                    slate,
                    current_manifest,
                )
            return copy.deepcopy(manifest_authority_cache[cache_key])

        def respond(payload: Dict[str, Any], progress: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            out = dict(payload)
            try:
                diagnostics = _finish_attempt_diagnostics(
                    module,
                    attempts,
                    progress or latest_progress,
                    failures,
                )
            except Exception as exc:
                diagnostics = {
                    "version": ATTEMPT_DIAGNOSTICS_VERSION,
                    "appendOnly": True,
                    "writeOnce": True,
                    "attemptedGameCount": len(attempts),
                    "attempts": [],
                    "error": f"{type(exc).__name__}:{exc}",
                }
                lifecycle_diagnostic_errors.append({
                    "checkpoint": "LOCK_ATTEMPT_DIAGNOSTIC_FINISH",
                    "error": diagnostics["error"],
                })
            out["perGameLockAttemptDiagnostics"] = diagnostics
            out["terminalLockOutcomes"] = copy.deepcopy(terminal_outcomes)
            out["lifecycleDiagnosticErrors"] = copy.deepcopy(lifecycle_diagnostic_errors)
            return out

        try:
            pre = _progress(module, slate, pulls, manifest, now, ensure_canonical=True)
            latest_progress = pre
            for game_status in pre.get("games") or []:
                if game_status.get("state") != "DUE_NOT_STAGED":
                    continue
                identity = game_status["gameIdentity"]
                try:
                    game = next(entry for entry in manifest if game_identity(entry) == identity)
                    scoring = _scoring_pulls(module, pulls, game)
                except Exception as exc:
                    failures.append({
                        "gameIdentity": identity,
                        "reason": "PER_GAME_LOCK_PREPARATION_FAILED",
                        "errors": [f"{type(exc).__name__}:{exc}"],
                    })
                    continue
                stable_item: Optional[Dict[str, Any]] = None
                failed = False
                for _attempt in range(3):
                    try:
                        manifest_authority = manifest_authority_for(
                            pulls,
                            manifest,
                        )
                    except Exception as exc:
                        failures.append({
                            "gameIdentity": identity,
                            "reason": "PROVIDER_MANIFEST_AUTHORITY_NOT_STAGED",
                            "errors": [str(exc)],
                        })
                        failed = True
                        break

                    try:
                        item, errors = _generate_stage(
                            module,
                            slate,
                            game,
                            manifest,
                            scoring,
                            pre.get("stagedCount", 0) + 1,
                            manifest_authority,
                        )
                    except Exception as exc:
                        failures.append({
                            "gameIdentity": identity,
                            "reason": "PER_GAME_STAGE_GENERATION_FAILED",
                            "errors": [f"{type(exc).__name__}:{exc}"],
                        })
                        failed = True
                        break
                    if errors or not item:
                        if _is_no_prediction_candidate_failure(errors):
                            try:
                                outcome = _put_no_prediction_outcome(
                                    module,
                                    slate,
                                    game,
                                    now,
                                    errors or ["PERSISTED_PRELOCK_PREDICTION_MISSING"],
                                    manifest_authority,
                                )
                                terminal_outcomes.append({
                                    "gameIdentity": identity,
                                    "lockStatus": outcome.get("lock_status"),
                                    "reasons": outcome.get("reasons") or [],
                                })
                            except Exception as exc:
                                failures.append({
                                    "gameIdentity": identity,
                                    "reason": "TERMINAL_LOCK_OUTCOME_WRITE_FAILED",
                                    "errors": [f"{type(exc).__name__}:{exc}"],
                                })
                        else:
                            failures.append({"gameIdentity": identity, "reason": "PER_GAME_STAGE_VALIDATION_FAILED", "errors": errors})
                        failed = True
                        break

                    # Close the source window against a consistent re-read immediately
                    # before the immutable stage write. If an in-flight pull appeared,
                    # regenerate from that newer at-or-before-cutoff window.
                    try:
                        refreshed_pulls = sorted(
                            module._pulls_for_date(slate),
                            key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc),
                        )
                    except Exception as exc:
                        failures.append({
                            "gameIdentity": identity,
                            "reason": "SOURCE_WINDOW_REFRESH_FAILED",
                            "errors": [f"{type(exc).__name__}:{exc}"],
                        })
                        failed = True
                        break
                    pull_history_unchanged = (
                        _pull_history_identity(module, refreshed_pulls)
                        == _pull_history_identity(module, pulls)
                    )
                    try:
                        refreshed_manifest = (
                            manifest
                            if pull_history_unchanged
                            else module._latest_games_for_date(slate, refreshed_pulls)
                        )
                    except Exception as exc:
                        failures.append({
                            "gameIdentity": identity,
                            "reason": "SOURCE_WINDOW_MANIFEST_REFRESH_FAILED",
                            "errors": [f"{type(exc).__name__}:{exc}"],
                        })
                        failed = True
                        break
                    if [game_identity(entry) for entry in refreshed_manifest] != [game_identity(entry) for entry in manifest]:
                        failures.append({"gameIdentity": identity, "reason": "MANIFEST_CHANGED_DURING_SOURCE_WINDOW_CLOSE_NOT_STAGED"})
                        failed = True
                        break
                    refreshed_game = next(
                        (entry for entry in refreshed_manifest if game_identity(entry) == identity),
                        None,
                    )
                    if refreshed_game is None:
                        failures.append({"gameIdentity": identity, "reason": "GAME_MISSING_DURING_SOURCE_WINDOW_CLOSE_NOT_STAGED"})
                        failed = True
                        break
                    try:
                        refreshed_scoring = _scoring_pulls(module, refreshed_pulls, refreshed_game)
                    except Exception as exc:
                        failures.append({
                            "gameIdentity": identity,
                            "reason": "SOURCE_WINDOW_SCORING_REFRESH_FAILED",
                            "errors": [f"{type(exc).__name__}:{exc}"],
                        })
                        failed = True
                        break
                    try:
                        refreshed_manifest_authority = manifest_authority_for(
                            refreshed_pulls,
                            refreshed_manifest,
                        )
                    except Exception as exc:
                        failures.append({
                            "gameIdentity": identity,
                            "reason": "PROVIDER_MANIFEST_AUTHORITY_CHANGED_OR_INVALID_DURING_SOURCE_WINDOW_CLOSE_NOT_STAGED",
                            "errors": [str(exc)],
                        })
                        failed = True
                        break
                    pulls = refreshed_pulls
                    manifest = refreshed_manifest
                    game = refreshed_game
                    try:
                        source_window_unchanged = (
                            _source_window_entries(module, scoring, game)
                            == _source_window_entries(module, refreshed_scoring, game)
                        )
                        authority_unchanged = (
                            _manifest_authority_identity(item.get("provider_manifest_authority") or {})
                            == _manifest_authority_identity(refreshed_manifest_authority)
                        )
                    except Exception as exc:
                        failures.append({
                            "gameIdentity": identity,
                            "reason": "SOURCE_WINDOW_COMPARISON_FAILED",
                            "errors": [f"{type(exc).__name__}:{exc}"],
                        })
                        failed = True
                        break
                    if source_window_unchanged and authority_unchanged:
                        scoring = refreshed_scoring
                        stable_item = item
                        break
                    scoring = refreshed_scoring

                if failed:
                    continue
                if stable_item is None:
                    failures.append({"gameIdentity": identity, "reason": "SOURCE_WINDOW_CHANGED_REPEATEDLY_NOT_STAGED"})
                    continue

                try:
                    stored = _put_stage(module, stable_item, slate, game)
                    stored_errors = _validate_stage(module, stored, slate, game, manifest, scoring)
                except Exception as exc:
                    failures.append({
                        "gameIdentity": identity,
                        "reason": "PER_GAME_STAGE_WRITE_OR_READBACK_FAILED",
                        "errors": [f"{type(exc).__name__}:{exc}"],
                    })
                    continue
                if stored_errors:
                    failures.append({"gameIdentity": identity, "reason": "PER_GAME_STAGE_READBACK_INVALID", "errors": stored_errors})
                    continue
                try:
                    _canonical_store(module, (stored.get("data") or {}).get("row") or {})
                except Exception as exc:
                    failures.append({"gameIdentity": identity, "reason": "CANONICAL_IMMUTABLE_GAME_WRITE_FAILED", "errors": [str(exc)]})

            final_pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
            final_history_unchanged = (
                _pull_history_identity(module, final_pulls)
                == _pull_history_identity(module, pulls)
            )
            pulls = final_pulls
            if not final_history_unchanged:
                manifest = module._latest_games_for_date(slate, pulls)
            final_now = module._now_utc().astimezone(timezone.utc)
            progress = _progress(module, slate, pulls, manifest, final_now, ensure_canonical=True)
            record_lifecycle_diagnostics(progress, final_now)
            try:
                progress = _progress(
                    module,
                    slate,
                    pulls,
                    manifest,
                    final_now,
                    ensure_canonical=False,
                )
            except Exception as exc:
                lifecycle_diagnostic_errors.append({
                    "checkpoint": "POST_DIAGNOSTIC_PROGRESS_READ",
                    "error": f"{type(exc).__name__}:{exc}",
                })
            latest_progress = progress
            if progress.get("missedCount"):
                return respond({"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "MISSED_PER_GAME_LOCK_NOT_BACKFILLED", "failClosed": True, "forceIgnoredForSafety": bool(force), "failures": failures, "perGameLockProgress": progress}, progress)
            if failures or progress.get("dueMissingCount"):
                return respond({"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL", "failClosed": True, "forceIgnoredForSafety": bool(force), "failures": failures, "perGameLockProgress": progress}, progress)
            if progress.get("lockOutcomeCount") == len(manifest) and progress.get("canonicalCount") < len(manifest):
                return respond({
                    "ok": True,
                    "sport": "mlb",
                    "modelVersion": VERSION,
                    "slateDateEt": slate,
                    "locked": False,
                    "dailyCardComplete": True,
                    "lockStatusComplete": True,
                    "reason": "ALL_GAME_LOCK_OUTCOMES_RECORDED_WITH_NO_PREDICTION_DATA",
                    "forceIgnoredForSafety": bool(force),
                    "perGameLockProgress": progress,
                }, progress)
            if progress.get("stagedCount") < len(manifest) or progress.get("canonicalCount") < len(manifest):
                return respond({"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "PER_GAME_LOCKS_STAGED_WAITING_FOR_REMAINDER", "forceIgnoredForSafety": bool(force), "perGameLockProgress": progress}, progress)

            item = _daily_item(module, slate, pulls, manifest, progress)
            try:
                module.TABLE.put_item(Item=item, ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
                locked = module._lock_response(item)
                return respond({"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": False, "lock": locked, "perGameLockProgress": progress}, progress)
            except Exception as exc:
                if _conditional_collision(exc):
                    collision_item = module._get_lock_item(slate)
                    authority_errors = _daily_authority_errors(
                        module,
                        slate,
                        collision_item,
                        manifest,
                        progress,
                    )
                    existing = module._lock_response(collision_item) if collision_item and not authority_errors else None
                    if existing:
                        return respond({"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": True, "lock": existing, "perGameLockProgress": progress}, progress)
                    failures.append({
                        "reason": "DAILY_LOCK_CONDITIONAL_COLLISION_NOT_PER_GAME_AUTHORITY",
                        "errors": authority_errors,
                    })
                    return respond({"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "EXISTING_DAILY_LOCK_NOT_PER_GAME_AUTHORITY", "failClosed": True, "dailyLockAuthorityErrors": authority_errors, "perGameLockProgress": progress}, progress)
                raise
        except Exception as exc:
            try:
                _finish_attempt_diagnostics(
                    module,
                    attempts,
                    latest_progress,
                    failures,
                    exception=exc,
                )
            except Exception:
                pass
            raise

    module.MODEL_VERSION = VERSION
    module.LOCK_POLICY = LOCK_POLICY
    module._status_payload = status_payload
    module.run_lock = run_lock
    module.MLB_DAILY_PER_GAME_LOCK_VERSION = VERSION
    module.MLB_LAST_PRELOCK_PROMOTION_VERSION = PROMOTION_POLICY_VERSION
    module.MLB_PER_GAME_LOCK_ATTEMPT_DIAGNOSTICS_VERSION = ATTEMPT_DIAGNOSTICS_VERSION
    module.MLB_LOCK_READINESS_VERSION = READINESS_VERSION
    module.MLB_LOCK_OUTCOME_VERSION = LOCK_OUTCOME_VERSION
    module.MLB_PLAYABILITY_ASSESSMENT_VERSION = RELEASE_ASSESSMENT_VERSION
    module.MLB_LOCK_SOURCE_WINDOW_STABILIZATION_SECONDS = CUTOFF_STABILIZATION_SECONDS
    module._INQSI_MLB_DAILY_PER_GAME_LOCK_V1 = True
    return module
