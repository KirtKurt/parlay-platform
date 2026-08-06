from __future__ import annotations

import copy
import functools
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


MISSED_LOCK_TERMINAL_RECONCILIATION_VERSION = (
    "MLB-MISSED-LOCK-TERMINAL-RECONCILIATION-v1-proven-no-prelock-candidate"
)
PROMOTED_LOCK_TRAINING_ELIGIBILITY_VERSION = (
    "MLB-PROMOTED-LOCK-TRAINING-ELIGIBILITY-v1-expired-prelock-state-cleared"
)
_RUNTIME_PATCH_FLAG = "_INQSI_MLB_MISSED_LOCK_TERMINAL_RECONCILIATION_V1"
_APPLY_HOOK_FLAG = "_INQSI_MLB_MISSED_LOCK_TERMINAL_APPLY_HOOK_V1"
_PREPARE_ROW_HOOK_FLAG = "_INQSI_MLB_PROMOTED_LOCK_TRAINING_ELIGIBILITY_V1"
EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS = frozenset(
    {
        "immutable_tminus45_prediction_not_available",
        "incomplete_slate_coverage",
    }
)


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
    """Remove only pre-lock exclusions made false by a verified T-45 lock."""

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
    if not (
        out.get("lockedPrediction") is True
        and out.get("immutablePerGameStage") is True
        and out.get("exactVectorVerified") is True
        and not exact_errors
    ):
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
    if not cleared:
        return out
    remaining = sorted(reasons - EXPIRED_PRELOCK_ONLY_TRAINING_EXCLUSIONS)
    eligible = not remaining
    metadata = {
        "trainingEligible": eligible,
        "trainingExclusionReasons": remaining,
        "expiredPrelockTrainingExclusionsCleared": cleared,
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
    out = copy.deepcopy(result)
    out["missedLockTerminalReconciliation"] = copy.deepcopy(report)
    progress = report.get("progressAfter")
    if not isinstance(progress, dict):
        return out
    remaining = _int(progress.get("missedCount"), 0)
    due = _int(progress.get("dueMissingCount"), 0)
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
    if report.get("ok") is True and remaining == 0 and due == 0:
        out.update(
            {
                "ok": True,
                "reason": "PROVEN_NO_PREDICTION_TERMINALS_RECONCILED",
                "skipped": False,
                "postStartPredictionCreationAllowed": False,
            }
        )
        out.pop("failClosed", None)
    else:
        out.update(
            {
                "ok": False,
                "reason": "MISSED_PER_GAME_LOCK_NOT_TERMINALIZED",
                "failClosed": True,
                "postStartPredictionCreationAllowed": False,
            }
        )
    return out


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
        if not isinstance(result, dict) or _missed_count_from_result(result) <= 0:
            return result
        slate = str(
            slate_date or result.get("slateDateEt") or module._today_et()
        )
        return _attach_repair(
            result,
            _repair_proven_no_prediction_misses(module, patch, slate),
        )

    module.run_lock = run_lock
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
