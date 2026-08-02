"""Fail-closed compatibility handler for the canonical MLB AWS trainer.

The canonical implementation remains in ``mlb_ml_aws_training_v1.py``. This
uniquely named Lambda entrypoint loads that file directly and installs one
narrow normalization for unresolved canonical-slate continuity at both the
immutable status-persistence boundary and the training-result return boundary.

No chronology, final-label, holdout, calibration, accuracy, promotion,
champion, inference-authority, or production-authority rule is weakened.
"""
from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from functools import wraps
from pathlib import Path
from typing import Any, Dict

COMPAT_VERSION = "MLB-TRAINER-CANONICAL-CONTINUITY-WAIT-v3-return-and-persist"
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
        and value.get("modelTrained") is False
        and value.get("championChanged") is False
        and isinstance(continuity, Mapping)
        and continuity.get("ok") is False
        and (
            not isinstance(milestones, Mapping)
            or milestones.get("canonicalContinuityReady") is False
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


canonical = _load_canonical_module()

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
        return normalize_canonical_continuity_wait(result)

    _run_scheduled_with_continuity_wait._mlb_unique_continuity_return_patch = True
    _run_scheduled_with_continuity_wait._mlb_unique_continuity_return_version = (
        COMPAT_VERSION
    )
    canonical.TrainingService.run_scheduled = _run_scheduled_with_continuity_wait


def lambda_handler(event, context):
    return canonical.lambda_handler(event, context)
