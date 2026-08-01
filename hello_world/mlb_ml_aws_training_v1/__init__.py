"""Compatibility entrypoint for the MLB AWS trainer.

The canonical implementation remains in the sibling ``mlb_ml_aws_training_v1.py``
module.  This package is preferred by Python's import resolver and installs one
narrow compatibility patch before exposing the original module object:
canonical-slate continuity gaps are persisted as a healthy, fail-closed wait
instead of being converted into an unhandled Lambda function error.

No training, chronology, label, holdout, promotion, champion, or production-
authority rule is changed.
"""
from __future__ import annotations

import importlib.util
import sys
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Mapping

_COMPAT_VERSION = "MLB-TRAINER-CANONICAL-CONTINUITY-WAIT-v1"
_BASE_MODULE_NAME = "_inqsi_mlb_ml_aws_training_v1_base"
_BASE_PATH = Path(__file__).resolve().parent.parent / "mlb_ml_aws_training_v1.py"


def _load_base_module():
    existing = sys.modules.get(_BASE_MODULE_NAME)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(_BASE_MODULE_NAME, _BASE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("mlb_trainer_base_module_unavailable")

    module = importlib.util.module_from_spec(spec)
    # Dataclasses and other import-time helpers resolve the defining module
    # through sys.modules, so publish the private identity before execution.
    sys.modules[_BASE_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_BASE_MODULE_NAME, None)
        raise
    return module


def _normalize_canonical_continuity_wait(
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return a copied payload with only the expected continuity wait normalized."""

    value = dict(payload)
    continuity = value.get("canonicalSlateContinuity")
    milestones = value.get("milestones")
    is_expected_wait = (
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
    if not is_expected_wait:
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
            "continuityWaitCompatibilityVersion": _COMPAT_VERSION,
        }
    )
    return value


_base = _load_base_module()
_original_save_run_status = _base.TrainingService._save_run_status

if not getattr(_original_save_run_status, "_mlb_continuity_wait_patch", False):

    @wraps(_original_save_run_status)
    def _save_run_status_with_continuity_wait(self, payload):
        normalized = _normalize_canonical_continuity_wait(payload)
        return _original_save_run_status(self, normalized)

    _save_run_status_with_continuity_wait._mlb_continuity_wait_patch = True
    _save_run_status_with_continuity_wait._mlb_continuity_wait_version = (
        _COMPAT_VERSION
    )
    _base.TrainingService._save_run_status = _save_run_status_with_continuity_wait

_base._normalize_canonical_continuity_wait = _normalize_canonical_continuity_wait
_base.MLB_TRAINER_CONTINUITY_WAIT_COMPAT_VERSION = _COMPAT_VERSION
_base.MLB_TRAINER_CANONICAL_IMPLEMENTATION_PATH = str(_BASE_PATH)

# Expose the original module object so existing imports, monkeypatches, class
# identities, and Lambda handler resolution behave exactly as before.
sys.modules[__name__] = _base
