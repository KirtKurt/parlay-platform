#!/usr/bin/env python3
"""Run V7/V9 shadow learning with provider-neutral point-in-time context.

The legacy cadence woke only after new settled games.  This wrapper also wakes the
lightweight evaluation after ten new eligible context rows and the full refit after
fifty, while preserving the existing game-count cadence, chronological partitions,
untouched audit, and promotion gates.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping

import mlb_historical_optimizer_v7_recovery_entrypoint as runtime
import mlb_no_bbd_context_bridge_v1 as bridge
import run_mlb_historical_supervised_v9_shadow_cadence as cadence
import run_mlb_historical_supervised_v9_shadow_v2 as guarded_runner

VERSION = "MLB-V7-V9-NO-BBD-FEATURE-CADENCE-v1"
_CONTEXT_PROOF: Dict[str, Any] = {}
_ORIGINAL_DECIDE = cadence.decide_cadence
_ORIGINAL_ANCHORS = cadence.report_anchor_fields


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _argument(name: str) -> str | None:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return None
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


def _handlers() -> list[Any]:
    values = [runtime.base.optimizer_handler]
    original_runtime = getattr(guarded_runner.original, "runtime", None)
    if original_runtime is not None:
        values.append(original_runtime.base.optimizer_handler)
    unique = []
    seen = set()
    for handler in values:
        if id(handler) not in seen:
            unique.append(handler)
            seen.add(id(handler))
    return unique


def _install_record_bridge() -> None:
    for handler in _handlers():
        if getattr(handler, "_INQSI_NO_BBD_CONTEXT_LOADER_INSTALLED", False):
            continue
        original = handler._load_training_records

        def load(state: Mapping[str, Any], *, _original=original):
            records = _original(state)
            enriched, overlay_proof = bridge.apply_stored_overlays(records)
            enriched, context_proof = bridge.augment_v7_v9_records(enriched)
            global _CONTEXT_PROOF
            _CONTEXT_PROOF = {
                **context_proof,
                "overlayProof": overlay_proof,
                "sourceRecordCount": len(records),
            }
            return enriched

        handler._load_training_records = load
        handler._INQSI_NO_BBD_CONTEXT_LOADER_INSTALLED = True


def _feature_decision(
    previous: Mapping[str, Any],
    *,
    current_count: int,
    fingerprint: str,
    full_increment: int,
    lightweight_increment: int,
    force: bool = False,
) -> Dict[str, Any]:
    decision = _ORIGINAL_DECIDE(
        previous,
        current_count=current_count,
        fingerprint=fingerprint,
        full_increment=full_increment,
        lightweight_increment=lightweight_increment,
        force=force,
    )
    current_feature_count = _integer(
        _CONTEXT_PROOF.get("eligibleFeatureGameCount"), 0
    )
    current_feature_fingerprint = _text(
        _CONTEXT_PROOF.get("featureFingerprint")
    )
    previous_bridge = previous.get("contextBridge") or {}

    shadow_count = _integer(
        previous.get("lastShadowFitFeatureEligibleGameCount"), -1
    )
    shadow_fingerprint = _text(previous.get("lastShadowFitFeatureFingerprint"))
    if shadow_count < 0:
        if previous.get("shadowRefitPerformed") is True and previous_bridge:
            shadow_count = _integer(
                previous_bridge.get("eligibleFeatureGameCount"), 0
            )
            shadow_fingerprint = _text(previous_bridge.get("featureFingerprint"))
        else:
            shadow_count = 0

    light_count = _integer(
        previous.get("lastLightweightFeatureEligibleGameCount"), -1
    )
    light_fingerprint = _text(previous.get("lastLightweightFeatureFingerprint"))
    if light_count < 0:
        light_count = shadow_count
        light_fingerprint = shadow_fingerprint

    feature_full_increment = max(
        1, _integer(os.environ.get("MLB_V7_FEATURE_REFIT_INCREMENT_ROWS"), 50)
    )
    feature_light_increment = max(
        1,
        _integer(
            os.environ.get("MLB_V7_FEATURE_LIGHTWEIGHT_INCREMENT_ROWS"), 10
        ),
    )
    new_feature_rows = max(0, current_feature_count - shadow_count)
    new_light_rows = max(0, current_feature_count - light_count)
    feature_changed = bool(
        current_feature_fingerprint
        and current_feature_fingerprint != shadow_fingerprint
    )
    light_changed = bool(
        current_feature_fingerprint
        and current_feature_fingerprint != light_fingerprint
    )
    feature_refit = feature_changed and new_feature_rows >= feature_full_increment
    feature_light = light_changed and new_light_rows >= feature_light_increment

    decision.update(
        {
            "lastShadowFitFeatureEligibleGameCount": shadow_count,
            "lastShadowFitFeatureFingerprint": shadow_fingerprint,
            "lastLightweightFeatureEligibleGameCount": light_count,
            "lastLightweightFeatureFingerprint": light_fingerprint,
            "currentFeatureEligibleGameCount": current_feature_count,
            "currentFeatureFingerprint": current_feature_fingerprint,
            "newFeatureRowsSinceLastShadowFit": new_feature_rows,
            "newFeatureRowsSinceLastLightweightEvaluation": new_light_rows,
            "featureRefitIncrementRows": feature_full_increment,
            "featureLightweightIncrementRows": feature_light_increment,
            "remainingFeatureRowsUntilShadowRefit": max(
                0, feature_full_increment - new_feature_rows
            ),
            "remainingFeatureRowsUntilLightweightEvaluation": max(
                0, feature_light_increment - new_light_rows
            ),
            "featureDatasetChangedSinceLastFit": feature_changed,
            "featureDatasetChangedSinceLastLightweightEvaluation": light_changed,
            "featureCadenceTriggeredRefit": feature_refit,
            "featureCadenceTriggeredLightweightEvaluation": feature_light,
            "shouldRefit": bool(decision.get("shouldRefit") or feature_refit),
            "shouldLightweight": bool(
                decision.get("shouldLightweight") or feature_refit or feature_light
            ),
        }
    )
    return decision


def _feature_anchor_fields(
    decision: Mapping[str, Any],
    *,
    current_count: int,
    fingerprint: str,
    shadow_refit_performed: bool,
    lightweight_performed: bool,
) -> Dict[str, Any]:
    output = _ORIGINAL_ANCHORS(
        decision,
        current_count=current_count,
        fingerprint=fingerprint,
        shadow_refit_performed=shadow_refit_performed,
        lightweight_performed=lightweight_performed,
    )
    current_feature_count = _integer(
        decision.get("currentFeatureEligibleGameCount"), 0
    )
    current_feature_fingerprint = _text(decision.get("currentFeatureFingerprint"))
    output.update(
        {
            "featureCadenceVersion": VERSION,
            "lastShadowFitFeatureEligibleGameCount": (
                current_feature_count
                if shadow_refit_performed
                else _integer(
                    decision.get("lastShadowFitFeatureEligibleGameCount"), 0
                )
            ),
            "lastShadowFitFeatureFingerprint": (
                current_feature_fingerprint
                if shadow_refit_performed
                else _text(decision.get("lastShadowFitFeatureFingerprint"))
            ),
            "lastLightweightFeatureEligibleGameCount": (
                current_feature_count
                if lightweight_performed
                else _integer(
                    decision.get("lastLightweightFeatureEligibleGameCount"), 0
                )
            ),
            "lastLightweightFeatureFingerprint": (
                current_feature_fingerprint
                if lightweight_performed
                else _text(decision.get("lastLightweightFeatureFingerprint"))
            ),
            "newFeatureRowsSinceLastShadowFit": _integer(
                decision.get("newFeatureRowsSinceLastShadowFit"), 0
            ),
            "newFeatureRowsSinceLastLightweightEvaluation": _integer(
                decision.get("newFeatureRowsSinceLastLightweightEvaluation"), 0
            ),
            "remainingFeatureRowsUntilShadowRefit": (
                0
                if shadow_refit_performed
                else _integer(
                    decision.get("remainingFeatureRowsUntilShadowRefit"), 0
                )
            ),
            "remainingFeatureRowsUntilLightweightEvaluation": (
                0
                if lightweight_performed
                else _integer(
                    decision.get(
                        "remainingFeatureRowsUntilLightweightEvaluation"), 0
                )
            ),
            "contextBridge": dict(_CONTEXT_PROOF),
        }
    )
    return output


def _install_cadence() -> None:
    modules = [cadence]
    original_cadence = getattr(guarded_runner.original, "cadence_state", None)
    if original_cadence is not None:
        modules.append(original_cadence)
    seen = set()
    for module in modules:
        if id(module) in seen:
            continue
        module.decide_cadence = _feature_decision
        module.report_anchor_fields = _feature_anchor_fields
        seen.add(id(module))


def _postprocess(path: Path) -> None:
    if not path.exists() or not path.stat().st_size:
        return
    value = json.loads(path.read_text())
    value.update(
        {
            "noBbdFeatureCadenceVersion": VERSION,
            "contextBridge": dict(_CONTEXT_PROOF),
            "liveBbdApiAvailable": False,
            "liveBbdApiRequired": False,
            "providerCallsMade": 0,
            "productionAuthorityChanged": False,
        }
    )
    if value.get("shadowRefitPerformed") is not True:
        value["stalledStage"] = "WAITING_FOR_NEW_ELIGIBLE_GAMES_OR_FEATURE_ROWS"
        value["cadenceWaitIsOperationalFailure"] = False
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    output = _argument("--output")
    os.environ["BBS_API_DISABLED"] = "true"
    os.environ["MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED"] = "false"
    _install_record_bridge()
    _install_cadence()
    code = guarded_runner.main()
    if output:
        _postprocess(Path(output))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
