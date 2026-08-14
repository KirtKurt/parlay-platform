#!/usr/bin/env python3
"""Autonomous MLB trainer deployment verifier.

The full structural verifier remains in
``verify_mlb_trainer_deploy_response_legacy``. This wrapper replaces the old
manual-first shadow expectation with the intended MLB AUTO contract:

* gated automatic promotion is enabled;
* the first passing prospective champion does not require manual review;
* the V2 inference consumer is installed; and
* runtime activation is available only through immutable prospective,
  calibration, proper-scoring, and deployment-identity gates.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Keep deployment-identity literals visible in the executable verifier source.
# The deployment stabilizer intentionally scans this file before importing it.
TRAINER_VERSION = "MLB-ML-AWS-TRAINING-v1-persisted-cutover-selection-ledger-shadow"
EXPERIMENT_ID = "mlb-v2-2026-08-03-future-prospective-r7"
RELEASE_CUTOFF_UTC = "2026-08-03T04:00:00+00:00"

try:
    from scripts import verify_mlb_trainer_deploy_response_legacy as _legacy
except ImportError:  # Direct execution from the scripts directory.
    import verify_mlb_trainer_deploy_response_legacy as _legacy


for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


def _health_latest_live_authority(status_after: Dict[str, Any], key: str) -> Any:
    health = status_after.get(key)
    if not isinstance(health, dict) or health.get("ok") is not True:
        return None
    latest = health.get("latestRun")
    if not isinstance(latest, dict) or latest.get("ok") is not True:
        return None
    return latest.get("liveInferenceAuthority")


def _runtime_authority_activation_errors(
    status_after: Dict[str, Any],
) -> List[str]:
    errors: List[str] = []
    if status_after.get("automaticPromotionEnabled") is not True:
        errors.append("automatic_promotion_not_enabled")
    if status_after.get("firstPromotionRequiresManualReview") is not False:
        errors.append("first_promotion_still_requires_manual_review")
    if status_after.get("v2InferenceConsumerInstalled") is not True:
        errors.append("v2_inference_consumer_not_installed")
    if status_after.get("runtimeAuthorityActivationAvailable") is not True:
        errors.append("runtime_authority_activation_not_available")
    if status_after.get("learningContinuesBelowAspirationalAccuracy") is not True:
        errors.append("learning_still_bound_to_aspirational_accuracy")
    if status_after.get("aspirationalAccuracyBlocksTraining") is not False:
        errors.append("aspirational_accuracy_still_blocks_training")
    if status_after.get("aspirationalAccuracyBlocksCandidateEvaluation") is not False:
        errors.append("aspirational_accuracy_still_blocks_candidate_evaluation")
    if status_after.get("aspirationalAccuracyBlocksPlayableAuthority") is not True:
        errors.append("aspirational_accuracy_does_not_block_playable_authority")

    consumer = status_after.get("v2InferenceConsumer") or {}
    if not isinstance(consumer, dict) or consumer.get("installed") is not True:
        errors.append("v2_inference_consumer_contract_missing")
    elif consumer.get("automaticWagerAllowed") is not False:
        errors.append("v2_consumer_automatic_wager_contract_invalid")

    # Before a champion passes the gate, persisted training and capture runs
    # remain shadow/no-authority. That is expected and distinct from the
    # availability of the automatic activation path itself.
    for key in ("trainingHealth", "selectionCaptureHealth"):
        marker = _health_latest_live_authority(status_after, key)
        if marker not in {False, True}:
            errors.append(f"{key}:live_inference_authority_marker_missing")
    return errors


# Functions copied from the legacy module retain that module's global namespace,
# so patch the implementation there as well as exporting the corrected helper.
_legacy._runtime_authority_activation_errors = _runtime_authority_activation_errors


if __name__ == "__main__":
    raise SystemExit(_legacy.main())
