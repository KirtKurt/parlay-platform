"""Fail-closed compatibility handler for the canonical MLB AWS trainer.

The canonical implementation remains in ``mlb_ml_aws_training_v1.py``. This
uniquely named Lambda entrypoint installs three narrow normalizations:

* unresolved canonical-slate continuity is represented as a healthy,
  non-authoritative wait at both persistence and return boundaries;
* after a scheduled run is persisted, the Lambda returns the exact immutable
  run record read back from the status store when available; and
* existing immutable locks and labels are corrected in memory only when their
  sole exclusion is a pre-lock state made false by a verified exact T-45 lock.

The persisted read-back prevents harmless DynamoDB numeric round-trip changes
from making deployment verification compare a pre-persistence object with a
post-persistence object. The prospective read repair never rewrites an
immutable lock or label and preserves every chronology, source, fundamentals,
vector, final-label, holdout, calibration, accuracy, promotion, champion,
inference-authority, and production-authority gate.
"""
from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from functools import wraps
from pathlib import Path
from typing import Any, Dict

import mlb_prospective_trainer_read_repair as prospective_trainer_read_repair


COMPAT_VERSION = "MLB-TRAINER-CANONICAL-CONTINUITY-WAIT-v5-persisted-return"
_BASE_MODULE_NAME = "_inqsi_mlb_ml_aws_training_v1_canonical"
_BASE_PATH = Path(__file__).resolve().with_name("mlb_ml_aws_training_v1.py")


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
    """Return the immutable stored run when it can be proven to be the same run.

    The invocation payload is retained as the fail-closed fallback. A persisted
    record is used only when the service exposes the canonical status store and
    the read-back record carries the identical non-empty run ID.
    """
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

_original_save_run_status = canonical.TrainingService._save_run_status
if not getattr(_original_save_run_status, "_mlb_unique_continuity_wait_patch", False):

    @wraps(_original_save_run_status)
    def _save_run_status_with_continuity_wait(self, payload):
        return _original_save_run_status(
            self, normalize_canonical_continuity_wait(payload)
        )

    _save_run_status_with_continuity_wait._mlb_unique_continuity_wait_patch = True
    _save_run_status_with_continuity_wait._mlb_unique_continuity_wait_version = (
        COMPAT_VERSION
    )
    canonical.TrainingService._save_run_status = _save_run_status_with_continuity_wait

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