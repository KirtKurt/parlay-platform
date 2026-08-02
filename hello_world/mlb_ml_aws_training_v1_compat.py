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

COMPAT_VERSION = "MLB-TRAINER-CANONICAL-CONTINUITY-WAIT-v4-sparse-safe"
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


def _authority_change_claimed(value: Mapping[str, Any]) -> bool:
    """Return True when a payload claims any model or authority mutation."""
    direct_flags = (
        "modelTrained",
        "championChanged",
        "runtimeAuthorityChanged",
        "runtimeAuthorityActivated",
        "productionAuthorityChanged",
        "liveInferenceAuthority",
        "automaticPromotionEnabled",
        "trainingReady",
    )
    if any(value.get(name) is True for name in direct_flags):
        return True
    promotion = value.get("promotion")
    if isinstance(promotion, Mapping) and any(
        promotion.get(name) is True
        for name in (
            "shadowChampionApproved",
            "runtimeAuthorityActivated",
            "productionAuthorityChanged",
        )
    ):
        return True
    return False


def normalize_canonical_continuity_wait(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize only the exact, non-mutating continuity wait condition.

    Production continuity payloads may be sparse because the trainer exits
    before dataset/model construction. Requiring optional diagnostic fields
    made the expected wait look like a Lambda failure. The exact status remains
    the authority, while every model/champion/runtime mutation flag must remain
    absent or false. Contradictory payloads stay unhealthy.
    """
    value = dict(payload)
    if value.get("status") != "CANONICAL_SLATE_CONTINUITY_BLOCKED":
        return value
    if _authority_change_claimed(value):
        return value

    continuity = value.get("canonicalSlateContinuity")
    if isinstance(continuity, Mapping) and continuity.get("ok") is True:
        return value
    milestones = value.get("milestones")
    if (
        isinstance(milestones, Mapping)
        and milestones.get("canonicalContinuityReady") is True
    ):
        return value

    value.update(
        {
            "ok": True,
            "status": "WAITING_FOR_CANONICAL_SLATE_CONTINUITY",
            "executionMode": value.get("executionMode") or "training",
            "trainingReady": False,
            "waiting": True,
            "waitReason": "canonical_slate_continuity",
            "modelTrained": False,
            "championChanged": False,
            "runtimeAuthorityChanged": False,
            "runtimeAuthorityActivated": False,
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
