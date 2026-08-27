from __future__ import annotations

import copy
import functools
import hashlib
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


MISSED_LOCK_TERMINAL_RECONCILIATION_VERSION = (
    "MLB-MISSED-LOCK-TERMINAL-RECONCILIATION-v3-protected-force-replay"
)
PROMOTED_LOCK_TRAINING_ELIGIBILITY_VERSION = (
    "MLB-PROMOTED-LOCK-TRAINING-ELIGIBILITY-v2-verified-empty-exclusions"
)
COOPERATIVE_TERMINAL_CHUNK_VERSION = (
    "MLB-COOPERATIVE-TERMINAL-CHUNK-v3-bounded-proof-lease-handoff"
)
# A canonical owner is admitted with at least 660 seconds left.  Each chunk
# consumes at most one manifest game and stops admitting new work well before
# Lambda's hard timeout so the owner can durably checkpoint and release leases.
COOPERATIVE_TERMINAL_CHUNK_INITIAL_MIN_REMAINING_SECONDS = 300
COOPERATIVE_TERMINAL_CHUNK_GAME_MIN_REMAINING_SECONDS = 180
COOPERATIVE_TERMINAL_CHUNK_WRITE_MIN_REMAINING_SECONDS = 120
COOPERATIVE_TERMINAL_CHUNK_COMPLETION_MIN_REMAINING_SECONDS = 90
COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_QUERY_LIMIT = 4
COOPERATIVE_TERMINAL_IDENTITY_ALIAS_LIMIT = 4
COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS = 32
COOPERATIVE_TERMINAL_MAX_MANIFEST_GAMES = 15
COOPERATIVE_TERMINAL_COMPLETION_LEASE_MARGIN_SECONDS = 60
COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION = (
    "MLB-COOPERATIVE-TERMINAL-COMPLETION-HANDOFF-v1"
)
COOPERATIVE_TERMINAL_IDENTITY_RESOLUTION_VERSION = (
    "MLB-TERMINAL-IDENTITY-RESOLUTION-v1-"
    "unique-official-provider-crosswalk"
)
COOPERATIVE_TERMINAL_MANIFEST_BINDING_VERSION = (
    "MLB-COOPERATIVE-TERMINAL-MANIFEST-BINDING-v2-relevant-authority"
)
COOPERATIVE_TERMINAL_WRITER_LEASE_VERSION = (
    "MLB-LOCK-EXECUTION-LEASE-v2-global-all-mutating"
)
_COOPERATIVE_TERMINAL_IDENTITY_OVERRIDE = (
    "_cooperative_terminal_durable_identity_override"
)
_TERMINAL_CHUNK_STATES = frozenset(
    {"LOCKED_CANONICAL", "LOCKED_NO_PREDICTION_DATA"}
)
_RUNTIME_PATCH_FLAG = "_INQSI_MLB_MISSED_LOCK_TERMINAL_RECONCILIATION_V3"
_APPLY_HOOK_FLAG = "_INQSI_MLB_MISSED_LOCK_TERMINAL_APPLY_HOOK_V3"
_PREPARE_ROW_HOOK_FLAG = "_INQSI_MLB_PROMOTED_LOCK_TRAINING_ELIGIBILITY_V2"
EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS = frozenset(
    {
        "immutable_tminus45_prediction_not_available",
        "incomplete_slate_coverage",
    }
)
_CACHED_TERMINAL_RECONCILIATION_REASONS = frozenset(
    {"POST_WINDOW_TERMINAL_STATUS_ALREADY_RECONCILED"}
)
_EXISTING_POST_WINDOW_SUCCESS_REASONS = frozenset(
    {"POST_WINDOW_TERMINAL_STATUS_RECONCILED"}
)
_POST_WINDOW_REPAIR_REASONS = (
    _CACHED_TERMINAL_RECONCILIATION_REASONS
    | _EXISTING_POST_WINDOW_SUCCESS_REASONS
)
_RAW_MISSED_REASON = "MISSED_PER_GAME_LOCK_NOT_BACKFILLED"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _missed_count_from_result(result: Dict[str, Any]) -> int:
    progress = result.get("perGameLockProgress")
    if isinstance(progress, dict):
        missed = _int(progress.get("missedCount"), 0)
        if missed:
            return missed
    return max(
        _int(result.get("missedGameCount"), 0),
        _int(result.get("missedCount"), 0),
    )


def _cleanup_promoted_lock_training_eligibility(
    row: Dict[str, Any],
) -> Dict[str, Any]:
    """Normalize only stale pre-lock state on a verified immutable lock."""

    out = copy.deepcopy(row or {})
    freeze = (
        dict(out.get("mlFeatureFreeze") or {})
        if isinstance(out.get("mlFeatureFreeze"), dict)
        else {}
    )
    exact_errors = [
        str(error)
        for error in (
            out.get("exactVectorValidationErrors")
            or freeze.get("exactVectorValidationErrors")
            or []
        )
        if str(error)
    ]
    verified_lock = bool(
        out.get("lockedPrediction") is True
        and out.get("immutablePerGameStage") is True
        and out.get("exactVectorVerified") is True
        and not exact_errors
    )
    if not verified_lock:
        return out

    reasons = {
        str(reason)
        for values in (
            out.get("trainingExclusionReasons") or [],
            freeze.get("trainingExclusionReasons") or [],
        )
        for reason in values
        if str(reason)
    }
    cleared = sorted(reasons & EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)
    remaining = sorted(reasons - EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)
    eligible = not remaining
    vector = out.get("frozenFeatureVector")
    exact_vector_present = bool(
        isinstance(vector, dict) and vector.get("fingerprint")
    )
    stale_false_boolean = bool(
        eligible
        and exact_vector_present
        and (
            out.get("trainingEligible") is not True
            or freeze.get("trainingEligible") is not True
        )
    )
    if not cleared and not stale_false_boolean:
        return out

    metadata = {
        "trainingEligible": eligible,
        "trainingExclusionReasons": remaining,
        "expiredPrelockTrainingExclusionsCleared": cleared,
        "staleTrainingEligibleBooleanCleared": stale_false_boolean,
        "promotedLockTrainingEligibilityVersion": (
            PROMOTED_LOCK_TRAINING_ELIGIBILITY_VERSION
        ),
    }
    freeze.update(metadata)
    out.update(
        {
            **metadata,
            "trainingEligibilityStatus": (
                "ELIGIBLE" if eligible else "INELIGIBLE"
            ),
            "mlFeatureFreeze": freeze,
        }
    )
    return out


def _install_prepare_row_training_cleanup(patch: Any) -> None:
    if getattr(patch, _PREPARE_ROW_HOOK_FLAG, False):
        return
    original = getattr(patch, "_prepare_row", None)
    if not callable(original):
        return

    @functools.wraps(original)
    def prepare_row(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return _cleanup_promoted_lock_training_eligibility(
            original(*args, **kwargs)
        )

    patch._prepare_row = prepare_row
    setattr(patch, _PREPARE_ROW_HOOK_FLAG, True)


def _identity_values(value: Dict[str, Any], patch: Any) -> set[str]:
    values = {
        str(value.get(key) or "").strip()
        for key in (
            "gameIdentity",
            "gameId",
            "game_id",
            "id",
            "providerEventId",
            "provider_event_id",
            "officialGamePk",
            "official_game_pk",
        )
    }
    try:
        values.add(str(patch.game_identity(value) or "").strip())
    except Exception:
        pass
    expanded = {item for item in values if item}
    expanded.update(
        item.split(":", 1)[1]
        for item in list(expanded)
        if ":" in item and item.split(":", 1)[1]
    )
    return expanded


def _ensure_missed_lock_diagnostics(
    module: Any,
    patch: Any,
    slate: str,
    result: Dict[str, Any],
    force: bool,
) -> Dict[str, Any]:
    """Backstop the append-only START/OUTCOME pair for a terminal miss."""

    out = copy.deepcopy(result)
    existing_summary = out.get("perGameLockAttemptDiagnostics") or {}
    if _int(existing_summary.get("attemptedGameCount"), 0) > 0:
        return out
    progress = out.get("perGameLockProgress") or {}
    statuses = [
        row
        for row in (progress.get("games") or [])
        if isinstance(row, dict)
        and str(row.get("state") or "") == "MISSED_NOT_BACKFILLED"
    ]
    if not statuses:
        return out
    try:
        attempted_at = module._now_utc().astimezone(timezone.utc)
        pulls = sorted(
            module._pulls_for_date(slate),
            key=lambda pull: patch._pull_at(module, pull)
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        manifest = module._latest_games_for_date(slate, pulls)
        summaries: List[Dict[str, Any]] = []
        for status in statuses:
            status_ids = _identity_values(status, patch)
            game = next(
                (
                    candidate
                    for candidate in manifest
                    if status_ids & _identity_values(candidate, patch)
                ),
                None,
            )
            if not isinstance(game, dict):
                continue
            history = patch._diagnostic_history(module, slate, game, limit=20)
            latest = history.get("latestAttempt") or {}
            if latest.get("outcome") == "MISSED_NOT_BACKFILLED":
                continue
            attempt_id = uuid4().hex
            base = patch._diagnostic_base(
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
            start_item = {
                **base,
                "SK": patch._diagnostic_sk(
                    module, game, attempted_at, attempt_id, "START"
                ),
                "record_type": patch.ATTEMPT_RECORD_TYPE,
                "diagnostic_event": "ATTEMPT_STARTED",
                "created_at": attempted_at.isoformat(),
            }
            start_write = patch._put_diagnostic(module, start_item)
            outcome_item = {
                **base,
                "SK": patch._diagnostic_sk(
                    module, game, attempted_at, attempt_id, "OUTCOME"
                ),
                "record_type": patch.ATTEMPT_OUTCOME_RECORD_TYPE,
                "diagnostic_event": "ATTEMPT_OUTCOME",
                "outcome": "MISSED_NOT_BACKFILLED",
                "reason": "MISSED_NOT_BACKFILLED",
                "state_after_attempt": "MISSED_NOT_BACKFILLED",
                "state_errors_after_attempt": list(status.get("errors") or []),
                "failure_details": [],
                "exception_type": None,
                "exception_message": None,
                "stage_present_after_attempt": False,
                "canonical_proven_after_attempt": False,
                "finished_at_utc": attempted_at.isoformat(),
                "elapsed_milliseconds": 0,
                "created_at": attempted_at.isoformat(),
            }
            outcome_write = patch._put_diagnostic(module, outcome_item)
            summaries.append(
                {
                    "attemptId": attempt_id,
                    "gameIdentity": str(status.get("gameIdentity") or ""),
                    "stateAtAttempt": "MISSED_NOT_BACKFILLED",
                    "stateAfterAttempt": "MISSED_NOT_BACKFILLED",
                    "outcome": "MISSED_NOT_BACKFILLED",
                    "reason": "MISSED_NOT_BACKFILLED",
                    "startWrite": start_write,
                    "outcomeWrite": outcome_write,
                }
            )
        out["perGameLockAttemptDiagnostics"] = {
            "version": patch.ATTEMPT_DIAGNOSTICS_VERSION,
            "appendOnly": True,
            "writeOnce": True,
            "attemptedGameCount": len(summaries),
            "attempts": summaries,
            "terminalMissedLockBackstop": True,
        }
    except Exception as exc:
        errors = list(out.get("lifecycleDiagnosticErrors") or [])
        errors.append(
            {
                "checkpoint": "MISSED_LOCK_DIAGNOSTIC_BACKSTOP",
                "error": f"{type(exc).__name__}:{exc}",
            }
        )
        out["lifecycleDiagnosticErrors"] = errors
    return out


def _repair_proven_no_prediction_misses(
    module: Any,
    patch: Any,
    slate: str,
) -> Dict[str, Any]:
    """Write no-prediction terminals, never post-start predictions."""

    now = module._now_utc().astimezone(timezone.utc)
    try:
        pulls = sorted(
            module._pulls_for_date(slate),
            key=lambda pull: patch._pull_at(module, pull)
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        manifest = module._latest_games_for_date(slate, pulls)
        before = patch._progress(
            module,
            slate,
            pulls,
            manifest,
            now,
            ensure_canonical=False,
        )
        missed = [
            copy.deepcopy(row)
            for row in before.get("games") or []
            if isinstance(row, dict)
            and row.get("state") == "MISSED_NOT_BACKFILLED"
        ]
        if not missed:
            return {
                "ok": True,
                "version": MISSED_LOCK_TERMINAL_RECONCILIATION_VERSION,
                "slateDateEt": slate,
                "reconciledCount": 0,
                "remainingMissedCount": 0,
                "progressAfter": before,
                "postStartPredictionCreationAllowed": False,
            }

        authority = patch._select_provider_manifest_authority(
            module,
            pulls,
            slate,
            manifest,
        )
        games = {patch.game_identity(game): game for game in manifest}
        reconciled: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, Any]] = []
        for status in missed:
            identity = str(status.get("gameIdentity") or "")
            game = games.get(identity)
            if game is None:
                unresolved.append(
                    {"gameIdentity": identity, "reason": "GAME_NOT_IN_MANIFEST"}
                )
                continue
            start = patch._start(module, game)
            if start is None or now < start:
                unresolved.append(
                    {"gameIdentity": identity, "reason": "GAME_NOT_STARTED"}
                )
                continue
            if patch._get_stage(module, slate, game):
                unresolved.append(
                    {"gameIdentity": identity, "reason": "STAGE_NOW_PRESENT"}
                )
                continue
            existing = patch._get_lock_outcome(module, slate, game)
            if existing:
                reconciled.append(
                    {
                        "gameIdentity": identity,
                        "lockStatus": existing.get("lock_status"),
                        "idempotent": True,
                    }
                )
                continue

            scoring = patch._scoring_pulls(module, pulls, game)
            candidate, proof, bound, errors = patch._last_prelock_candidate(
                module,
                slate,
                game,
                scoring,
            )
            proven_absence = bool(
                candidate is None
                and proof is None
                and not bound
                and patch._is_no_prediction_candidate_failure(errors)
            )
            if not proven_absence:
                unresolved.append(
                    {
                        "gameIdentity": identity,
                        "reason": "PRELOCK_CANDIDATE_REQUIRES_REVIEW",
                        "candidatePresent": candidate is not None,
                        "candidateProofPresent": proof is not None,
                        "candidateErrors": list(errors or []),
                    }
                )
                continue

            outcome = patch._put_no_prediction_outcome(
                module,
                slate,
                game,
                now,
                [
                    *(errors or []),
                    "POST_START_PROVEN_NO_PREGAME_PREDICTION_RECONCILIATION",
                ],
                authority,
            )
            if not patch._get_lock_outcome(module, slate, game):
                raise RuntimeError("TERMINAL_OUTCOME_READBACK_MISSING")
            reconciled.append(
                {
                    "gameIdentity": identity,
                    "lockStatus": outcome.get("lock_status"),
                    "idempotent": False,
                }
            )

        after = patch._progress(
            module,
            slate,
            pulls,
            manifest,
            module._now_utc().astimezone(timezone.utc),
            ensure_canonical=False,
        )
        remaining = _int(after.get("missedCount"), 0)
        return {
            "ok": remaining == 0,
            "version": MISSED_LOCK_TERMINAL_RECONCILIATION_VERSION,
            "slateDateEt": slate,
            "manifestGameCount": len(manifest),
            "missedBeforeCount": len(missed),
            "reconciledCount": len(reconciled),
            "remainingMissedCount": remaining,
            "unresolved": unresolved,
            "progressAfter": after,
            "postStartPredictionCreationAllowed": False,
            "candidateIntegrityFailuresRelabeled": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "version": MISSED_LOCK_TERMINAL_RECONCILIATION_VERSION,
            "slateDateEt": slate,
            "reconciledCount": 0,
            "reason": "TERMINAL_RECONCILIATION_FAILED_CLOSED",
            "error": f"{type(exc).__name__}:{exc}",
            "postStartPredictionCreationAllowed": False,
        }


def _attach_repair(
    result: Dict[str, Any],
    report: Dict[str, Any],
) -> Dict[str, Any]:
    """Attach repair evidence while preserving established public contracts."""

    out = copy.deepcopy(result)
    out["missedLockTerminalReconciliation"] = copy.deepcopy(report)
    progress = report.get("progressAfter")
    if not isinstance(progress, dict):
        out.update(
            {
                "ok": False,
                "reason": "PROTECTED_TERMINAL_RECONCILIATION_FAILED_CLOSED",
                "failClosed": True,
                "postStartPredictionCreationAllowed": False,
            }
        )
        return out

    reconciled_count = _int(report.get("reconciledCount"), 0)
    remaining = _int(progress.get("missedCount"), 0)
    due = _int(progress.get("dueMissingCount"), 0)
    unresolved = report.get("unresolved") or []
    cached_idempotent = bool(
        str(result.get("reason") or "")
        in _CACHED_TERMINAL_RECONCILIATION_REASONS
        and reconciled_count == 0
    )
    transition_complete = bool(
        report.get("ok") is True
        and isinstance(unresolved, list)
        and not unresolved
        and remaining == 0
        and due == 0
        and (reconciled_count > 0 or cached_idempotent)
    )
    if not transition_complete:
        out.update(
            {
                "ok": False,
                "reason": "PROTECTED_TERMINAL_RECONCILIATION_FAILED_CLOSED",
                "failClosed": True,
                "postStartPredictionCreationAllowed": False,
            }
        )
        return out

    out["durableNoPredictionTerminalReconciled"] = True
    out["durableNoPredictionTerminalReconciledCount"] = reconciled_count
    out["postStartPredictionCreationAllowed"] = False

    preserve_existing_success_contract = bool(
        str(result.get("reason") or "")
        in _EXISTING_POST_WINDOW_SUCCESS_REASONS
        or cached_idempotent
    )
    if (
        preserve_existing_success_contract
        and result.get("lockStatusComplete") is True
    ):
        return out

    out["perGameLockProgress"] = copy.deepcopy(progress)
    out["missedGameCount"] = remaining
    out["noPredictionDataCount"] = _int(
        progress.get("noPredictionDataCount"), 0
    )
    manifest_count = _int(report.get("manifestGameCount"), 0)
    lock_outcome_count = _int(progress.get("lockOutcomeCount"), 0)
    canonical_count = _int(progress.get("canonicalCount"), 0)
    out["lockStatusComplete"] = bool(
        manifest_count and lock_outcome_count == manifest_count
    )
    out["dailyCardComplete"] = out["lockStatusComplete"]
    out["canonicalPredictionComplete"] = bool(
        manifest_count and canonical_count == manifest_count
    )
    out.update(
        {
            "ok": True,
            "reason": "PROVEN_NO_PREDICTION_TERMINALS_RECONCILED",
            "skipped": False,
            "postStartPredictionCreationAllowed": False,
        }
    )
    out.pop("failClosed", None)
    return out



def _cooperative_chunk_remaining_seconds(context: Any) -> int:
    reader = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(reader):
        return 0
    try:
        return max(0, int(reader()) // 1000)
    except (TypeError, ValueError):
        return 0


def _cooperative_chunk_telemetry(
    *,
    slate: str,
    stage: str,
    remaining_seconds: int,
    game_index: Optional[int] = None,
    game_identity: Optional[str] = None,
    durable_identity: Optional[str] = None,
    phase: Optional[str] = None,
    status: str = "IN_PROGRESS",
    error_code: Optional[str] = None,
) -> None:
    payload: Dict[str, Any] = {
        "event": "MLB_COOPERATIVE_TERMINAL_CHUNK_PROGRESS",
        "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "slateDateEt": slate,
        "stage": stage,
        "status": status,
        "remainingSeconds": remaining_seconds,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "productionAuthorityChanged": False,
    }
    if game_index is not None:
        payload["gameIndex"] = game_index
    if game_identity:
        payload["gameIdentity"] = str(game_identity)[:200]
    if durable_identity:
        payload["durableIdentity"] = str(durable_identity)[:200]
    if phase:
        payload["phase"] = str(phase)[:40]
    if error_code:
        payload["errorCode"] = str(error_code)[:160]
    print(json.dumps(payload, sort_keys=True))


def _strict_chunk_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(
            f"COOPERATIVE_TERMINAL_CHUNK_{field.upper()}_INVALID"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"COOPERATIVE_TERMINAL_CHUNK_{field.upper()}_INVALID"
        ) from exc
    if parsed < 0:
        raise RuntimeError(
            f"COOPERATIVE_TERMINAL_CHUNK_{field.upper()}_INVALID"
        )
    return parsed


def _cooperative_prefixed_identity(value: Any, prefix: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith(("provider:", "official:", "key:", "teams:")):
        return raw
    return f"{prefix}:{raw}"


def _cooperative_terminal_identity_options(
    patch: Any,
    game: Dict[str, Any],
) -> List[str]:
    primary = str(patch.game_identity(game) or "").strip()
    if not primary:
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_IDENTITY_INVALID"
        )
    options = [primary]

    for field in ("officialGamePk", "official_game_pk"):
        value = _cooperative_prefixed_identity(game.get(field), "official")
        if value and value not in options:
            options.append(value)

    explicit = str(game.get("gameIdentity") or "").strip()
    if explicit:
        value = _cooperative_prefixed_identity(explicit, "provider")
        if value not in options:
            options.append(value)

    for field in (
        "game_id",
        "gameId",
        "id",
        "providerEventId",
        "provider_event_id",
    ):
        value = _cooperative_prefixed_identity(game.get(field), "provider")
        if value and value not in options:
            options.append(value)

    if len(options) > COOPERATIVE_TERMINAL_IDENTITY_ALIAS_LIMIT:
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_IDENTITY_ALIAS_LIMIT_EXCEEDED"
        )
    return [options[0], *sorted(options[1:])]


def _cooperative_terminal_manifest_authority_evidence(
    module: Any,
    patch: Any,
    authority: Any,
    manifest_count: int,
) -> Dict[str, Any]:
    if not isinstance(authority, dict):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_AUTHORITY_INVALID"
        )
    evidence = {
        "version": str(authority.get("version") or ""),
        "recordType": str(authority.get("recordType") or ""),
        "pk": str(authority.get("pk") or ""),
        "sk": str(authority.get("sk") or ""),
        "fingerprint": str(authority.get("fingerprint") or ""),
        "gameCount": _strict_chunk_integer(
            authority.get("gameCount"), "authority_game_count"
        ),
        "immutable": authority.get("immutable") is True,
        "writeOnce": authority.get("writeOnce") is True,
        "consistentReadVerified": (
            authority.get("consistentReadVerified") is True
        ),
        "officialScheduleAuthorityVersion": str(
            authority.get("officialScheduleAuthorityVersion") or ""
        ),
        "officialScheduleAuthorityFingerprint": str(
            authority.get("officialScheduleAuthorityFingerprint") or ""
        ),
    }
    schedule = authority.get("scheduleRevisionAuthority")
    if isinstance(schedule, dict):
        evidence["scheduleRevisionAuthority"] = {
            "version": str(schedule.get("version") or ""),
            "pk": str(schedule.get("pk") or ""),
            "sk": str(schedule.get("sk") or ""),
            "fingerprint": str(schedule.get("fingerprint") or ""),
            "gameCount": _strict_chunk_integer(
                schedule.get("gameCount"), "schedule_authority_game_count"
            ),
        }
    if (
        not all(
            evidence.get(field)
            for field in ("version", "recordType", "pk", "sk", "fingerprint")
        )
        or evidence["gameCount"] != manifest_count
        or evidence["immutable"] is not True
        or evidence["writeOnce"] is not True
        or evidence["consistentReadVerified"] is not True
    ):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_AUTHORITY_INVALID"
        )
    authority_keys = [
        {
            "PK": evidence["pk"],
            "SK": evidence["sk"],
        }
    ]
    schedule_evidence = evidence.get("scheduleRevisionAuthority")
    if isinstance(schedule_evidence, dict) and schedule_evidence:
        schedule_key = {
            "PK": str(schedule_evidence.get("pk") or ""),
            "SK": str(schedule_evidence.get("sk") or ""),
        }
        if schedule_key not in authority_keys:
            authority_keys.append(schedule_key)
    atomic_items = []
    for key in authority_keys:
        try:
            item = module.history.PULLS.get_item(
                Key=key,
                ConsistentRead=True,
            ).get("Item")
        except BaseException as exc:
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_"
                "MANIFEST_AUTHORITY_READBACK_FAILED"
            ) from exc
        if not isinstance(item, dict):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_"
                "MANIFEST_AUTHORITY_READBACK_MISSING"
            )
        item_fingerprint = getattr(
            patch,
            "_cooperative_terminal_item_fingerprint",
            None,
        )
        if not callable(item_fingerprint):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_"
                "ITEM_FINGERPRINT_PREREQUISITE_NOT_READY"
            )
        atomic_items.append(
            {
                "tableRole": "PULLS_TABLE",
                "PK": key["PK"],
                "SK": key["SK"],
                "itemFingerprint": item_fingerprint(item),
            }
        )
    evidence["atomicItems"] = atomic_items
    evidence["authorityEvidenceFingerprint"] = hashlib.sha256(
        json.dumps(
            evidence,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return evidence


def _cooperative_terminal_manifest_fingerprint(
    module: Any,
    patch: Any,
    manifest: List[Dict[str, Any]],
    *,
    identities: List[str],
    identity_options: List[List[str]],
    manifest_authority: Dict[str, Any],
) -> str:
    material: List[Dict[str, Any]] = []
    lock_at_reader = getattr(patch, "_lock_at", None)
    for index, game in enumerate(manifest):
        start = patch._start(module, game)
        lock_at = (
            lock_at_reader(module, game)
            if callable(lock_at_reader)
            else None
        )
        material.append(
            {
                "index": index,
                "primaryIdentity": identities[index],
                "identityOptions": list(identity_options[index]),
                "startUtc": start.isoformat() if start is not None else "",
                "scheduledLockAtUtc": (
                    lock_at.isoformat() if lock_at is not None else ""
                ),
                "officialGamePk": str(
                    game.get("officialGamePk")
                    or game.get("official_game_pk")
                    or ""
                ),
                "providerEventId": str(
                    game.get("providerEventId")
                    or game.get("provider_event_id")
                    or ""
                ),
                "gameId": str(
                    game.get("game_id")
                    or game.get("gameId")
                    or game.get("id")
                    or ""
                ),
                "homeTeam": str(
                    game.get("home_team") or game.get("homeTeam") or ""
                ),
                "awayTeam": str(
                    game.get("away_team") or game.get("awayTeam") or ""
                ),
            }
        )
    bound = {
        "bindingVersion": COOPERATIVE_TERMINAL_MANIFEST_BINDING_VERSION,
        "manifestAuthority": manifest_authority,
        "games": material,
    }
    return hashlib.sha256(
        json.dumps(
            bound,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _cooperative_terminal_authority_matches_selected(
    item: Dict[str, Any],
    manifest_authority: Dict[str, Any],
) -> bool:
    authority = item.get("provider_manifest_authority")
    if not isinstance(authority, dict):
        return False
    for source_field, selected_field in (
        ("version", "version"),
        ("recordType", "recordType"),
        ("pk", "pk"),
        ("sk", "sk"),
        ("fingerprint", "fingerprint"),
        ("gameCount", "gameCount"),
        ("officialScheduleAuthorityVersion", "officialScheduleAuthorityVersion"),
        (
            "officialScheduleAuthorityFingerprint",
            "officialScheduleAuthorityFingerprint",
        ),
    ):
        if authority.get(source_field) != manifest_authority.get(
            selected_field
        ):
            return False
    selected_schedule = manifest_authority.get(
        "scheduleRevisionAuthority"
    )
    source_schedule = authority.get("scheduleRevisionAuthority")
    if bool(selected_schedule) != bool(source_schedule):
        return False
    if isinstance(selected_schedule, dict):
        if not isinstance(source_schedule, dict):
            return False
        for field in ("version", "pk", "sk", "fingerprint", "gameCount"):
            if source_schedule.get(field) != selected_schedule.get(field):
                return False
    return True


def _bind_cooperative_terminal_manifest_evidence(
    evidence: Dict[str, Any],
    manifest_authority: Dict[str, Any],
) -> Dict[str, Any]:
    out = copy.deepcopy(evidence)
    out["manifestAuthorityEvidenceFingerprint"] = str(
        manifest_authority.get("authorityEvidenceFingerprint") or ""
    )
    out["evidenceFingerprint"] = (
        _cooperative_terminal_evidence_fingerprint(out)
    )
    return out


def _cooperative_terminal_evidence_fingerprint(
    evidence: Dict[str, Any],
) -> str:
    material = {
        str(key): value
        for key, value in evidence.items()
        if key != "evidenceFingerprint"
    }
    return hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _validated_cooperative_terminal_evidence(
    evidence: Any,
    *,
    durable_identity: str,
    terminal_state: str,
) -> Dict[str, Any]:
    if not isinstance(evidence, dict):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_DURABLE_EVIDENCE_INVALID"
        )
    out = copy.deepcopy(evidence)
    items = out.get("items")
    authority_item_count = (
        1 if terminal_state == "LOCKED_NO_PREDICTION_DATA" else 2
    )
    if (
        str(out.get("durableIdentity") or "") != durable_identity
        or str(out.get("terminalState") or "") != terminal_state
        or not isinstance(items, list)
        or len(items) <= authority_item_count
        or len(items) > COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS
        or _strict_chunk_integer(
            out.get("authorityItemCount"), "authority_item_count"
        )
        != authority_item_count
        or _strict_chunk_integer(
            out.get("dependencyItemCount"), "dependency_item_count"
        )
        != len(items) - authority_item_count
        or any(
            not isinstance(item, dict)
            or str(item.get("tableRole") or "")
            not in {"LOCK_TABLE", "PULLS_TABLE"}
            or not str(item.get("PK") or "")
            or not str(item.get("SK") or "")
            or len(str(item.get("itemFingerprint") or "")) != 64
            for item in items
        )
        or len(
            str(out.get("manifestAuthorityEvidenceFingerprint") or "")
        )
        != 64
        or str(out.get("evidenceFingerprint") or "")
        != _cooperative_terminal_evidence_fingerprint(out)
    ):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_DURABLE_EVIDENCE_INVALID"
        )
    primary_roles = [
        str(item["tableRole"]) for item in items[:authority_item_count]
    ]
    if (
        terminal_state == "LOCKED_NO_PREDICTION_DATA"
        and primary_roles != ["LOCK_TABLE"]
    ) or (
        terminal_state == "LOCKED_CANONICAL"
        and primary_roles != ["LOCK_TABLE", "PULLS_TABLE"]
    ):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_DURABLE_EVIDENCE_INVALID"
        )
    seen = set()
    for item in items:
        key = (
            str(item["tableRole"]),
            str(item["PK"]),
            str(item["SK"]),
        )
        if key in seen:
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_DURABLE_EVIDENCE_INVALID"
            )
        seen.add(key)
    return out


def _cooperative_terminal_checkpoint_fingerprint(
    checkpoint: Dict[str, Any],
) -> str:
    material = {
        str(key): value
        for key, value in checkpoint.items()
        if key
        not in {
            "checkpointFingerprint",
            "attemptCount",
            "lastAttempt",
            "updatedAtUtc",
        }
    }
    return hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _validated_cooperative_terminal_checkpoint(
    checkpoint: Optional[Dict[str, Any]],
    *,
    slate: str,
    request_epoch: int,
    request_id: str,
    manifest_fingerprint: str,
    manifest_authority: Dict[str, Any],
    identities: List[str],
    identity_options: List[List[str]],
) -> Dict[str, Any]:
    if checkpoint is None:
        raw: Dict[str, Any] = {}
    elif isinstance(checkpoint, dict):
        raw = copy.deepcopy(checkpoint)
    else:
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_CHECKPOINT_NOT_OBJECT"
        )

    if raw.get("version") == (
        "MLB-COOPERATIVE-TERMINAL-CHUNK-"
        "v1-one-game-per-eventbridge-owner"
    ):
        # v1 progress was only an observability hint and never proved the full
        # durable prefix. Restart the bounded scan; immutable outcomes/stages
        # remain the authority and make this idempotent.
        raw = {}

    if not raw:
        initial = {
            "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "slateDateEt": slate,
            "requestEpoch": request_epoch,
            "requestId": request_id,
            "manifestFingerprint": manifest_fingerprint,
            "manifestBindingVersion": (
                COOPERATIVE_TERMINAL_MANIFEST_BINDING_VERSION
            ),
            "manifestAuthority": copy.deepcopy(manifest_authority),
            "manifestGameCount": len(identities),
            "phase": "PROCESS",
            "nextGameIndex": 0,
            "processedGameCount": 0,
            "terminalCount": 0,
            "canonicalCount": 0,
            "noPredictionDataCount": 0,
            "reconciledCount": 0,
            "processedGames": [],
            "verificationIndex": 0,
            "verifiedGameCount": 0,
            "verificationComplete": False,
            "attemptCount": 0,
            "identityResolutionVersion": (
                COOPERATIVE_TERMINAL_IDENTITY_RESOLUTION_VERSION
            ),
            "identityAliasLimit": (
                COOPERATIVE_TERMINAL_IDENTITY_ALIAS_LIMIT
            ),
            "candidateAliasQueryLimit": (
                COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_QUERY_LIMIT
            ),
            "writerLeaseVersion": (
                COOPERATIVE_TERMINAL_WRITER_LEASE_VERSION
            ),
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "productionAuthorityChanged": False,
        }
        initial["checkpointFingerprint"] = (
            _cooperative_terminal_checkpoint_fingerprint(initial)
        )
        return initial

    if (
        raw.get("version") != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or str(raw.get("slateDateEt") or "") != slate
        or _strict_chunk_integer(
            raw.get("requestEpoch"), "request_epoch"
        )
        != request_epoch
        or str(raw.get("requestId") or "") != request_id
        or str(raw.get("manifestFingerprint") or "")
        != manifest_fingerprint
        or raw.get("manifestBindingVersion")
        != COOPERATIVE_TERMINAL_MANIFEST_BINDING_VERSION
        or raw.get("manifestAuthority") != manifest_authority
        or _strict_chunk_integer(
            raw.get("manifestGameCount"), "manifest_game_count"
        )
        != len(identities)
        or raw.get("identityResolutionVersion")
        != COOPERATIVE_TERMINAL_IDENTITY_RESOLUTION_VERSION
        or _strict_chunk_integer(
            raw.get("identityAliasLimit"), "identity_alias_limit"
        )
        != COOPERATIVE_TERMINAL_IDENTITY_ALIAS_LIMIT
        or _strict_chunk_integer(
            raw.get("candidateAliasQueryLimit"),
            "candidate_alias_query_limit",
        )
        != COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_QUERY_LIMIT
        or raw.get("writerLeaseVersion")
        != COOPERATIVE_TERMINAL_WRITER_LEASE_VERSION
        or raw.get("postStartPredictionCreationAllowed") is not False
        or raw.get("immutablePredictionRewriteAllowed") is not False
        or raw.get("productionAuthorityChanged") is not False
    ):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_CHECKPOINT_IDENTITY_INVALID"
        )

    next_index = _strict_chunk_integer(
        raw.get("nextGameIndex"), "next_game_index"
    )
    verification_index = _strict_chunk_integer(
        raw.get("verificationIndex"), "verification_index"
    )
    manifest_count = len(identities)
    if next_index > manifest_count or verification_index > manifest_count:
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_CURSOR_OUT_OF_RANGE"
        )

    phase = str(raw.get("phase") or "")
    if (
        phase not in {"PROCESS", "VERIFY"}
        or (next_index < manifest_count and phase != "PROCESS")
        or (next_index == manifest_count and phase != "VERIFY")
        or (phase == "PROCESS" and verification_index != 0)
    ):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_PHASE_INVALID"
        )

    processed = raw.get("processedGames")
    if not isinstance(processed, list) or len(processed) != next_index:
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_PROCESSED_GAMES_INVALID"
        )

    normalized_games: List[Dict[str, Any]] = []
    for index, entry in enumerate(processed):
        if not isinstance(entry, dict):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_PROCESSED_GAME_NOT_OBJECT"
            )
        identity = str(entry.get("gameIdentity") or "")
        durable_identity = str(entry.get("durableIdentity") or "")
        terminal_state = str(entry.get("terminalState") or "")
        if (
            identity != identities[index]
            or durable_identity not in identity_options[index]
            or terminal_state not in _TERMINAL_CHUNK_STATES
            or not isinstance(entry.get("reconciled"), bool)
        ):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_PROCESSED_GAME_INVALID"
            )
        evidence = _validated_cooperative_terminal_evidence(
            entry.get("durableEvidence"),
            durable_identity=durable_identity,
            terminal_state=terminal_state,
        )
        if evidence.get("manifestAuthorityEvidenceFingerprint") != (
            manifest_authority.get("authorityEvidenceFingerprint")
        ):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_"
                "MANIFEST_AUTHORITY_EVIDENCE_MISMATCH"
            )
        normalized_games.append(
            {
                "gameIdentity": identity,
                "durableIdentity": durable_identity,
                "terminalState": terminal_state,
                "reconciled": entry["reconciled"],
                "durableEvidence": evidence,
            }
        )
    if normalized_games:
        _cooperative_terminal_atomic_read_set(
            normalized_games,
            manifest_authority,
        )

    canonical_count = sum(
        entry["terminalState"] == "LOCKED_CANONICAL"
        for entry in normalized_games
    )
    no_prediction_count = sum(
        entry["terminalState"] == "LOCKED_NO_PREDICTION_DATA"
        for entry in normalized_games
    )
    reconciled_count = sum(
        entry["reconciled"] is True for entry in normalized_games
    )
    expected_counts = {
        "processedGameCount": next_index,
        "terminalCount": next_index,
        "canonicalCount": canonical_count,
        "noPredictionDataCount": no_prediction_count,
        "reconciledCount": reconciled_count,
        "verifiedGameCount": verification_index,
    }
    for field, expected in expected_counts.items():
        if _strict_chunk_integer(raw.get(field), field) != expected:
            raise RuntimeError(
                f"COOPERATIVE_TERMINAL_CHUNK_{field.upper()}_MISMATCH"
            )

    verification_complete = (
        phase == "VERIFY" and verification_index == manifest_count
    )
    if raw.get("verificationComplete") is not verification_complete:
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_VERIFICATION_STATE_INVALID"
        )

    normalized = {
        "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "slateDateEt": slate,
        "requestEpoch": request_epoch,
        "requestId": request_id,
        "manifestFingerprint": manifest_fingerprint,
        "manifestBindingVersion": (
            COOPERATIVE_TERMINAL_MANIFEST_BINDING_VERSION
        ),
        "manifestAuthority": copy.deepcopy(manifest_authority),
        "manifestGameCount": manifest_count,
        "phase": phase,
        "nextGameIndex": next_index,
        **expected_counts,
        "processedGames": normalized_games,
        "verificationIndex": verification_index,
        "verificationComplete": verification_complete,
        "attemptCount": _strict_chunk_integer(
            raw.get("attemptCount", 0), "attempt_count"
        ),
        "identityResolutionVersion": (
            COOPERATIVE_TERMINAL_IDENTITY_RESOLUTION_VERSION
        ),
        "identityAliasLimit": (
            COOPERATIVE_TERMINAL_IDENTITY_ALIAS_LIMIT
        ),
        "candidateAliasQueryLimit": (
            COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_QUERY_LIMIT
        ),
        "writerLeaseVersion": COOPERATIVE_TERMINAL_WRITER_LEASE_VERSION,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "productionAuthorityChanged": False,
    }
    if isinstance(raw.get("lastAttempt"), dict):
        normalized["lastAttempt"] = copy.deepcopy(raw["lastAttempt"])
    if raw.get("updatedAtUtc") is not None:
        normalized["updatedAtUtc"] = str(raw.get("updatedAtUtc") or "")
    normalized["checkpointFingerprint"] = (
        _cooperative_terminal_checkpoint_fingerprint(normalized)
    )
    if str(raw.get("checkpointFingerprint") or "") != normalized[
        "checkpointFingerprint"
    ]:
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_CHECKPOINT_FINGERPRINT_INVALID"
        )
    return normalized


def _cooperative_terminal_attempt_checkpoint(
    checkpoint: Dict[str, Any],
    *,
    now: datetime,
    stage: str,
    status: str,
    game_index: Optional[int],
    game_identity: Optional[str],
    durable_identity: Optional[str] = None,
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    out = copy.deepcopy(checkpoint)
    out["attemptCount"] = _strict_chunk_integer(
        out.get("attemptCount", 0), "attempt_count"
    ) + 1
    attempt: Dict[str, Any] = {
        "status": status,
        "stage": stage,
        "atUtc": now.isoformat(),
        "phase": str(out.get("phase") or ""),
    }
    if game_index is not None:
        attempt["gameIndex"] = game_index
    if game_identity:
        attempt["gameIdentity"] = str(game_identity)[:200]
    if durable_identity:
        attempt["durableIdentity"] = str(durable_identity)[:200]
    if error_code:
        attempt["errorCode"] = str(error_code)[:160]
    out["lastAttempt"] = attempt
    out["updatedAtUtc"] = now.isoformat()
    out["checkpointFingerprint"] = (
        _cooperative_terminal_checkpoint_fingerprint(out)
    )
    return out


def _cooperative_terminal_deferred(
    checkpoint: Optional[Dict[str, Any]],
    *,
    slate: str,
    stage: str,
    remaining_seconds: int,
    now: datetime,
    game_index: Optional[int] = None,
    game_identity: Optional[str] = None,
    durable_identity: Optional[str] = None,
    status: str = "DEFERRED_INSUFFICIENT_REMAINING_TIME",
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    writable = isinstance(checkpoint, dict)
    updated = (
        _cooperative_terminal_attempt_checkpoint(
            checkpoint,
            now=now,
            stage=stage,
            status=status,
            game_index=game_index,
            game_identity=game_identity,
            durable_identity=durable_identity,
            error_code=error_code,
        )
        if writable
        else None
    )
    _cooperative_chunk_telemetry(
        slate=slate,
        stage=stage,
        remaining_seconds=remaining_seconds,
        game_index=game_index,
        game_identity=game_identity,
        durable_identity=durable_identity,
        phase=(
            str(checkpoint.get("phase") or "")
            if isinstance(checkpoint, dict)
            else None
        ),
        status=status,
        error_code=error_code,
    )
    return {
        "ok": True,
        "complete": False,
        "deferred": True,
        "stage": stage,
        "errorCode": str(error_code or "")[:160],
        "remainingSeconds": remaining_seconds,
        "checkpoint": updated,
        "checkpointWriteAllowed": writable,
        "terminalChunkVersion": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "productionAuthorityChanged": False,
    }


def _cooperative_terminal_failure(
    checkpoint: Optional[Dict[str, Any]],
    *,
    slate: str,
    stage: str,
    remaining_seconds: int,
    now: datetime,
    error_code: str,
    game_index: Optional[int] = None,
    game_identity: Optional[str] = None,
    durable_identity: Optional[str] = None,
) -> Dict[str, Any]:
    writable = isinstance(checkpoint, dict)
    updated = (
        _cooperative_terminal_attempt_checkpoint(
            checkpoint,
            now=now,
            stage=stage,
            status="FAILED_CLOSED",
            game_index=game_index,
            game_identity=game_identity,
            durable_identity=durable_identity,
            error_code=error_code,
        )
        if writable
        else None
    )
    _cooperative_chunk_telemetry(
        slate=slate,
        stage=stage,
        remaining_seconds=remaining_seconds,
        game_index=game_index,
        game_identity=game_identity,
        durable_identity=durable_identity,
        phase=(
            str(checkpoint.get("phase") or "")
            if isinstance(checkpoint, dict)
            else None
        ),
        status="FAILED_CLOSED",
        error_code=error_code,
    )
    return {
        "ok": False,
        "complete": False,
        "deferred": False,
        "stage": stage,
        "errorCode": str(error_code)[:160],
        "remainingSeconds": remaining_seconds,
        "checkpoint": updated,
        "checkpointWriteAllowed": writable,
        "terminalChunkVersion": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "productionAuthorityChanged": False,
    }


def _cooperative_terminal_atomic_read_set(
    processed_games: List[Dict[str, Any]],
    manifest_authority: Optional[Dict[str, Any]] = None,
) -> tuple[List[Dict[str, Any]], str]:
    by_key: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for item in (
        (manifest_authority or {}).get("atomicItems") or []
    ):
        if (
            not isinstance(item, dict)
            or str(item.get("tableRole") or "") != "PULLS_TABLE"
            or not str(item.get("PK") or "")
            or not str(item.get("SK") or "")
            or len(str(item.get("itemFingerprint") or "")) != 64
        ):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_EVIDENCE_INVALID"
            )
        key = (
            str(item["tableRole"]),
            str(item["PK"]),
            str(item["SK"]),
        )
        by_key[key] = copy.deepcopy(item)
    for entry in processed_games:
        durable_identity = str(entry.get("durableIdentity") or "")
        terminal_state = str(entry.get("terminalState") or "")
        evidence = _validated_cooperative_terminal_evidence(
            entry.get("durableEvidence"),
            durable_identity=durable_identity,
            terminal_state=terminal_state,
        )
        for item in evidence["items"]:
            key = (
                str(item["tableRole"]),
                str(item["PK"]),
                str(item["SK"]),
            )
            prior = by_key.get(key)
            if (
                prior is not None
                and prior.get("itemFingerprint")
                != item.get("itemFingerprint")
            ):
                raise RuntimeError(
                    "COOPERATIVE_TERMINAL_CHUNK_ATOMIC_EVIDENCE_CONFLICT"
                )
            by_key[key] = copy.deepcopy(item)
    requests = [by_key[key] for key in sorted(by_key)]
    if (
        not requests
        or len(requests) > COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS
    ):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_ATOMIC_READ_SET_OUT_OF_RANGE"
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            requests,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return requests, fingerprint


def _validated_cooperative_terminal_complete_checkpoint(
    checkpoint: Any,
) -> tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    if not isinstance(checkpoint, dict):
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")
    out = copy.deepcopy(checkpoint)
    manifest_count = _strict_chunk_integer(
        out.get("manifestGameCount"), "manifest_game_count"
    )
    request_epoch = _strict_chunk_integer(
        out.get("requestEpoch"), "request_epoch"
    )
    request_id = str(out.get("requestId") or "")
    manifest_authority = out.get("manifestAuthority")
    if not isinstance(manifest_authority, dict):
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")
    authority_fingerprint = str(
        manifest_authority.get("authorityEvidenceFingerprint") or ""
    )
    authority_material = {
        key: value
        for key, value in manifest_authority.items()
        if key != "authorityEvidenceFingerprint"
    }
    expected_authority_fingerprint = hashlib.sha256(
        json.dumps(
            authority_material,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    schedule_authority = manifest_authority.get(
        "scheduleRevisionAuthority"
    )
    manifest_atomic_items = manifest_authority.get("atomicItems")
    expected_manifest_keys = [
        (
            "PULLS_TABLE",
            str(manifest_authority.get("pk") or ""),
            str(manifest_authority.get("sk") or ""),
        )
    ]
    schedule_authority_valid = schedule_authority is None
    if isinstance(schedule_authority, dict):
        schedule_key = (
            "PULLS_TABLE",
            str(schedule_authority.get("pk") or ""),
            str(schedule_authority.get("sk") or ""),
        )
        schedule_authority_valid = bool(
            str(schedule_authority.get("version") or "")
            and schedule_key[1]
            and schedule_key[2]
            and len(str(schedule_authority.get("fingerprint") or ""))
            == 64
            and _strict_chunk_integer(
                schedule_authority.get("gameCount"),
                "schedule_authority_game_count",
            )
            == manifest_count
        )
        if schedule_key not in expected_manifest_keys:
            expected_manifest_keys.append(schedule_key)
    manifest_atomic_keys = []
    manifest_atomic_items_valid = isinstance(
        manifest_atomic_items, list
    )
    if manifest_atomic_items_valid:
        for atomic_item in manifest_atomic_items:
            if (
                not isinstance(atomic_item, dict)
                or str(atomic_item.get("tableRole") or "")
                != "PULLS_TABLE"
                or not str(atomic_item.get("PK") or "")
                or not str(atomic_item.get("SK") or "")
                or len(str(atomic_item.get("itemFingerprint") or ""))
                != 64
            ):
                manifest_atomic_items_valid = False
                break
            manifest_atomic_keys.append(
                (
                    "PULLS_TABLE",
                    str(atomic_item["PK"]),
                    str(atomic_item["SK"]),
                )
            )
    manifest_atomic_items_valid = bool(
        manifest_atomic_items_valid
        and len(manifest_atomic_keys) == len(expected_manifest_keys)
        and len(set(manifest_atomic_keys)) == len(manifest_atomic_keys)
        and set(manifest_atomic_keys) == set(expected_manifest_keys)
    )
    processed = out.get("processedGames")
    if (
        out.get("version") != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or not str(out.get("slateDateEt") or "")
        or out.get("manifestBindingVersion")
        != COOPERATIVE_TERMINAL_MANIFEST_BINDING_VERSION
        or out.get("identityResolutionVersion")
        != COOPERATIVE_TERMINAL_IDENTITY_RESOLUTION_VERSION
        or _strict_chunk_integer(
            out.get("identityAliasLimit"), "identity_alias_limit"
        )
        != COOPERATIVE_TERMINAL_IDENTITY_ALIAS_LIMIT
        or _strict_chunk_integer(
            out.get("candidateAliasQueryLimit"),
            "candidate_alias_query_limit",
        )
        != COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_QUERY_LIMIT
        or out.get("writerLeaseVersion")
        != COOPERATIVE_TERMINAL_WRITER_LEASE_VERSION
        or manifest_count <= 0
        or manifest_count > COOPERATIVE_TERMINAL_MAX_MANIFEST_GAMES
        or request_epoch <= 0
        or not request_id
        or len(str(out.get("manifestFingerprint") or "")) != 64
        or authority_fingerprint != expected_authority_fingerprint
        or not all(
            str(manifest_authority.get(field) or "")
            for field in ("version", "recordType", "pk", "sk")
        )
        or len(str(manifest_authority.get("fingerprint") or "")) != 64
        or manifest_authority.get("immutable") is not True
        or manifest_authority.get("writeOnce") is not True
        or manifest_authority.get("consistentReadVerified") is not True
        or not schedule_authority_valid
        or not manifest_atomic_items_valid
        or _strict_chunk_integer(
            manifest_authority.get("gameCount"),
            "authority_game_count",
        )
        != manifest_count
        or out.get("phase") != "VERIFY"
        or _strict_chunk_integer(
            out.get("nextGameIndex"), "next_game_index"
        )
        != manifest_count
        or _strict_chunk_integer(
            out.get("processedGameCount"), "processed_game_count"
        )
        != manifest_count
        or _strict_chunk_integer(
            out.get("terminalCount"), "terminal_count"
        )
        != manifest_count
        or _strict_chunk_integer(
            out.get("verificationIndex"), "verification_index"
        )
        != manifest_count
        or _strict_chunk_integer(
            out.get("verifiedGameCount"), "verified_game_count"
        )
        != manifest_count
        or out.get("verificationComplete") is not True
        or _strict_chunk_integer(
            out.get("attemptCount"), "attempt_count"
        )
        < 1
        or not isinstance(out.get("lastAttempt"), dict)
        or str((out.get("lastAttempt") or {}).get("phase") or "")
        != "VERIFY"
        or not str((out.get("lastAttempt") or {}).get("status") or "")
        or not str((out.get("lastAttempt") or {}).get("stage") or "")
        or not str((out.get("lastAttempt") or {}).get("atUtc") or "")
        or out.get("postStartPredictionCreationAllowed") is not False
        or out.get("immutablePredictionRewriteAllowed") is not False
        or out.get("productionAuthorityChanged") is not False
        or not isinstance(processed, list)
        or len(processed) != manifest_count
        or str(out.get("checkpointFingerprint") or "")
        != _cooperative_terminal_checkpoint_fingerprint(out)
    ):
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")

    identities = []
    canonical_count = 0
    no_prediction_count = 0
    reconciled_count = 0
    for entry in processed:
        if (
            not isinstance(entry, dict)
            or entry.get("reconciled") not in {True, False}
        ):
            raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")
        game_identity_value = str(entry.get("gameIdentity") or "")
        durable_identity = str(entry.get("durableIdentity") or "")
        terminal_state = str(entry.get("terminalState") or "")
        if (
            not game_identity_value
            or durable_identity != game_identity_value
            or terminal_state not in _TERMINAL_CHUNK_STATES
        ):
            raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")
        evidence = _validated_cooperative_terminal_evidence(
            entry.get("durableEvidence"),
            durable_identity=durable_identity,
            terminal_state=terminal_state,
        )
        if evidence.get("manifestAuthorityEvidenceFingerprint") != (
            manifest_authority.get("authorityEvidenceFingerprint")
        ):
            raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")
        evidence_keys = {
            (
                str(item.get("tableRole") or ""),
                str(item.get("PK") or ""),
                str(item.get("SK") or ""),
            )
            for item in evidence["items"]
        }
        if not set(expected_manifest_keys).issubset(evidence_keys):
            raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")
        identities.append(game_identity_value)
        canonical_count += terminal_state == "LOCKED_CANONICAL"
        no_prediction_count += (
            terminal_state == "LOCKED_NO_PREDICTION_DATA"
        )
        reconciled_count += entry["reconciled"] is True
    if len(set(identities)) != manifest_count:
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")
    expected_counts = {
        "canonicalCount": canonical_count,
        "noPredictionDataCount": no_prediction_count,
        "reconciledCount": reconciled_count,
    }
    for field, expected in expected_counts.items():
        if _strict_chunk_integer(out.get(field), field) != expected:
            raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")
    if canonical_count + no_prediction_count != manifest_count:
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")
    requests, read_set_fingerprint = (
        _cooperative_terminal_atomic_read_set(
            processed,
            manifest_authority,
        )
    )
    return out, requests, read_set_fingerprint


def _cooperative_terminal_completion_response(
    checkpoint: Dict[str, Any],
) -> Dict[str, Any]:
    checkpoint, atomic_requests, _ = (
        _validated_cooperative_terminal_complete_checkpoint(checkpoint)
    )
    manifest_count = _strict_chunk_integer(
        checkpoint.get("manifestGameCount"), "manifest_game_count"
    )
    terminal_count = _strict_chunk_integer(
        checkpoint.get("terminalCount"), "terminal_count"
    )
    processed_count = _strict_chunk_integer(
        checkpoint.get("processedGameCount"), "processed_game_count"
    )
    verification_index = _strict_chunk_integer(
        checkpoint.get("verificationIndex"), "verification_index"
    )
    atomic_item_count = len(atomic_requests)
    canonical_count = _strict_chunk_integer(
        checkpoint.get("canonicalCount"), "canonical_count"
    )
    no_prediction_count = _strict_chunk_integer(
        checkpoint.get("noPredictionDataCount"),
        "no_prediction_data_count",
    )
    reconciled_count = _strict_chunk_integer(
        checkpoint.get("reconciledCount"), "reconciled_count"
    )

    progress = {
        "manifestGameCount": manifest_count,
        "processedGameCount": processed_count,
        "verifiedGameCount": manifest_count,
        "verificationIndex": verification_index,
        "verificationComplete": True,
        "atomicDurableItemCount": atomic_item_count,
        "atomicDurableProofRequired": True,
        "canonicalCount": canonical_count,
        "noPredictionDataCount": no_prediction_count,
        "lockOutcomeCount": terminal_count,
        "missedCount": 0,
        "dueMissingCount": 0,
    }
    reason = (
        "PROVEN_NO_PREDICTION_TERMINALS_RECONCILED"
        if reconciled_count
        else "POST_WINDOW_TERMINAL_STATUS_ALREADY_RECONCILED"
    )
    repair = {
        "ok": True,
        "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "slateDateEt": checkpoint["slateDateEt"],
        "manifestGameCount": manifest_count,
        "processedGameCount": processed_count,
        "verifiedGameCount": manifest_count,
        "verificationIndex": verification_index,
        "durableTerminalVerificationComplete": True,
        "atomicDurableProofRequired": True,
        "atomicDurableItemCount": atomic_item_count,
        "completionMutationLeaseRequired": True,
        "reconciledCount": reconciled_count,
        "remainingMissedCount": 0,
        "unresolved": [],
        "progressAfter": copy.deepcopy(progress),
        "identityResolutionVersion": (
            COOPERATIVE_TERMINAL_IDENTITY_RESOLUTION_VERSION
        ),
        "postStartPredictionCreationAllowed": False,
        "candidateIntegrityFailuresRelabeled": False,
    }
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": checkpoint["slateDateEt"],
        "reason": reason,
        "scheduledInvocation": True,
        "skipped": reconciled_count == 0,
        "postWindowTerminalReconciliation": True,
        "singleGamePerEventBridgeOwner": True,
        "terminalChunkVersion": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "checkpointFingerprint": checkpoint["checkpointFingerprint"],
        "manifestFingerprint": checkpoint["manifestFingerprint"],
        "manifestGameCount": manifest_count,
        "processedGameCount": processed_count,
        "verifiedGameCount": manifest_count,
        "verificationIndex": verification_index,
        "verificationPhase": "VERIFY",
        "durableTerminalVerificationComplete": True,
        "atomicDurableProofRequired": True,
        "atomicDurableItemCount": atomic_item_count,
        "atomicDurableProofMaxItemCount": (
            COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS
        ),
        "completionMutationLeaseRequired": True,
        "lockOutcomeCount": terminal_count,
        "missedGameCount": 0,
        "lockStatusComplete": True,
        "dailyCardComplete": True,
        "perGameLockProgress": progress,
        "missedLockTerminalReconciliation": repair,
        "identityResolutionVersion": (
            COOPERATIVE_TERMINAL_IDENTITY_RESOLUTION_VERSION
        ),
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
    }


def _cooperative_no_prediction_outcome_error(
    outcome: Any,
) -> Optional[str]:
    if not isinstance(outcome, dict):
        return "NO_PREDICTION_TERMINAL_AUTHORITY_INVALID"
    exact = {
        "lock_status": "LOCKED_NO_PREDICTION_DATA",
        "lock_outcome_recorded": True,
        "locked_prediction": False,
        "canonical": False,
        "official_prediction": False,
        "playable": False,
        "blocked": True,
        "training_eligible": False,
        "write_once": True,
    }
    if any(outcome.get(field) != value for field, value in exact.items()):
        return "NO_PREDICTION_TERMINAL_AUTHORITY_INVALID"
    exclusions = outcome.get("training_exclusion_reasons")
    if (
        not isinstance(exclusions, list)
        or "missing_immutable_prediction" not in exclusions
    ):
        return "NO_PREDICTION_TERMINAL_AUTHORITY_INVALID"
    reasons = outcome.get("reasons")
    if not isinstance(reasons, list) or not reasons:
        return "NO_PREDICTION_TERMINAL_AUTHORITY_INVALID"
    return None


def _cooperative_terminal_expected_lease_specs(
    patch: Any,
    slate: str,
) -> List[Dict[str, Any]]:
    key_reader = getattr(patch, "_lock_execution_lease_key", None)
    bridge_key_reader = getattr(
        patch, "_legacy_scheduled_single_flight_key", None
    )
    bridge_slates_reader = getattr(
        patch, "_legacy_rollout_bridge_slates", None
    )
    if not all(
        callable(value)
        for value in (
            key_reader,
            bridge_key_reader,
            bridge_slates_reader,
        )
    ):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_COMPLETION_LEASE_PREREQUISITE_NOT_READY"
        )
    return [
        {
            "key": copy.deepcopy(key_reader()),
            "recordType": str(
                getattr(patch, "LOCK_EXECUTION_LEASE_RECORD_TYPE", "")
            ),
            "version": str(
                getattr(patch, "LOCK_EXECUTION_LEASE_VERSION", "")
            ),
        },
        *[
            {
                "key": copy.deepcopy(bridge_key_reader(bridge_slate)),
                "recordType": str(
                    getattr(
                        patch,
                        "LEGACY_SCHEDULED_SINGLE_FLIGHT_RECORD_TYPE",
                        "",
                    )
                ),
                "version": str(
                    getattr(
                        patch,
                        "LEGACY_SCHEDULED_SINGLE_FLIGHT_VERSION",
                        "",
                    )
                ),
            }
            for bridge_slate in bridge_slates_reader(slate)
        ],
    ]


def _validated_cooperative_terminal_completion_lease(
    patch: Any,
    *,
    slate: str,
    lease: Any,
    now_epoch: int,
    require_live: bool,
) -> Dict[str, Any]:
    if not isinstance(lease, dict):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_COMPLETION_LEASE_INVALID"
        )
    expected = _cooperative_terminal_expected_lease_specs(patch, slate)
    owned = lease.get("ownedKeys")
    try:
        expires_epoch = int(lease.get("expiresAtEpoch"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_COMPLETION_LEASE_INVALID"
        ) from exc
    normalized_owned = []
    if not isinstance(owned, list):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_COMPLETION_LEASE_INVALID"
        )
    for value in owned:
        if not isinstance(value, dict):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_COMPLETION_LEASE_INVALID"
            )
        normalized_owned.append(
            {
                "key": {
                    "PK": str((value.get("key") or {}).get("PK") or ""),
                    "SK": str((value.get("key") or {}).get("SK") or ""),
                },
                "recordType": str(value.get("recordType") or ""),
                "version": str(value.get("version") or ""),
            }
        )
    if (
        lease.get("handoffVersion")
        != COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION
        or lease.get("acquired") is not True
        or str(lease.get("slateDateEt") or "") != slate
        or not str(lease.get("owner") or "").strip()
        or normalized_owned != expected
        or expires_epoch <= 0
        or (
            require_live
            and expires_epoch - now_epoch
            < COOPERATIVE_TERMINAL_COMPLETION_LEASE_MARGIN_SECONDS
        )
    ):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_COMPLETION_LEASE_INVALID"
        )
    return {
        "handoffVersion": COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION,
        "acquired": True,
        "slateDateEt": slate,
        "owner": str(lease["owner"]),
        "expiresAtEpoch": expires_epoch,
        "expiresAtUtc": str(lease.get("expiresAtUtc") or ""),
        "ownedKeys": normalized_owned,
    }


def _cooperative_terminal_completion_lease_handle(
    patch: Any,
    *,
    slate: str,
    lease: Dict[str, Any],
    now_epoch: int,
) -> Dict[str, Any]:
    raw = {
        "handoffVersion": COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION,
        "acquired": lease.get("acquired"),
        "slateDateEt": slate,
        "owner": lease.get("owner"),
        "expiresAtEpoch": lease.get("expiresAtEpoch"),
        "expiresAtUtc": lease.get("expiresAtUtc"),
        "ownedKeys": copy.deepcopy(lease.get("ownedKeys")),
    }
    return _validated_cooperative_terminal_completion_lease(
        patch,
        slate=slate,
        lease=raw,
        now_epoch=now_epoch,
        require_live=True,
    )


def _release_cooperative_terminal_completion_lease(
    module: Any,
    patch: Any,
    *,
    slate_date: str,
    lease: Any,
) -> Dict[str, Any]:
    slate = str(slate_date or "").strip()
    now_epoch = int(
        module._now_utc().astimezone(timezone.utc).timestamp()
    )
    validated = _validated_cooperative_terminal_completion_lease(
        patch,
        slate=slate,
        lease=lease,
        now_epoch=now_epoch,
        require_live=False,
    )
    release = getattr(patch, "_release_lock_execution_lease", None)
    if not callable(release):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_COMPLETION_LEASE_RELEASE_NOT_READY"
        )
    release_error: Optional[BaseException] = None
    released = False
    try:
        released = release(module, validated) is True
    except BaseException as exc:
        release_error = exc

    retained = []
    replaced = []
    for owned in validated["ownedKeys"]:
        try:
            item = module.TABLE.get_item(
                Key=owned["key"],
                ConsistentRead=True,
            ).get("Item")
        except BaseException as exc:
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_COMPLETION_LEASE_RELEASE_READ_FAILED"
            ) from exc
        if not isinstance(item, dict):
            continue
        if str(item.get("lease_owner") or "") == validated["owner"]:
            retained.append(copy.deepcopy(owned["key"]))
        else:
            replaced.append(copy.deepcopy(owned["key"]))
    if retained:
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_COMPLETION_LEASE_RETAINED_UNTIL_TTL"
        ) from release_error
    if replaced:
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_COMPLETION_LEASE_OWNER_REPLACED"
        ) from release_error
    if not released and release_error is None:
        # An owner-conditional delete may report ambiguity even though every
        # exact key is now absent.  The strong readback is authoritative.
        released = True
    return {
        "ok": released,
        "released": released,
        "ownedKeyCount": len(validated["ownedKeys"]),
        "ownerExposed": False,
    }


def _validate_cooperative_terminal_completion_handoff(
    module: Any,
    patch: Any,
    *,
    slate_date: str,
    request_epoch: Any,
    request_id: Any,
    checkpoint: Any,
    chunk_result: Any,
) -> Dict[str, Any]:
    slate = str(slate_date or "").strip()
    bound_epoch = _strict_chunk_integer(
        request_epoch, "request_epoch"
    )
    bound_request_id = str(request_id or "")
    validated_checkpoint, requests, read_set_fingerprint = (
        _validated_cooperative_terminal_complete_checkpoint(checkpoint)
    )
    if (
        str(validated_checkpoint.get("slateDateEt") or "") != slate
        or _strict_chunk_integer(
            validated_checkpoint.get("requestEpoch"),
            "checkpoint_request_epoch",
        )
        != bound_epoch
        or str(validated_checkpoint.get("requestId") or "")
        != bound_request_id
        or not isinstance(chunk_result, dict)
        or chunk_result.get("terminalChunkVersion")
        != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or chunk_result.get("complete") is not True
        or chunk_result.get("ok") is not True
    ):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_INVALID"
        )
    lease = _validated_cooperative_terminal_completion_lease(
        patch,
        slate=slate,
        lease=chunk_result.get("_completionLease"),
        now_epoch=int(
            module._now_utc().astimezone(timezone.utc).timestamp()
        ),
        require_live=True,
    )
    proof = chunk_result.get("_atomicCompletionProof")
    terminal_response = chunk_result.get("terminalReplayResponse")
    expected_response = _cooperative_terminal_completion_response(
        validated_checkpoint
    )
    if (
        not isinstance(proof, dict)
        or proof.get("handoffVersion")
        != COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION
        or str(proof.get("slateDateEt") or "") != slate
        or _strict_chunk_integer(
            proof.get("requestEpoch"), "proof_request_epoch"
        )
        != bound_epoch
        or str(proof.get("requestId") or "") != bound_request_id
        or str(proof.get("checkpointFingerprint") or "")
        != str(validated_checkpoint.get("checkpointFingerprint") or "")
        or str(proof.get("leaseOwnerFingerprint") or "")
        != hashlib.sha256(
            lease["owner"].encode("utf-8")
        ).hexdigest()
        or str(proof.get("readSetFingerprint") or "")
        != read_set_fingerprint
        or _strict_chunk_integer(
            proof.get("itemCount"), "proof_item_count"
        )
        != len(requests)
        or _strict_chunk_integer(
            proof.get("verifiedAtEpoch"), "proof_verified_at_epoch"
        )
        <= 0
        or terminal_response != expected_response
    ):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_INVALID"
        )
    return {
        "ok": True,
        "checkpointFingerprint": validated_checkpoint[
            "checkpointFingerprint"
        ],
        "itemCount": len(requests),
        "lease": lease,
        "ownerExposed": False,
    }


def _cooperative_terminal_observed_exact_state(
    module: Any,
    patch: Any,
    *,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    game_index: int,
    durable_identity: str,
    selected_manifest_authority: Dict[str, Any],
    manifest_authority: Dict[str, Any],
) -> tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    original_identity = getattr(patch, "game_identity", None)
    evidence_reader = getattr(
        patch,
        "_cooperative_terminal_authority_evidence",
        None,
    )
    outcome_observer = getattr(
        patch,
        "_cooperative_terminal_lock_outcome_observation",
        None,
    )
    if not callable(original_identity):
        return None, None, "GAME_IDENTITY_NOT_CALLABLE"
    if not callable(evidence_reader):
        return None, None, "DURABLE_EVIDENCE_READER_NOT_READY"
    if not callable(outcome_observer):
        return None, None, "OUTCOME_OBSERVER_NOT_READY"

    scoped_game = copy.deepcopy(manifest[game_index])
    scoped_game[_COOPERATIVE_TERMINAL_IDENTITY_OVERRIDE] = durable_identity
    scoped_manifest = list(manifest)
    scoped_manifest[game_index] = scoped_game

    def scoped_identity(value: Dict[str, Any]) -> str:
        if isinstance(value, dict):
            override = str(
                value.get(_COOPERATIVE_TERMINAL_IDENTITY_OVERRIDE) or ""
            ).strip()
            if override:
                return override
        return str(original_identity(value) or "")

    setattr(patch, "game_identity", scoped_identity)
    try:
        outcome_observation = outcome_observer(
            module,
            slate,
            scoped_game,
        )
        if (
            not isinstance(outcome_observation, dict)
            or outcome_observation.get("exists") not in {True, False}
            or outcome_observation.get("valid") not in {True, False}
            or not isinstance(
                outcome_observation.get("errors"), list
            )
        ):
            return None, None, "OUTCOME_OBSERVATION_INVALID"
        if (
            outcome_observation["exists"] is True
            and outcome_observation["valid"] is not True
        ):
            return (
                None,
                None,
                "IMMUTABLE_LOCK_OUTCOME_AUTHORITY_INVALID",
            )
        outcome = (
            outcome_observation.get("item")
            if outcome_observation["valid"] is True
            else None
        )
        stored_stage = patch._get_stage(module, slate, scoped_game)
        if outcome and stored_stage:
            return None, None, "AMBIGUOUS_DUAL_TERMINAL_AUTHORITY"
        if outcome:
            error = _cooperative_no_prediction_outcome_error(outcome)
            if error:
                return None, None, error
            if not _cooperative_terminal_authority_matches_selected(
                outcome,
                selected_manifest_authority,
            ):
                return (
                    None,
                    None,
                    "DURABLE_TERMINAL_MANIFEST_AUTHORITY_MISMATCH",
                )
            evidence = evidence_reader(
                module,
                durable_identity=durable_identity,
                terminal_state="LOCKED_NO_PREDICTION_DATA",
                outcome=outcome,
                stored_stage=None,
                canonical=None,
            )
            evidence = _bind_cooperative_terminal_manifest_evidence(
                evidence,
                manifest_authority,
            )
            evidence = _validated_cooperative_terminal_evidence(
                evidence,
                durable_identity=durable_identity,
                terminal_state="LOCKED_NO_PREDICTION_DATA",
            )
            return "LOCKED_NO_PREDICTION_DATA", evidence, None
        if not stored_stage:
            return None, None, None
        if not isinstance(stored_stage, dict):
            return None, None, "IMMUTABLE_STAGE_AUTHORITY_INVALID"
        scoring = patch._scoring_pulls(module, pulls, scoped_game)
        stage_errors = list(
            patch._validate_stage(
                module,
                stored_stage,
                slate,
                scoped_game,
                scoped_manifest,
                scoring,
            )
            or []
        )
        if stage_errors:
            return None, None, "IMMUTABLE_STAGE_AUTHORITY_INVALID"
        stage_row = copy.deepcopy(
            ((stored_stage.get("data") or {}).get("row")) or {}
        )
        canonical = patch._canonical_readback(module, stage_row)
        if not canonical:
            return None, None, "IMMUTABLE_CANONICAL_READBACK_MISSING"
        if not _cooperative_terminal_authority_matches_selected(
            stored_stage,
            selected_manifest_authority,
        ):
            return (
                None,
                None,
                "DURABLE_TERMINAL_MANIFEST_AUTHORITY_MISMATCH",
            )
        evidence = evidence_reader(
            module,
            durable_identity=durable_identity,
            terminal_state="LOCKED_CANONICAL",
            outcome=None,
            stored_stage=stored_stage,
            canonical=canonical,
        )
        evidence = _bind_cooperative_terminal_manifest_evidence(
            evidence,
            manifest_authority,
        )
        evidence = _validated_cooperative_terminal_evidence(
            evidence,
            durable_identity=durable_identity,
            terminal_state="LOCKED_CANONICAL",
        )
        return "LOCKED_CANONICAL", evidence, None
    finally:
        setattr(patch, "game_identity", original_identity)


def _cooperative_terminal_observed_state(
    module: Any,
    patch: Any,
    *,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    game_index: int,
    identity_options: List[str],
    selected_manifest_authority: Dict[str, Any],
    manifest_authority: Dict[str, Any],
) -> tuple[
    Optional[str],
    Optional[str],
    Optional[Dict[str, Any]],
    Optional[str],
]:
    """Strongly resolve one terminal across a bounded unique ID crosswalk."""

    observed: List[tuple[str, str, Dict[str, Any]]] = []
    for durable_identity in identity_options:
        state, evidence, error = (
            _cooperative_terminal_observed_exact_state(
                module,
                patch,
                slate=slate,
                pulls=pulls,
                manifest=manifest,
                game_index=game_index,
                durable_identity=durable_identity,
                selected_manifest_authority=selected_manifest_authority,
                manifest_authority=manifest_authority,
            )
        )
        if error:
            return None, None, None, error
        if state and isinstance(evidence, dict):
            observed.append((durable_identity, state, evidence))
    if len(observed) > 1:
        return (
            None,
            None,
            None,
            "AMBIGUOUS_DURABLE_TERMINAL_IDENTITY",
        )
    if not observed:
        return None, None, None, None
    durable_identity, state, evidence = observed[0]
    if durable_identity != identity_options[0]:
        # Normal status/training authority is provider-manifest-primary.  A
        # legacy official-only row is observed to prevent a duplicate write,
        # but it cannot close coverage that production readers would still
        # report as missed.
        return (
            None,
            None,
            None,
            "NONCANONICAL_TERMINAL_ALIAS_REQUIRES_REVIEW",
        )
    return state, durable_identity, evidence, None


def _cooperative_terminal_write_identity(
    identity_options: List[str],
) -> str:
    # New durable authority must use the provider-manifest canonical identity.
    # Official IDs remain bounded lookup aliases for historical rows only:
    # the manifest validator rejects an outcome whose game_identity is not in
    # its canonical provider-first identity list.
    return identity_options[0]


def _cooperative_put_no_prediction_outcome(
    module: Any,
    patch: Any,
    *,
    slate: str,
    manifest: List[Dict[str, Any]],
    game_index: int,
    durable_identity: str,
    now: datetime,
    reasons: List[str],
    authority: Dict[str, Any],
) -> Any:
    original_identity = getattr(patch, "game_identity", None)
    if not callable(original_identity):
        raise RuntimeError(
            "COOPERATIVE_TERMINAL_CHUNK_GAME_IDENTITY_NOT_CALLABLE"
        )
    scoped_game = copy.deepcopy(manifest[game_index])
    scoped_game[_COOPERATIVE_TERMINAL_IDENTITY_OVERRIDE] = durable_identity

    def scoped_identity(value: Dict[str, Any]) -> str:
        if isinstance(value, dict):
            override = str(
                value.get(_COOPERATIVE_TERMINAL_IDENTITY_OVERRIDE) or ""
            ).strip()
            if override:
                return override
        return str(original_identity(value) or "")

    setattr(patch, "game_identity", scoped_identity)
    try:
        return patch._put_no_prediction_outcome(
            module,
            slate,
            scoped_game,
            now,
            reasons,
            authority,
        )
    finally:
        setattr(patch, "game_identity", original_identity)


def _cooperative_terminal_exception_code(
    stage: str,
    exc: BaseException,
) -> str:
    value = str(exc or "").strip()
    if (
        value
        and len(value) <= 160
        and all(
            character.isupper()
            or character.isdigit()
            or character in "_:-."
            for character in value
        )
    ):
        return value
    return f"{stage}_{type(exc).__name__}"[:160]


def _execute_cooperative_terminal_target(
    module: Any,
    patch: Any,
    *,
    slate: str,
    pulls: List[Dict[str, Any]],
    manifest: List[Dict[str, Any]],
    selected_manifest_authority: Dict[str, Any],
    manifest_authority: Dict[str, Any],
    identities: List[str],
    identity_options: List[List[str]],
    checkpoint: Dict[str, Any],
    context: Any,
) -> Dict[str, Any]:
    phase = str(checkpoint["phase"])
    game_index = (
        int(checkpoint["nextGameIndex"])
        if phase == "PROCESS"
        else int(checkpoint["verificationIndex"])
    )
    game = manifest[game_index]
    identity = identities[game_index]
    durable_identity: Optional[str] = None
    durable_evidence: Optional[Dict[str, Any]] = None
    stage = (
        "READ_DURABLE_TERMINAL"
        if phase == "PROCESS"
        else "VERIFY_DURABLE_TERMINAL"
    )
    try:
        remaining = _cooperative_chunk_remaining_seconds(context)
        _cooperative_chunk_telemetry(
            slate=slate,
            stage=stage,
            remaining_seconds=remaining,
            game_index=game_index,
            game_identity=identity,
            phase=phase,
        )
        (
            terminal_state,
            durable_identity,
            durable_evidence,
            terminal_error,
        ) = _cooperative_terminal_observed_state(
                module,
                patch,
                slate=slate,
                pulls=pulls,
                manifest=manifest,
                game_index=game_index,
                identity_options=identity_options[game_index],
                selected_manifest_authority=selected_manifest_authority,
                manifest_authority=manifest_authority,
            )
        if terminal_error:
            return _cooperative_terminal_failure(
                checkpoint,
                slate=slate,
                stage=stage,
                remaining_seconds=_cooperative_chunk_remaining_seconds(
                    context
                ),
                now=module._now_utc().astimezone(timezone.utc),
                error_code=terminal_error,
                game_index=game_index,
                game_identity=identity,
                durable_identity=durable_identity,
            )

        if phase == "VERIFY":
            expected = checkpoint["processedGames"][game_index]
            if (
                terminal_state != expected["terminalState"]
                or durable_identity != expected["durableIdentity"]
                or durable_evidence != expected["durableEvidence"]
            ):
                return _cooperative_terminal_failure(
                    checkpoint,
                    slate=slate,
                    stage=stage,
                    remaining_seconds=_cooperative_chunk_remaining_seconds(
                        context
                    ),
                    now=module._now_utc().astimezone(timezone.utc),
                    error_code="DURABLE_VERIFICATION_MISMATCH",
                    game_index=game_index,
                    game_identity=identity,
                    durable_identity=durable_identity,
                )
            advanced = copy.deepcopy(checkpoint)
            advanced["verificationIndex"] = game_index + 1
            advanced["verifiedGameCount"] = game_index + 1
            advanced["verificationComplete"] = (
                game_index + 1 == len(manifest)
            )
            complete = advanced["verificationComplete"] is True
            checkpoint_stage = (
                "COMPLETE_READY"
                if complete
                else "VERIFICATION_CHECKPOINT_READY"
            )
            advanced = _cooperative_terminal_attempt_checkpoint(
                advanced,
                now=module._now_utc().astimezone(timezone.utc),
                stage=checkpoint_stage,
                status="DURABLE_TERMINAL_VERIFIED",
                game_index=game_index,
                game_identity=identity,
                durable_identity=durable_identity,
            )
            remaining = _cooperative_chunk_remaining_seconds(context)
            _cooperative_chunk_telemetry(
                slate=slate,
                stage=checkpoint_stage,
                remaining_seconds=remaining,
                game_index=game_index,
                game_identity=identity,
                durable_identity=durable_identity,
                phase=phase,
                status="DURABLE_TERMINAL_VERIFIED",
            )
            return {
                "ok": True,
                "complete": False,
                "deferred": False,
                "stage": checkpoint_stage,
                "remainingSeconds": remaining,
                "checkpoint": advanced,
                "checkpointWriteAllowed": True,
                "terminalChunkVersion": COOPERATIVE_TERMINAL_CHUNK_VERSION,
                "verifiedGameIdentity": identity,
                "verifiedDurableIdentity": durable_identity,
                "terminalWrittenThisInvocation": False,
                "completionReady": complete,
                "postStartPredictionCreationAllowed": False,
                "immutablePredictionRewriteAllowed": False,
                "productionAuthorityChanged": False,
            }

        reconciled = False
        if terminal_state is None:
            stage = "PROVE_PRELOCK_ABSENCE"
            scoring = patch._scoring_pulls(module, pulls, game)
            candidate, proof, bound, errors = patch._last_prelock_candidate(
                module,
                slate,
                game,
                scoring,
            )
            proven_absence = bool(
                candidate is None
                and proof is None
                and not bound
                and patch._is_no_prediction_candidate_failure(errors)
            )
            if not proven_absence:
                return _cooperative_terminal_failure(
                    checkpoint,
                    slate=slate,
                    stage=stage,
                    remaining_seconds=_cooperative_chunk_remaining_seconds(
                        context
                    ),
                    now=module._now_utc().astimezone(timezone.utc),
                    error_code="PRELOCK_CANDIDATE_REQUIRES_REVIEW",
                    game_index=game_index,
                    game_identity=identity,
                )

            stage = "BIND_MANIFEST_AUTHORITY"
            authority = copy.deepcopy(selected_manifest_authority)
            remaining = _cooperative_chunk_remaining_seconds(context)
            if (
                remaining
                < COOPERATIVE_TERMINAL_CHUNK_WRITE_MIN_REMAINING_SECONDS
            ):
                return _cooperative_terminal_deferred(
                    checkpoint,
                    slate=slate,
                    stage="WRITE_BUDGET",
                    remaining_seconds=remaining,
                    now=module._now_utc().astimezone(timezone.utc),
                    game_index=game_index,
                    game_identity=identity,
                )

            durable_identity = _cooperative_terminal_write_identity(
                identity_options[game_index]
            )
            stage = "WRITE_NO_PREDICTION_TERMINAL"
            _cooperative_chunk_telemetry(
                slate=slate,
                stage=stage,
                remaining_seconds=remaining,
                game_index=game_index,
                game_identity=identity,
                durable_identity=durable_identity,
                phase=phase,
            )
            _cooperative_put_no_prediction_outcome(
                module,
                patch,
                slate=slate,
                manifest=manifest,
                game_index=game_index,
                durable_identity=durable_identity,
                now=module._now_utc().astimezone(timezone.utc),
                reasons=[
                    *(errors or []),
                    (
                        "POST_START_PROVEN_NO_PREGAME_PREDICTION_"
                        "RECONCILIATION"
                    ),
                ],
                authority=authority,
            )
            stage = "READBACK_NO_PREDICTION_TERMINAL"
            (
                readback_state,
                readback_identity,
                readback_evidence,
                readback_error,
            ) = _cooperative_terminal_observed_state(
                module,
                patch,
                slate=slate,
                pulls=pulls,
                manifest=manifest,
                game_index=game_index,
                identity_options=identity_options[game_index],
                selected_manifest_authority=selected_manifest_authority,
                manifest_authority=manifest_authority,
            )
            if (
                readback_error
                or readback_state != "LOCKED_NO_PREDICTION_DATA"
                or readback_identity != durable_identity
            ):
                raise RuntimeError(
                    readback_error
                    or "COOPERATIVE_TERMINAL_CHUNK_OUTCOME_READBACK_INVALID"
                )
            terminal_state = readback_state
            durable_identity = readback_identity
            durable_evidence = readback_evidence
            reconciled = True

        if (
            terminal_state not in _TERMINAL_CHUNK_STATES
            or durable_identity not in identity_options[game_index]
            or not isinstance(durable_evidence, dict)
        ):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_DURABLE_TARGET_INVALID"
            )

        advanced = copy.deepcopy(checkpoint)
        advanced["processedGames"] = [
            *advanced["processedGames"],
            {
                "gameIdentity": identity,
                "durableIdentity": durable_identity,
                "terminalState": terminal_state,
                "reconciled": reconciled,
                "durableEvidence": copy.deepcopy(durable_evidence),
            },
        ]
        advanced["nextGameIndex"] = game_index + 1
        advanced["processedGameCount"] = game_index + 1
        advanced["terminalCount"] = game_index + 1
        advanced["canonicalCount"] = sum(
            entry["terminalState"] == "LOCKED_CANONICAL"
            for entry in advanced["processedGames"]
        )
        advanced["noPredictionDataCount"] = sum(
            entry["terminalState"] == "LOCKED_NO_PREDICTION_DATA"
            for entry in advanced["processedGames"]
        )
        advanced["reconciledCount"] = sum(
            entry["reconciled"] is True
            for entry in advanced["processedGames"]
        )
        if game_index + 1 == len(manifest):
            advanced["phase"] = "VERIFY"
            advanced["verificationIndex"] = 0
            advanced["verifiedGameCount"] = 0
            advanced["verificationComplete"] = False
        advanced = _cooperative_terminal_attempt_checkpoint(
            advanced,
            now=module._now_utc().astimezone(timezone.utc),
            stage="PROCESS_CHECKPOINT_READY",
            status="TERMINAL_CHECKPOINT_READY",
            game_index=game_index,
            game_identity=identity,
            durable_identity=durable_identity,
        )
        remaining = _cooperative_chunk_remaining_seconds(context)
        _cooperative_chunk_telemetry(
            slate=slate,
            stage="PROCESS_CHECKPOINT_READY",
            remaining_seconds=remaining,
            game_index=game_index,
            game_identity=identity,
            durable_identity=durable_identity,
            phase=phase,
            status="TERMINAL_CHECKPOINT_READY",
        )
        return {
            "ok": True,
            "complete": False,
            "deferred": False,
            "stage": "PROCESS_CHECKPOINT_READY",
            "remainingSeconds": remaining,
            "checkpoint": advanced,
            "checkpointWriteAllowed": True,
            "terminalChunkVersion": COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "processedGameIdentity": identity,
            "processedDurableIdentity": durable_identity,
            "processedTerminalState": terminal_state,
            "terminalWrittenThisInvocation": reconciled,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "productionAuthorityChanged": False,
        }
    except BaseException as exc:
        return _cooperative_terminal_failure(
            checkpoint,
            slate=slate,
            stage=stage,
            remaining_seconds=_cooperative_chunk_remaining_seconds(context),
            now=module._now_utc().astimezone(timezone.utc),
            error_code=_cooperative_terminal_exception_code(stage, exc),
            game_index=game_index,
            game_identity=identity,
            durable_identity=durable_identity,
        )


def _run_cooperative_terminal_chunk_impl(
    module: Any,
    patch: Any,
    *,
    slate_date: str,
    request_epoch: Any,
    request_id: Any,
    checkpoint: Optional[Dict[str, Any]] = None,
    context: Any,
) -> Dict[str, Any]:
    """Process or verify exactly one historical terminal under writer leases."""

    slate = str(slate_date or "").strip()
    stage = "INITIAL_BUDGET"
    remaining = _cooperative_chunk_remaining_seconds(context)
    now = module._now_utc().astimezone(timezone.utc)
    try:
        bound_request_epoch = _strict_chunk_integer(
            request_epoch, "request_epoch"
        )
    except RuntimeError:
        bound_request_epoch = 0
    bound_request_id = str(request_id or "").strip()
    current_checkpoint: Optional[Dict[str, Any]] = None
    game_index: Optional[int] = None
    identity: Optional[str] = None
    phase: Optional[str] = None
    _cooperative_chunk_telemetry(
        slate=slate,
        stage=stage,
        remaining_seconds=remaining,
    )
    if bound_request_epoch <= 0 or not bound_request_id:
        return _cooperative_terminal_failure(
            None,
            slate=slate,
            stage="BIND_REQUEST",
            remaining_seconds=remaining,
            now=now,
            error_code="REQUEST_IDENTITY_INVALID",
        )
    if remaining < COOPERATIVE_TERMINAL_CHUNK_INITIAL_MIN_REMAINING_SECONDS:
        return _cooperative_terminal_deferred(
            None,
            slate=slate,
            stage=stage,
            remaining_seconds=remaining,
            now=now,
        )

    try:
        try:
            parsed_slate = datetime.strptime(slate, "%Y-%m-%d").date()
            parsed_today = datetime.strptime(
                str(module._today_et()), "%Y-%m-%d"
            ).date()
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_SLATE_DATE_INVALID"
            ) from exc
        if parsed_slate >= parsed_today:
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_NOT_EXACT_HISTORICAL_DATE"
            )

        stage = "LOAD_PULL_HISTORY"
        pulls = sorted(
            list(module._pulls_for_date(slate) or []),
            key=lambda pull: patch._pull_at(module, pull)
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        if not pulls:
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_PULL_HISTORY_MISSING"
            )

        stage = "RESOLVE_MANIFEST"
        manifest = list(module._latest_games_for_date(slate, pulls) or [])
        manifest = sorted(
            manifest,
            key=lambda game: (
                (
                    patch._start(module, game)
                    or datetime.min.replace(tzinfo=timezone.utc)
                ).isoformat(),
                str(patch.game_identity(game) or ""),
            ),
        )
        if len(manifest) > COOPERATIVE_TERMINAL_MAX_MANIFEST_GAMES:
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_TOO_LARGE"
            )
        identities: List[str] = []
        identity_options: List[List[str]] = []
        identity_owner: Dict[str, int] = {}
        for index, game in enumerate(manifest):
            options = _cooperative_terminal_identity_options(patch, game)
            for option in options:
                if option in identity_owner:
                    raise RuntimeError(
                        "COOPERATIVE_TERMINAL_CHUNK_"
                        "AMBIGUOUS_MANIFEST_IDENTITY"
                    )
                identity_owner[option] = index
            identities.append(options[0])
            identity_options.append(options)
        if not identities:
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_IDENTITY_INVALID"
            )

        stage = "BIND_MANIFEST_AUTHORITY"
        selected_manifest_authority = (
            patch._select_provider_manifest_authority(
                module,
                pulls,
                slate,
                manifest,
            )
        )
        manifest_authority = (
            _cooperative_terminal_manifest_authority_evidence(
                module,
                patch,
                selected_manifest_authority,
                len(manifest),
            )
        )
        manifest_fingerprint = _cooperative_terminal_manifest_fingerprint(
            module,
            patch,
            manifest,
            identities=identities,
            identity_options=identity_options,
            manifest_authority=manifest_authority,
        )
        current_checkpoint = _validated_cooperative_terminal_checkpoint(
            checkpoint,
            slate=slate,
            request_epoch=bound_request_epoch,
            request_id=bound_request_id,
            manifest_fingerprint=manifest_fingerprint,
            manifest_authority=manifest_authority,
            identities=identities,
            identity_options=identity_options,
        )
        phase = str(current_checkpoint["phase"])
        game_index = (
            int(current_checkpoint["nextGameIndex"])
            if phase == "PROCESS"
            else int(current_checkpoint["verificationIndex"])
        )
        remaining = _cooperative_chunk_remaining_seconds(context)
        _cooperative_chunk_telemetry(
            slate=slate,
            stage="MANIFEST_READY",
            remaining_seconds=remaining,
            game_index=game_index,
            phase=phase,
            status="READY",
        )

        if (
            phase == "VERIFY"
            and game_index == len(manifest)
            and current_checkpoint["verificationComplete"] is True
        ):
            stage = "ATOMIC_COMPLETION_PROOF"
            if (
                remaining
                < COOPERATIVE_TERMINAL_CHUNK_COMPLETION_MIN_REMAINING_SECONDS
            ):
                return _cooperative_terminal_deferred(
                    current_checkpoint,
                    slate=slate,
                    stage=stage,
                    remaining_seconds=remaining,
                    now=module._now_utc().astimezone(timezone.utc),
                    game_index=game_index,
                )
            acquire = getattr(
                patch, "_acquire_lock_execution_lease", None
            )
            release = getattr(
                patch, "_release_lock_execution_lease", None
            )
            atomic_verify = getattr(
                patch, "_cooperative_terminal_atomic_verify", None
            )
            if (
                not callable(acquire)
                or not callable(release)
                or not callable(atomic_verify)
                or getattr(patch, "LOCK_EXECUTION_LEASE_VERSION", None)
                != COOPERATIVE_TERMINAL_WRITER_LEASE_VERSION
            ):
                raise RuntimeError(
                    "COOPERATIVE_TERMINAL_CHUNK_"
                    "ATOMIC_COMPLETION_PREREQUISITE_NOT_READY"
                )
            lease = acquire(
                module,
                slate,
                module._now_utc().astimezone(timezone.utc),
            )
            if not isinstance(lease, dict):
                raise RuntimeError(
                    "COOPERATIVE_TERMINAL_CHUNK_"
                    "WRITER_LEASE_RESULT_INVALID"
                )
            if lease.get("acquired") is not True:
                if lease.get("acquired") is not False:
                    raise RuntimeError(
                        "COOPERATIVE_TERMINAL_CHUNK_"
                        "WRITER_LEASE_RESULT_INVALID"
                    )
                return _cooperative_terminal_deferred(
                    current_checkpoint,
                    slate=slate,
                    stage="MUTATION_LEASE_CONTENDED",
                    remaining_seconds=(
                        _cooperative_chunk_remaining_seconds(context)
                    ),
                    now=module._now_utc().astimezone(timezone.utc),
                    game_index=game_index,
                    status="DEFERRED_MUTATION_LEASE_CONTENDED",
                    error_code="WRITER_LEASE_CONTENDED",
                )

            handoff = False
            try:
                now_for_proof = module._now_utc().astimezone(timezone.utc)
                lease_handle = (
                    _cooperative_terminal_completion_lease_handle(
                        patch,
                        slate=slate,
                        lease=lease,
                        now_epoch=int(now_for_proof.timestamp()),
                    )
                )
                _cooperative_chunk_telemetry(
                    slate=slate,
                    stage=stage,
                    remaining_seconds=(
                        _cooperative_chunk_remaining_seconds(context)
                    ),
                    game_index=game_index,
                    phase=phase,
                )
                atomic_proof = atomic_verify(
                    module,
                    current_checkpoint["processedGames"],
                    current_checkpoint["manifestAuthority"],
                )
                atomic_requests, expected_read_set_fingerprint = (
                    _cooperative_terminal_atomic_read_set(
                        current_checkpoint["processedGames"],
                        current_checkpoint["manifestAuthority"],
                    )
                )
                expected_atomic_items = len(atomic_requests)
                if (
                    not isinstance(atomic_proof, dict)
                    or atomic_proof.get("ok") is not True
                    or atomic_proof.get("atomicSnapshot") is not True
                    or _strict_chunk_integer(
                        atomic_proof.get("itemCount"),
                        "atomic_item_count",
                    )
                    != expected_atomic_items
                    or str(atomic_proof.get("readSetFingerprint") or "")
                    != expected_read_set_fingerprint
                    or atomic_proof.get(
                        "postStartPredictionCreationAllowed"
                    )
                    is not False
                ):
                    raise RuntimeError(
                        "COOPERATIVE_TERMINAL_CHUNK_"
                        "ATOMIC_COMPLETION_PROOF_INVALID"
                    )

                response = _cooperative_terminal_completion_response(
                    current_checkpoint
                )
                verified_epoch = int(
                    module._now_utc().astimezone(timezone.utc).timestamp()
                )
                private_proof = {
                    "handoffVersion": (
                        COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION
                    ),
                    "slateDateEt": slate,
                    "requestEpoch": bound_request_epoch,
                    "requestId": bound_request_id,
                    "checkpointFingerprint": current_checkpoint[
                        "checkpointFingerprint"
                    ],
                    "leaseOwnerFingerprint": hashlib.sha256(
                        lease_handle["owner"].encode("utf-8")
                    ).hexdigest(),
                    "readSetFingerprint": expected_read_set_fingerprint,
                    "itemCount": expected_atomic_items,
                    "verifiedAtEpoch": verified_epoch,
                }
                remaining = _cooperative_chunk_remaining_seconds(context)
                result = {
                    "ok": True,
                    "complete": True,
                    "deferred": False,
                    "stage": "COMPLETE",
                    "remainingSeconds": remaining,
                    "checkpoint": current_checkpoint,
                    "checkpointWriteAllowed": False,
                    "atomicCompletionProof": {
                        "atomicSnapshot": True,
                        "itemCount": expected_atomic_items,
                        "maxItemCount": (
                            COOPERATIVE_TERMINAL_ATOMIC_MAX_ITEMS
                        ),
                        "readSetFingerprint": (
                            expected_read_set_fingerprint
                        ),
                        "completionMutationLeaseHeld": True,
                        "ownerExposed": False,
                    },
                    "_completionLease": lease_handle,
                    "_atomicCompletionProof": private_proof,
                    "terminalReplayResponse": response,
                    "terminalChunkVersion": (
                        COOPERATIVE_TERMINAL_CHUNK_VERSION
                    ),
                    "terminalWrittenThisInvocation": False,
                    "postStartPredictionCreationAllowed": False,
                    "immutablePredictionRewriteAllowed": False,
                    "productionAuthorityChanged": False,
                }
                handoff = True
                _cooperative_chunk_telemetry(
                    slate=slate,
                    stage="COMPLETE",
                    remaining_seconds=remaining,
                    game_index=game_index,
                    phase=phase,
                    status="COMPLETE_LEASE_HANDOFF",
                )
                return result
            finally:
                if not handoff:
                    _cooperative_chunk_telemetry(
                        slate=slate,
                        stage="RELEASE_MUTATION_LEASE",
                        remaining_seconds=(
                            _cooperative_chunk_remaining_seconds(context)
                        ),
                        game_index=game_index,
                        phase=phase,
                    )
                    try:
                        released = release(module, lease)
                    except BaseException as exc:
                        stage = "RELEASE_MUTATION_LEASE"
                        raise RuntimeError(
                            "COOPERATIVE_TERMINAL_CHUNK_"
                            "MUTATION_LEASE_RELEASE_FAILED"
                        ) from exc
                    if released is not True:
                        stage = "RELEASE_MUTATION_LEASE"
                        raise RuntimeError(
                            "COOPERATIVE_TERMINAL_CHUNK_"
                            "MUTATION_LEASE_RELEASE_AMBIGUOUS"
                        )

        identity = identities[game_index]
        if remaining < COOPERATIVE_TERMINAL_CHUNK_GAME_MIN_REMAINING_SECONDS:
            return _cooperative_terminal_deferred(
                current_checkpoint,
                slate=slate,
                stage="GAME_BUDGET",
                remaining_seconds=remaining,
                now=module._now_utc().astimezone(timezone.utc),
                game_index=game_index,
                game_identity=identity,
            )
        if phase == "PROCESS":
            start = patch._start(module, manifest[game_index])
            if start is None or now < start:
                return _cooperative_terminal_failure(
                    current_checkpoint,
                    slate=slate,
                    stage="VERIFY_GAME_STARTED",
                    remaining_seconds=remaining,
                    now=module._now_utc().astimezone(timezone.utc),
                    error_code="GAME_NOT_STARTED",
                    game_index=game_index,
                    game_identity=identity,
                )

        acquire = getattr(patch, "_acquire_lock_execution_lease", None)
        release = getattr(patch, "_release_lock_execution_lease", None)
        if (
            not callable(acquire)
            or not callable(release)
            or getattr(patch, "LOCK_EXECUTION_LEASE_VERSION", None)
            != COOPERATIVE_TERMINAL_WRITER_LEASE_VERSION
        ):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_WRITER_LEASE_NOT_READY"
            )

        stage = "ACQUIRE_MUTATION_LEASE"
        _cooperative_chunk_telemetry(
            slate=slate,
            stage=stage,
            remaining_seconds=_cooperative_chunk_remaining_seconds(context),
            game_index=game_index,
            game_identity=identity,
            phase=phase,
        )
        lease = acquire(
            module,
            slate,
            module._now_utc().astimezone(timezone.utc),
        )
        if not isinstance(lease, dict):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_WRITER_LEASE_RESULT_INVALID"
            )
        if lease.get("acquired") is not True:
            if lease.get("acquired") is not False:
                raise RuntimeError(
                    "COOPERATIVE_TERMINAL_CHUNK_WRITER_LEASE_RESULT_INVALID"
                )
            return _cooperative_terminal_deferred(
                current_checkpoint,
                slate=slate,
                stage="MUTATION_LEASE_CONTENDED",
                remaining_seconds=_cooperative_chunk_remaining_seconds(
                    context
                ),
                now=module._now_utc().astimezone(timezone.utc),
                game_index=game_index,
                game_identity=identity,
                status="DEFERRED_MUTATION_LEASE_CONTENDED",
                error_code="WRITER_LEASE_CONTENDED",
            )

        result: Dict[str, Any]
        try:
            result = _execute_cooperative_terminal_target(
                module,
                patch,
                slate=slate,
                pulls=pulls,
                manifest=manifest,
                selected_manifest_authority=selected_manifest_authority,
                manifest_authority=manifest_authority,
                identities=identities,
                identity_options=identity_options,
                checkpoint=current_checkpoint,
                context=context,
            )
        finally:
            stage = "RELEASE_MUTATION_LEASE"
            _cooperative_chunk_telemetry(
                slate=slate,
                stage=stage,
                remaining_seconds=_cooperative_chunk_remaining_seconds(
                    context
                ),
                game_index=game_index,
                game_identity=identity,
                phase=phase,
            )
            try:
                released = release(module, lease)
            except BaseException as exc:
                raise RuntimeError(
                    "COOPERATIVE_TERMINAL_CHUNK_"
                    "MUTATION_LEASE_RELEASE_FAILED"
                ) from exc
            if released is not True:
                raise RuntimeError(
                    "COOPERATIVE_TERMINAL_CHUNK_"
                    "MUTATION_LEASE_RELEASE_AMBIGUOUS"
                )

        return result
    except BaseException as exc:
        return _cooperative_terminal_failure(
            current_checkpoint,
            slate=slate,
            stage=stage,
            remaining_seconds=_cooperative_chunk_remaining_seconds(context),
            now=module._now_utc().astimezone(timezone.utc),
            error_code=_cooperative_terminal_exception_code(stage, exc),
            game_index=game_index,
            game_identity=identity,
        )


def _run_cooperative_terminal_chunk(
    module: Any,
    patch: Any,
    *,
    slate_date: str,
    request_epoch: Any,
    request_id: Any,
    checkpoint: Optional[Dict[str, Any]] = None,
    context: Any,
) -> Dict[str, Any]:
    # Cache only canonicalized pull material. Deliberately omit consistentItems:
    # this path may create one terminal row and strong readback must never replay
    # a cached pre-write absence. Bound the separate candidate alias fanout.
    status_cache = getattr(patch, "_STATUS_READ_CACHE", None)
    alias_limit = getattr(
        patch,
        "_COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_LIMIT",
        None,
    )
    status_token = None
    alias_token = None
    if (
        status_cache is not None
        and callable(getattr(status_cache, "set", None))
        and callable(getattr(status_cache, "reset", None))
    ):
        status_token = status_cache.set({"canonicalPulls": {}})
    if (
        alias_limit is not None
        and callable(getattr(alias_limit, "set", None))
        and callable(getattr(alias_limit, "reset", None))
    ):
        alias_token = alias_limit.set(
            COOPERATIVE_TERMINAL_CANDIDATE_ALIAS_QUERY_LIMIT
        )
    else:
        if status_token is not None:
            status_cache.reset(status_token)
        return _cooperative_terminal_failure(
            None,
            slate=str(slate_date or ""),
            stage="INSTALL_CANDIDATE_ALIAS_BOUND",
            remaining_seconds=_cooperative_chunk_remaining_seconds(context),
            now=module._now_utc().astimezone(timezone.utc),
            error_code="CANDIDATE_ALIAS_BOUND_NOT_READY",
        )
    try:
        return _run_cooperative_terminal_chunk_impl(
            module,
            patch,
            slate_date=slate_date,
            request_epoch=request_epoch,
            request_id=request_id,
            checkpoint=checkpoint,
            context=context,
        )
    finally:
        if alias_token is not None:
            alias_limit.reset(alias_token)
        if status_token is not None:
            status_cache.reset(status_token)


def install_prospective_row_repair(module: Any, patch: Any) -> Any:
    if getattr(module, _RUNTIME_PATCH_FLAG, False):
        return module
    original = getattr(module, "run_lock", None)
    if not callable(original):
        return module

    @functools.wraps(original)
    def run_lock(
        slate_date: Optional[str] = None,
        force: bool = False,
        *,
        scheduled: bool = False,
    ) -> Dict[str, Any]:
        result = original(
            slate_date=slate_date,
            force=force,
            scheduled=scheduled,
        )
        if not isinstance(result, dict):
            return result
        if _missed_count_from_result(result) <= 0:
            return result
        slate = str(
            slate_date or result.get("slateDateEt") or module._today_et()
        )
        reason = str(result.get("reason") or "")
        if reason == _RAW_MISSED_REASON:
            result = _ensure_missed_lock_diagnostics(
                module, patch, slate, result, force
            )
            # Only the explicit protected reconciliation invocation is both
            # forced and method-less/scheduled. Ordinary scheduled processing
            # and manual force probes remain fail closed.
            if not (force and scheduled):
                return result
        elif reason not in _POST_WINDOW_REPAIR_REASONS:
            return result
        return _attach_repair(
            result,
            _repair_proven_no_prediction_misses(module, patch, slate),
        )

    module.run_lock = run_lock
    module.run_cooperative_terminal_chunk = functools.partial(
        _run_cooperative_terminal_chunk,
        module,
        patch,
    )
    module.validate_cooperative_terminal_completion_checkpoint = (
        _validated_cooperative_terminal_complete_checkpoint
    )
    module.validate_cooperative_terminal_completion_handoff = (
        functools.partial(
            _validate_cooperative_terminal_completion_handoff,
            module,
            patch,
        )
    )
    module.release_cooperative_terminal_completion_lease = (
        functools.partial(
            _release_cooperative_terminal_completion_lease,
            module,
            patch,
        )
    )
    module.MLB_COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION = (
        COOPERATIVE_TERMINAL_COMPLETION_HANDOFF_VERSION
    )
    module.MLB_COOPERATIVE_TERMINAL_CHUNK_VERSION = (
        COOPERATIVE_TERMINAL_CHUNK_VERSION
    )
    module.MLB_MISSED_LOCK_TERMINAL_RECONCILIATION_VERSION = (
        MISSED_LOCK_TERMINAL_RECONCILIATION_VERSION
    )
    module.MLB_PROMOTED_LOCK_TRAINING_ELIGIBILITY_VERSION = (
        PROMOTED_LOCK_TRAINING_ELIGIBILITY_VERSION
    )
    setattr(module, _RUNTIME_PATCH_FLAG, True)
    return module


def install() -> None:
    patch = sys.modules.get("mlb_daily_per_game_lock_patch")
    if patch is None or getattr(patch, _APPLY_HOOK_FLAG, False):
        return
    original = getattr(patch, "apply", None)
    if not callable(original):
        return
    _install_prepare_row_training_cleanup(patch)

    @functools.wraps(original)
    def apply(module: Any) -> Any:
        return install_prospective_row_repair(original(module), patch)

    patch.apply = apply
    setattr(patch, _APPLY_HOOK_FLAG, True)

    module = sys.modules.get("mlb_daily_pick_lock")
    if module is not None and getattr(
        module, "_INQSI_MLB_DAILY_PER_GAME_LOCK_V1", False
    ):
        install_prospective_row_repair(module, patch)
