#!/usr/bin/env python3
"""Run the gated V7/V9 evaluator against the materialized feature corpus.

The legacy runner decides cadence from canonical game rows only. This wrapper
loads provider-neutral official context first and treats retired BBS context as disabled, exposes target fundamentals to
signal-level V7 features, fingerprints the semantic feature corpus, and injects
feature-aware cadence anchors. It remains retrospective, read-only, and without
promotion or production-write authority.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping

import mlb_historical_v7_feature_bridge_v1 as feature_bridge

try:
    from scripts import run_mlb_historical_supervised_v9_shadow as legacy
    from scripts import run_mlb_historical_supervised_v9_shadow_cadence as cadence_state
    from scripts import run_mlb_historical_supervised_v9_shadow_cadence_v3 as cadence_v3
except ImportError:  # Direct execution from the scripts directory.
    import run_mlb_historical_supervised_v9_shadow as legacy
    import run_mlb_historical_supervised_v9_shadow_cadence as cadence_state
    import run_mlb_historical_supervised_v9_shadow_cadence_v3 as cadence_v3

VERSION = "MLB-V7-V9-FEATURE-AWARE-SHADOW-RUNNER-v2-provider-neutral-complete-slate-aware"


def _argument(name: str) -> str | None:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return None
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


def _integer_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except Exception:
        return default


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.feature-aware-{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _augment_report(
    report: Mapping[str, Any],
    *,
    feature_proof: Mapping[str, Any],
    decision: Mapping[str, Any],
    full_feature_increment: int,
    lightweight_feature_increment: int,
) -> Dict[str, Any]:
    value = copy.deepcopy(dict(report))
    feature_state = copy.deepcopy(dict(feature_proof.get("featureCorpus") or {}))
    value.update(
        {
            "featureAwareRunnerVersion": VERSION,
            "featureOverlay": copy.deepcopy(dict(feature_proof)),
            "featureCorpus": feature_state,
            "shadowRefitIncrementFeatureRows": full_feature_increment,
            "lightweightSelectiveEvaluationIncrementFeatureRows": lightweight_feature_increment,
            "newFeatureRowsSinceLastShadowFit": int(
                decision.get("newFeatureRowsSinceLastShadowFit") or 0
            ),
            "newFeatureRowsSinceLastLightweightEvaluation": int(
                decision.get("newFeatureRowsSinceLastLightweightEvaluation") or 0
            ),
            "remainingFeatureRowsUntilShadowRefit": int(
                decision.get("remainingFeatureRowsUntilShadowRefit") or 0
            ),
            "remainingFeatureRowsUntilLightweightEvaluation": int(
                decision.get("remainingFeatureRowsUntilLightweightEvaluation") or 0
            ),
            "refitReasons": list(decision.get("refitReasons") or []),
            "lightweightReasons": list(decision.get("lightweightReasons") or []),
            "newCompleteSlatesSinceLastShadowFit": int(
                decision.get("newCompleteSlatesSinceLastShadowFit") or 0
            ),
            "newCompleteSlatesSinceLastLightweightEvaluation": int(
                decision.get("newCompleteSlatesSinceLastLightweightEvaluation") or 0
            ),
            "remainingCompleteSlatesUntilLightweightEvaluation": int(
                decision.get("remainingCompleteSlatesUntilLightweightEvaluation") or 0
            ),
            "lightweightSelectiveEvaluationIncrementCompleteSlates": int(
                decision.get("lightweightSelectiveEvaluationIncrementCompleteSlates") or 1
            ),
            "providerNeutralOfficialContextRequired": True,
            "retiredBbsOverlayRequired": False,
            "providerCallsMade": 0,
            "productionAuthorityChanged": False,
        }
    )
    if value.get("shadowRefitPerformed") is not True:
        value["stalledStage"] = "WAITING_FOR_NEW_GAMES_FEATURE_ROWS_OR_COMPLETE_SLATES"
    return value


def main() -> int:
    import mlb_historical_optimizer_v7_recovery_entrypoint as runtime
    import mlb_historical_v7_priority_repairs_v1 as repairs

    output_name = _argument("--output")
    if not output_name:
        raise ValueError("--output is required")
    output = Path(output_name)

    # This runner is the shadow-only authority that consumes the immutable overlay
    # pointers. Require both feature families so a missing/stale pointer fails closed
    # instead of silently evaluating the market-only corpus again.
    os.environ.setdefault("MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED", "false")
    os.environ.setdefault("MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED", "false")
    os.environ.setdefault("MLB_V8_HISTORICAL_CONTEXT_OVERLAY_ENABLED", "true")
    os.environ.setdefault("MLB_V8_HISTORICAL_CONTEXT_OVERLAY_REQUIRED", "true")

    handler = runtime.base.optimizer_handler
    state = handler._load_state()
    if not isinstance(state, dict):
        raise RuntimeError("historical optimizer state is missing")
    complete_slate_count = int(
        state.get("completeSlateCount")
        or len(state.get("completedSlates") or [])
        or 0
    )
    lightweight_slate_increment = _integer_env(
        "MLB_V7_LIGHTWEIGHT_INCREMENT_COMPLETE_SLATES", 1
    )

    raw_records = handler._load_training_records(state)
    records, feature_proof = feature_bridge.load_and_apply(raw_records)
    feature_state = feature_proof.get("featureCorpus") or {}
    feature_count = int(feature_state.get("materializedFeatureRowCount") or 0)
    feature_fingerprint = str(feature_state.get("fingerprint") or "")
    dataset_fingerprint = feature_bridge.dataset_fingerprint(records, feature_state)
    full_feature_increment = _integer_env(
        "MLB_V7_SHADOW_REFIT_INCREMENT_FEATURE_ROWS", 50
    )
    lightweight_feature_increment = _integer_env(
        "MLB_V7_LIGHTWEIGHT_INCREMENT_FEATURE_ROWS", 10
    )

    original_load = handler._load_training_records
    original_fingerprint = repairs.dataset_fingerprint
    original_decide = cadence_state.decide_cadence
    original_anchor_fields = cadence_state.report_anchor_fields
    decision_holder: Dict[str, Any] = {}

    def load_training_records(_state):
        return records

    def dataset_fingerprint_for_enriched(_records):
        return dataset_fingerprint

    def decide(previous, **kwargs):
        value = cadence_v3.decide_cadence(
            previous,
            feature_count=feature_count,
            feature_fingerprint=feature_fingerprint,
            full_feature_increment=full_feature_increment,
            lightweight_feature_increment=lightweight_feature_increment,
            current_slate_count=complete_slate_count,
            lightweight_slate_increment=lightweight_slate_increment,
            **kwargs,
        )
        decision_holder.clear()
        decision_holder.update(value)
        return value

    def report_anchor_fields(decision, **kwargs):
        return cadence_v3.report_anchor_fields(
            decision,
            feature_count=feature_count,
            feature_fingerprint=feature_fingerprint,
            current_slate_count=complete_slate_count,
            **kwargs,
        )

    handler._load_training_records = load_training_records
    repairs.dataset_fingerprint = dataset_fingerprint_for_enriched
    cadence_state.decide_cadence = decide
    cadence_state.report_anchor_fields = report_anchor_fields
    try:
        exit_code = legacy.main()
    finally:
        handler._load_training_records = original_load
        repairs.dataset_fingerprint = original_fingerprint
        cadence_state.decide_cadence = original_decide
        cadence_state.report_anchor_fields = original_anchor_fields

    if not output.exists() or not output.stat().st_size:
        return 1
    report = json.loads(output.read_text())
    report = _augment_report(
        report,
        feature_proof=feature_proof,
        decision=decision_holder,
        full_feature_increment=full_feature_increment,
        lightweight_feature_increment=lightweight_feature_increment,
    )
    _atomic_write(output, report)
    return 0 if exit_code == 0 and report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
