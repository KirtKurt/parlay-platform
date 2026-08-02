#!/usr/bin/env python3
"""Compatibility wrapper for the MLB trainer deploy verifier.

The full verifier implementation is preserved in
``verify_mlb_trainer_deploy_response_legacy``. This wrapper corrects the
manual-first shadow authority lifecycle without changing any other verifier
contract. A persisted shadow artifact is not live inference authority; runtime
activation must remain unavailable until manual approval installs a consumer or
enables live inference authority.
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
    """Return the persisted latest-run authority marker for one health domain."""
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
    """Validate activation availability while preserving manual-first safety.

    The deployed persisted-status response does not always repeat
    ``liveInferenceAuthority`` at the top level. In that exact schema, require
    both independently persisted training and selection-capture health records
    to prove shadow-only authority. Missing or unhealthy evidence remains
    fail-closed.
    """
    automatic_promotion_disabled = (
        status_after.get("automaticPromotionEnabled") is False
    )
    manual_first_required = (
        status_after.get("firstPromotionRequiresManualReview") is True
    )
    inference_consumer_absent = (
        status_after.get("v2InferenceConsumerInstalled") is False
    )

    top_level_live_authority = status_after.get("liveInferenceAuthority")
    if top_level_live_authority is False:
        live_authority_absent = True
    elif "liveInferenceAuthority" not in status_after:
        live_authority_absent = (
            _health_latest_live_authority(status_after, "trainingHealth") is False
            and _health_latest_live_authority(
                status_after, "selectionCaptureHealth"
            )
            is False
        )
    else:
        live_authority_absent = False

    activation_available = status_after.get("runtimeAuthorityActivationAvailable")
    shadow_manual_first = (
        automatic_promotion_disabled
        and manual_first_required
        and inference_consumer_absent
        and live_authority_absent
    )
    if shadow_manual_first:
        return (
            []
            if activation_available is False
            else [
                "runtime_authority_activation_must_remain_unavailable_before_manual_approval"
            ]
        )
    return (
        []
        if activation_available is True
        else ["runtime_authority_activation_not_available"]
    )


# Functions copied from the legacy module retain that module's global namespace,
# so patch the implementation there as well as exporting the corrected helper.
_legacy._runtime_authority_activation_errors = _runtime_authority_activation_errors


if __name__ == "__main__":
    raise SystemExit(_legacy.main())
