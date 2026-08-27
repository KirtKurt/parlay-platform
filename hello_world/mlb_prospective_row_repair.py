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
    "MLB-COOPERATIVE-TERMINAL-CHUNK-v1-one-game-per-eventbridge-owner"
)
# A canonical owner is admitted with at least 660 seconds left.  Each chunk
# consumes at most one manifest game and stops admitting new work well before
# Lambda's hard timeout so the owner can durably checkpoint and release leases.
COOPERATIVE_TERMINAL_CHUNK_INITIAL_MIN_REMAINING_SECONDS = 300
COOPERATIVE_TERMINAL_CHUNK_GAME_MIN_REMAINING_SECONDS = 180
COOPERATIVE_TERMINAL_CHUNK_WRITE_MIN_REMAINING_SECONDS = 120
COOPERATIVE_TERMINAL_CHUNK_COMPLETION_MIN_REMAINING_SECONDS = 90
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
    if error_code:
        payload["errorCode"] = str(error_code)[:160]
    print(json.dumps(payload, sort_keys=True))


def _strict_chunk_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"COOPERATIVE_TERMINAL_CHUNK_{field.upper()}_INVALID")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"COOPERATIVE_TERMINAL_CHUNK_{field.upper()}_INVALID"
        ) from exc
    if parsed < 0:
        raise RuntimeError(f"COOPERATIVE_TERMINAL_CHUNK_{field.upper()}_INVALID")
    return parsed


def _cooperative_terminal_manifest_fingerprint(
    module: Any,
    patch: Any,
    manifest: List[Dict[str, Any]],
) -> str:
    fingerprint = getattr(patch, "_post_window_manifest_fingerprint", None)
    if callable(fingerprint):
        value = str(fingerprint(module, manifest) or "").strip()
        if value:
            return value
    material = sorted(
        (
            str(patch.game_identity(game) or ""),
            (
                patch._start(module, game).isoformat()
                if patch._start(module, game) is not None
                else ""
            ),
        )
        for game in manifest
    )
    return hashlib.sha256(
        json.dumps(material, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validated_cooperative_terminal_checkpoint(
    checkpoint: Optional[Dict[str, Any]],
    *,
    slate: str,
    manifest_fingerprint: str,
    identities: List[str],
) -> Dict[str, Any]:
    if checkpoint is None:
        raw: Dict[str, Any] = {}
    elif isinstance(checkpoint, dict):
        raw = copy.deepcopy(checkpoint)
    else:
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_CHECKPOINT_NOT_OBJECT")

    if not raw:
        return {
            "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "slateDateEt": slate,
            "manifestFingerprint": manifest_fingerprint,
            "manifestGameCount": len(identities),
            "nextGameIndex": 0,
            "processedGameCount": 0,
            "terminalCount": 0,
            "canonicalCount": 0,
            "noPredictionDataCount": 0,
            "reconciledCount": 0,
            "processedGames": [],
            "attemptCount": 0,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "productionAuthorityChanged": False,
        }

    if (
        raw.get("version") != COOPERATIVE_TERMINAL_CHUNK_VERSION
        or str(raw.get("slateDateEt") or "") != slate
        or str(raw.get("manifestFingerprint") or "") != manifest_fingerprint
        or _strict_chunk_integer(
            raw.get("manifestGameCount"), "manifest_game_count"
        )
        != len(identities)
        or raw.get("postStartPredictionCreationAllowed") is not False
        or raw.get("immutablePredictionRewriteAllowed") is not False
        or raw.get("productionAuthorityChanged") is not False
    ):
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_CHECKPOINT_IDENTITY_INVALID")

    next_index = _strict_chunk_integer(
        raw.get("nextGameIndex"), "next_game_index"
    )
    if next_index > len(identities):
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_CURSOR_OUT_OF_RANGE")
    processed = raw.get("processedGames")
    if not isinstance(processed, list) or len(processed) != next_index:
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_PROCESSED_GAMES_INVALID")

    normalized_games: List[Dict[str, Any]] = []
    for index, entry in enumerate(processed):
        if not isinstance(entry, dict):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_PROCESSED_GAME_NOT_OBJECT"
            )
        identity = str(entry.get("gameIdentity") or "")
        terminal_state = str(entry.get("terminalState") or "")
        if (
            identity != identities[index]
            or terminal_state not in _TERMINAL_CHUNK_STATES
            or not isinstance(entry.get("reconciled"), bool)
        ):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_PROCESSED_GAME_INVALID"
            )
        normalized_games.append(
            {
                "gameIdentity": identity,
                "terminalState": terminal_state,
                "reconciled": entry["reconciled"],
            }
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
    }
    for field, expected in expected_counts.items():
        if _strict_chunk_integer(raw.get(field), field) != expected:
            raise RuntimeError(
                f"COOPERATIVE_TERMINAL_CHUNK_{field.upper()}_MISMATCH"
            )

    normalized = {
        "version": COOPERATIVE_TERMINAL_CHUNK_VERSION,
        "slateDateEt": slate,
        "manifestFingerprint": manifest_fingerprint,
        "manifestGameCount": len(identities),
        "nextGameIndex": next_index,
        **expected_counts,
        "processedGames": normalized_games,
        "attemptCount": _strict_chunk_integer(
            raw.get("attemptCount", 0), "attempt_count"
        ),
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "productionAuthorityChanged": False,
    }
    if isinstance(raw.get("lastAttempt"), dict):
        normalized["lastAttempt"] = copy.deepcopy(raw["lastAttempt"])
    return normalized


def _cooperative_terminal_attempt_checkpoint(
    checkpoint: Dict[str, Any],
    *,
    now: datetime,
    stage: str,
    status: str,
    game_index: Optional[int],
    game_identity: Optional[str],
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
    }
    if game_index is not None:
        attempt["gameIndex"] = game_index
    if game_identity:
        attempt["gameIdentity"] = str(game_identity)[:200]
    if error_code:
        attempt["errorCode"] = str(error_code)[:160]
    out["lastAttempt"] = attempt
    out["updatedAtUtc"] = now.isoformat()
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
) -> Dict[str, Any]:
    writable = isinstance(checkpoint, dict)
    updated = (
        _cooperative_terminal_attempt_checkpoint(
            checkpoint,
            now=now,
            stage=stage,
            status="DEFERRED_INSUFFICIENT_REMAINING_TIME",
            game_index=game_index,
            game_identity=game_identity,
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
        status="DEFERRED_INSUFFICIENT_REMAINING_TIME",
    )
    return {
        "ok": True,
        "complete": False,
        "deferred": True,
        "stage": stage,
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


def _cooperative_terminal_completion_response(
    checkpoint: Dict[str, Any],
) -> Dict[str, Any]:
    manifest_count = _strict_chunk_integer(
        checkpoint.get("manifestGameCount"), "manifest_game_count"
    )
    terminal_count = _strict_chunk_integer(
        checkpoint.get("terminalCount"), "terminal_count"
    )
    if (
        manifest_count <= 0
        or terminal_count != manifest_count
        or _strict_chunk_integer(
            checkpoint.get("nextGameIndex"), "next_game_index"
        )
        != manifest_count
    ):
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_NOT_COMPLETE")
    canonical_count = _strict_chunk_integer(
        checkpoint.get("canonicalCount"), "canonical_count"
    )
    no_prediction_count = _strict_chunk_integer(
        checkpoint.get("noPredictionDataCount"), "no_prediction_data_count"
    )
    reconciled_count = _strict_chunk_integer(
        checkpoint.get("reconciledCount"), "reconciled_count"
    )
    if canonical_count + no_prediction_count != manifest_count:
        raise RuntimeError("COOPERATIVE_TERMINAL_CHUNK_TERMINAL_COUNT_MISMATCH")
    progress = {
        "manifestGameCount": manifest_count,
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
        "reconciledCount": reconciled_count,
        "remainingMissedCount": 0,
        "unresolved": [],
        "progressAfter": copy.deepcopy(progress),
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
        "manifestGameCount": manifest_count,
        "lockOutcomeCount": terminal_count,
        "missedGameCount": 0,
        "lockStatusComplete": True,
        "dailyCardComplete": True,
        "perGameLockProgress": progress,
        "missedLockTerminalReconciliation": repair,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
    }


def _run_cooperative_terminal_chunk(
    module: Any,
    patch: Any,
    *,
    slate_date: str,
    checkpoint: Optional[Dict[str, Any]] = None,
    context: Any,
) -> Dict[str, Any]:
    """Process at most one historical terminal game for one EventBridge owner."""

    slate = str(slate_date or "").strip()
    stage = "INITIAL_BUDGET"
    remaining = _cooperative_chunk_remaining_seconds(context)
    now = module._now_utc().astimezone(timezone.utc)
    current_checkpoint: Optional[Dict[str, Any]] = None
    game_index: Optional[int] = None
    identity: Optional[str] = None
    _cooperative_chunk_telemetry(
        slate=slate,
        stage=stage,
        remaining_seconds=remaining,
    )
    if remaining < COOPERATIVE_TERMINAL_CHUNK_INITIAL_MIN_REMAINING_SECONDS:
        return _cooperative_terminal_deferred(
            checkpoint if isinstance(checkpoint, dict) else None,
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
        identities = [str(patch.game_identity(game) or "") for game in manifest]
        if (
            not identities
            or any(not value for value in identities)
            or len(set(identities)) != len(identities)
        ):
            raise RuntimeError(
                "COOPERATIVE_TERMINAL_CHUNK_MANIFEST_IDENTITY_INVALID"
            )
        manifest_fingerprint = _cooperative_terminal_manifest_fingerprint(
            module,
            patch,
            manifest,
        )
        current_checkpoint = _validated_cooperative_terminal_checkpoint(
            checkpoint,
            slate=slate,
            manifest_fingerprint=manifest_fingerprint,
            identities=identities,
        )
        game_index = int(current_checkpoint["nextGameIndex"])
        remaining = _cooperative_chunk_remaining_seconds(context)
        _cooperative_chunk_telemetry(
            slate=slate,
            stage="MANIFEST_READY",
            remaining_seconds=remaining,
            game_index=game_index,
            status="READY",
        )

        if game_index == len(manifest):
            stage = "COMPLETE"
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
            response = _cooperative_terminal_completion_response(
                current_checkpoint
            )
            _cooperative_chunk_telemetry(
                slate=slate,
                stage=stage,
                remaining_seconds=remaining,
                game_index=game_index,
                status="COMPLETE",
            )
            return {
                "ok": True,
                "complete": True,
                "deferred": False,
                "stage": stage,
                "remainingSeconds": remaining,
                "checkpoint": current_checkpoint,
                "checkpointWriteAllowed": False,
                "terminalReplayResponse": response,
                "terminalChunkVersion": COOPERATIVE_TERMINAL_CHUNK_VERSION,
                "postStartPredictionCreationAllowed": False,
                "immutablePredictionRewriteAllowed": False,
                "productionAuthorityChanged": False,
            }

        game = manifest[game_index]
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
        start = patch._start(module, game)
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

        stage = "READ_STAGE"
        _cooperative_chunk_telemetry(
            slate=slate,
            stage=stage,
            remaining_seconds=_cooperative_chunk_remaining_seconds(context),
            game_index=game_index,
            game_identity=identity,
        )
        stored_stage = patch._get_stage(module, slate, game)
        reconciled = False
        if stored_stage:
            stage = "VALIDATE_STAGE"
            scoring = patch._scoring_pulls(module, pulls, game)
            stage_errors = list(
                patch._validate_stage(
                    module,
                    stored_stage,
                    slate,
                    game,
                    manifest,
                    scoring,
                )
                or []
            )
            if stage_errors:
                return _cooperative_terminal_failure(
                    current_checkpoint,
                    slate=slate,
                    stage=stage,
                    remaining_seconds=_cooperative_chunk_remaining_seconds(
                        context
                    ),
                    now=module._now_utc().astimezone(timezone.utc),
                    error_code="IMMUTABLE_STAGE_AUTHORITY_INVALID",
                    game_index=game_index,
                    game_identity=identity,
                )
            stage = "READ_CANONICAL"
            stage_row = copy.deepcopy(
                ((stored_stage.get("data") or {}).get("row")) or {}
            )
            canonical = patch._canonical_readback(module, stage_row)
            if not canonical:
                return _cooperative_terminal_failure(
                    current_checkpoint,
                    slate=slate,
                    stage=stage,
                    remaining_seconds=_cooperative_chunk_remaining_seconds(
                        context
                    ),
                    now=module._now_utc().astimezone(timezone.utc),
                    error_code="IMMUTABLE_CANONICAL_READBACK_MISSING",
                    game_index=game_index,
                    game_identity=identity,
                )
            terminal_state = "LOCKED_CANONICAL"
        else:
            stage = "READ_OUTCOME"
            outcome = patch._get_lock_outcome(module, slate, game)
            if outcome:
                terminal_state = "LOCKED_NO_PREDICTION_DATA"
            else:
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
                        current_checkpoint,
                        slate=slate,
                        stage=stage,
                        remaining_seconds=(
                            _cooperative_chunk_remaining_seconds(context)
                        ),
                        now=module._now_utc().astimezone(timezone.utc),
                        error_code="PRELOCK_CANDIDATE_REQUIRES_REVIEW",
                        game_index=game_index,
                        game_identity=identity,
                    )

                stage = "BIND_MANIFEST_AUTHORITY"
                authority = patch._select_provider_manifest_authority(
                    module,
                    pulls,
                    slate,
                    manifest,
                )
                remaining = _cooperative_chunk_remaining_seconds(context)
                if (
                    remaining
                    < COOPERATIVE_TERMINAL_CHUNK_WRITE_MIN_REMAINING_SECONDS
                ):
                    return _cooperative_terminal_deferred(
                        current_checkpoint,
                        slate=slate,
                        stage="WRITE_BUDGET",
                        remaining_seconds=remaining,
                        now=module._now_utc().astimezone(timezone.utc),
                        game_index=game_index,
                        game_identity=identity,
                    )
                stage = "WRITE_NO_PREDICTION_TERMINAL"
                _cooperative_chunk_telemetry(
                    slate=slate,
                    stage=stage,
                    remaining_seconds=remaining,
                    game_index=game_index,
                    game_identity=identity,
                )
                patch._put_no_prediction_outcome(
                    module,
                    slate,
                    game,
                    module._now_utc().astimezone(timezone.utc),
                    [
                        *(errors or []),
                        "POST_START_PROVEN_NO_PREGAME_PREDICTION_RECONCILIATION",
                    ],
                    authority,
                )
                stage = "READBACK_NO_PREDICTION_TERMINAL"
                outcome = patch._get_lock_outcome(module, slate, game)
                if (
                    not isinstance(outcome, dict)
                    or outcome.get("lock_status")
                    != "LOCKED_NO_PREDICTION_DATA"
                    or outcome.get("locked_prediction") is not False
                    or outcome.get("training_eligible") is not False
                ):
                    raise RuntimeError(
                        "COOPERATIVE_TERMINAL_CHUNK_OUTCOME_READBACK_INVALID"
                    )
                terminal_state = "LOCKED_NO_PREDICTION_DATA"
                reconciled = True

        stage = "CHECKPOINT_READY"
        advanced = copy.deepcopy(current_checkpoint)
        advanced["processedGames"] = [
            *advanced["processedGames"],
            {
                "gameIdentity": identity,
                "terminalState": terminal_state,
                "reconciled": reconciled,
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
        advanced = _cooperative_terminal_attempt_checkpoint(
            advanced,
            now=module._now_utc().astimezone(timezone.utc),
            stage=stage,
            status="TERMINAL_CHECKPOINT_READY",
            game_index=game_index,
            game_identity=identity,
        )
        remaining = _cooperative_chunk_remaining_seconds(context)
        _cooperative_chunk_telemetry(
            slate=slate,
            stage=stage,
            remaining_seconds=remaining,
            game_index=game_index,
            game_identity=identity,
            status="TERMINAL_CHECKPOINT_READY",
        )
        return {
            "ok": True,
            "complete": False,
            "deferred": False,
            "stage": stage,
            "remainingSeconds": remaining,
            "checkpoint": advanced,
            "checkpointWriteAllowed": True,
            "terminalChunkVersion": COOPERATIVE_TERMINAL_CHUNK_VERSION,
            "processedGameIdentity": identity,
            "processedTerminalState": terminal_state,
            "terminalWrittenThisInvocation": reconciled,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "productionAuthorityChanged": False,
        }
    except BaseException as exc:
        remaining = _cooperative_chunk_remaining_seconds(context)
        return _cooperative_terminal_failure(
            current_checkpoint,
            slate=slate,
            stage=stage,
            remaining_seconds=remaining,
            now=module._now_utc().astimezone(timezone.utc),
            error_code=f"{stage}_{type(exc).__name__}",
            game_index=game_index,
            game_identity=identity,
        )

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
