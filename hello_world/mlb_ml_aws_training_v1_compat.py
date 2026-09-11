"""Fail-closed compatibility handler for the canonical MLB AWS trainer.

The canonical implementation remains in ``mlb_ml_aws_training_v1.py``. This
uniquely named Lambda entrypoint installs narrow, production-safe
normalizations without changing model math, labels, locks, promotion gates, or
serving authority.

In addition to the existing continuity/read repairs, status heartbeats carry a
deterministic MLB-specific implementation/runtime identity. Health therefore
survives unrelated repository deployments only when the MLB executable and its
runtime contract are byte-for-byte/field-for-field equivalent. Missing or
mismatched MLB identity remains fail-closed.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from collections.abc import Mapping
from functools import wraps
from pathlib import Path
from typing import Any, Dict

import mlb_prospective_trainer_read_repair as prospective_trainer_read_repair
import mlb_r7_source_honest_training_repair as r7_source_honest_training_repair
import mlb_r7_historical_walkforward_bridge as r7_historical_walkforward_bridge


COMPAT_VERSION = (
    "MLB-TRAINER-CANONICAL-CONTINUITY-WAIT-v8-mlb-specific-deployment-identity"
)
MLB_IDENTITY_VERSION = "MLB-TRAINER-IMPLEMENTATION-IDENTITY-v1"
_BASE_MODULE_NAME = "_inqsi_mlb_ml_aws_training_v1_canonical"
_BASE_PATH = Path(__file__).resolve().with_name("mlb_ml_aws_training_v1.py")
_IDENTITY_SOURCE_FILES = (
    "mlb_ml_aws_training_v1.py",
    "mlb_ml_aws_training_v1_compat.py",
    "mlb_ml_dual_model_v2.py",
    "mlb_ml_experiment_v2.py",
    "mlb_ml_promotion_policy_v2.py",
    "mlb_prospective_trainer_read_repair.py",
    "mlb_r7_source_honest_training_repair.py",
    "mlb_r7_historical_walkforward_bridge.py",
    "mlb_successor_runtime_v1.py",
)


def _load_canonical_module():
    existing = sys.modules.get(_BASE_MODULE_NAME)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(_BASE_MODULE_NAME, _BASE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("mlb_trainer_canonical_module_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_BASE_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_BASE_MODULE_NAME, None)
        raise
    return module


def _stable_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _source_manifest() -> Dict[str, str]:
    root = Path(__file__).resolve().parent
    result: Dict[str, str] = {}
    for name in _IDENTITY_SOURCE_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("mlb_identity_source_missing:" + name)
        body = path.read_bytes()
        if not body:
            raise RuntimeError("mlb_identity_source_empty:" + name)
        result[name] = hashlib.sha256(body).hexdigest()
    return result


def mlb_deployment_identity(config: Any) -> Dict[str, Any]:
    """Return deterministic MLB-only executable + runtime-contract identity.

    Global Git/template hashes remain stored for audit, but are not sufficient
    proof of MLB incompatibility because unrelated sports share the repository
    and SAM template. Any missing source or required runtime field fails closed.
    """
    sources = _source_manifest()
    implementation = {
        "identityVersion": MLB_IDENTITY_VERSION,
        "sourceSha256": sources,
    }
    runtime_contract = {
        "trainerVersion": canonical.VERSION,
        "compatVersion": COMPAT_VERSION,
        "experimentId": str(config.experiment_id),
        "releaseContractId": str(config.release_contract_id),
        "releaseCutoffUtc": str(config.release_cutoff_utc),
        "featureVectorVersion": str(config.feature_vector_version),
        "artifactsBucket": str(config.artifacts_bucket),
        "automaticPromotionEnabled": bool(config.automatic_promotion_enabled),
        "executionLeaseVersion": canonical.EXECUTION_LEASE_VERSION,
        "executionLeaseSeconds": int(canonical.EXECUTION_LEASE_SECONDS),
        "statusFingerprintVersion": canonical.STATUS_FINGERPRINT_VERSION,
        "trainingStatusMaxAgeSeconds": int(canonical.TRAINING_STATUS_MAX_AGE.total_seconds()),
        "selectionCaptureStatusMaxAgeSeconds": int(canonical.SELECTION_CAPTURE_STATUS_MAX_AGE.total_seconds()),
        "handler": "mlb_ml_aws_training_v1_compat.lambda_handler",
    }
    return {
        **implementation,
        "implementationSha256": _stable_sha256(implementation),
        "runtimeContractSha256": _stable_sha256(runtime_contract),
    }


def normalize_canonical_continuity_wait(payload: Mapping[str, Any]) -> Dict[str, Any]:
    value = dict(payload)
    continuity = value.get("canonicalSlateContinuity")
    milestones = value.get("milestones")
    expected_wait = (
        value.get("ok") is False
        and value.get("status") == "CANONICAL_SLATE_CONTINUITY_BLOCKED"
        and value.get("executionMode") == "training"
        and value.get("modelTrained") is not True
        and value.get("championChanged") is not True
        and value.get("liveInferenceAuthority") is not True
        and value.get("productionAuthorityChanged") is not True
        and isinstance(continuity, Mapping)
        and continuity.get("ok") is not True
        and (
            not isinstance(milestones, Mapping)
            or milestones.get("canonicalContinuityReady") is not True
        )
    )
    if not expected_wait:
        return value
    value.update(
        {
            "ok": True,
            "status": "WAITING_FOR_CANONICAL_SLATE_CONTINUITY",
            "trainingReady": False,
            "waiting": True,
            "waitReason": "canonical_slate_continuity",
            "modelTrained": False,
            "championChanged": False,
            "liveInferenceAuthority": False,
            "automaticPromotionEnabled": False,
            "productionAuthorityChanged": False,
            "continuityWaitCompatibilityVersion": COMPAT_VERSION,
        }
    )
    return value


def persisted_run_response(service: Any, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the immutable stored run when it can be proven to be the same run."""
    normalized = normalize_canonical_continuity_wait(payload)
    run_id = str(normalized.get("runId") or "").strip()
    config = getattr(service, "config", None)
    experiment_id = str(getattr(config, "experiment_id", "") or "").strip()
    store = getattr(service, "store", None)
    loader = getattr(store, "load_status_run", None)
    if not run_id or not experiment_id or not callable(loader):
        return normalized
    try:
        persisted = loader(experiment_id, run_id)
    except Exception:
        return normalized
    if not isinstance(persisted, Mapping):
        return normalized
    persisted_value = normalize_canonical_continuity_wait(persisted)
    if str(persisted_value.get("runId") or "").strip() != run_id:
        return normalized
    return dict(persisted_value)


canonical = _load_canonical_module()
prospective_trainer_read_repair.install()
r7_source_honest_training_repair.install(
    experiment=canonical.experiment,
    dual_model=canonical.dual_model,
)
r7_historical_walkforward_bridge.install(
    canonical=canonical,
    experiment=canonical.experiment,
)


# Persist the MLB-specific identity before the canonical status fingerprint is
# computed. This deliberately mirrors the canonical writer and changes only the
# deploymentIdentity payload plus the already-established continuity wait.
def _save_run_status_with_mlb_identity(self, payload):
    payload = normalize_canonical_continuity_wait(payload)
    manifest = self.store.load_manifest(self.config.experiment_id)
    result = {
        **payload,
        "version": canonical.VERSION,
        "experimentId": self.config.experiment_id,
        "releaseCutoffUtc": self._normalized_release_cutoff(),
        "createdAtUtc": self.now().isoformat(),
        "manifestDigest": (manifest or {}).get("manifestDigest"),
        "deploymentIdentity": {
            "gitSha": self.config.deployment_git_sha,
            "templateSha256": self.config.deployment_template_sha256,
            "mlbIdentity": mlb_deployment_identity(self.config),
        },
    }
    invocation_run = getattr(self, "_invocation_run", None)
    if invocation_run:
        result.setdefault("requestRun", invocation_run)
    if (
        str(result.get("executionMode") or "").strip().lower() == "training"
        and self._selection_capture_before_training is not None
    ):
        result.setdefault(
            "selectionCaptureBeforeTraining",
            copy.deepcopy(self._selection_capture_before_training),
        )
    result.setdefault("automaticPromotionEnabled", self.config.automatic_promotion_enabled)
    result.setdefault(
        "executionConcurrencyControl",
        canonical.execution_concurrency_control(
            acquired_for_run=self._execution_lease_acquired_for_run
        ),
    )
    result.setdefault("championChanged", False)
    result.setdefault("liveInferenceAuthority", False)
    result.setdefault("productionAuthorityChanged", False)
    result.setdefault("immutablePredictionRewriteAllowed", False)
    result.setdefault("postStartPredictionCreationAllowed", False)
    result.setdefault("otherSportChanged", False)
    result.setdefault(
        "runId",
        canonical._sha256(
            {
                "experimentId": self.config.experiment_id,
                "createdAtUtc": result["createdAtUtc"],
                "status": result.get("status"),
            }
        )[:24],
    )
    result["statusFingerprintVersion"] = canonical.STATUS_FINGERPRINT_VERSION
    result["statusFingerprint"] = canonical._status_fingerprint(result)
    self.store.save_status(self.config.experiment_id, result)
    return result


_original_latest_status_health = canonical.TrainingService._latest_status_health


def _latest_status_health_with_mlb_identity(
    self, latest, *, execution_mode, maximum_age, manifest
):
    result = _original_latest_status_health(
        self,
        latest,
        execution_mode=execution_mode,
        maximum_age=maximum_age,
        manifest=manifest,
    )
    global_match = bool(result.get("deploymentIdentityMatches"))
    result["globalDeploymentIdentityMatches"] = global_match
    if not latest:
        result["mlbDeploymentIdentityMatches"] = False
        return result
    deployment = latest.get("deploymentIdentity") or {}
    observed = deployment.get("mlbIdentity")
    if not isinstance(observed, Mapping):
        errors = list(result.get("errors") or [])
        if "latest_status_mlb_identity_missing" not in errors:
            errors.append("latest_status_mlb_identity_missing")
        result.update(
            ok=False,
            deploymentIdentityMatches=False,
            mlbDeploymentIdentityMatches=False,
            errors=errors,
        )
        return result
    try:
        expected = mlb_deployment_identity(self.config)
    except Exception:
        errors = list(result.get("errors") or [])
        if "latest_status_mlb_identity_unavailable" not in errors:
            errors.append("latest_status_mlb_identity_unavailable")
        result.update(
            ok=False,
            deploymentIdentityMatches=False,
            mlbDeploymentIdentityMatches=False,
            errors=errors,
        )
        return result
    mlb_match = dict(observed) == expected
    errors = list(result.get("errors") or [])
    if mlb_match:
        errors = [e for e in errors if e != "latest_status_deployment_identity_mismatch"]
    else:
        if "latest_status_mlb_identity_mismatch" not in errors:
            errors.append("latest_status_mlb_identity_mismatch")
    result.update(
        ok=not errors,
        deploymentIdentityMatches=mlb_match,
        mlbDeploymentIdentityMatches=mlb_match,
        errors=errors,
    )
    return result


canonical.TrainingService._save_run_status = _save_run_status_with_mlb_identity
canonical.TrainingService._latest_status_health = _latest_status_health_with_mlb_identity

_original_run_scheduled = canonical.TrainingService.run_scheduled
if not getattr(_original_run_scheduled, "_mlb_unique_continuity_return_patch", False):

    @wraps(_original_run_scheduled)
    def _run_scheduled_with_continuity_wait(self, *args, **kwargs):
        result = _original_run_scheduled(self, *args, **kwargs)
        if not isinstance(result, Mapping):
            return result
        return persisted_run_response(self, result)

    _run_scheduled_with_continuity_wait._mlb_unique_continuity_return_patch = True
    _run_scheduled_with_continuity_wait._mlb_unique_continuity_return_version = (
        COMPAT_VERSION
    )
    canonical.TrainingService.run_scheduled = _run_scheduled_with_continuity_wait


def lambda_handler(event, context):
    return canonical.lambda_handler(event, context)
