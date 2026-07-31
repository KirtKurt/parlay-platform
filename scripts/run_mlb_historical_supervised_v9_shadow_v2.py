#!/usr/bin/env python3
"""Run V9 shadow evaluation with atomic evidence and feature-aware V7 inputs.

The wrapper repairs the frozen candidate handoff, composes immutable BBS prior-game
and target-game context overlays, projects validated record-level fundamentals into
the legacy V7/V9 team signals, and prevents a cancelled run from publishing a
handoff or zero-byte report. It remains read-only and has no production authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

import mlb_historical_supervised_v9 as supervised_v9
import mlb_historical_v7_feature_bridge_v1 as feature_bridge
import mlb_historical_v7_priority_repairs_v1 as repairs
import mlb_v8_historical_bbs_overlay_v1 as bbs_overlay
import mlb_v8_historical_context_overlay_v1 as context_overlay

try:
    from scripts import run_mlb_historical_supervised_v9_shadow as original
except ImportError:  # Direct execution from the scripts directory.
    import run_mlb_historical_supervised_v9_shadow as original

VERSION = "MLB-V9-SHADOW-MODEL-ARTIFACT-v1"
_TRAINING_BRIDGE_EVIDENCE: Dict[str, Any] = {}


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def candidate_handoff(result: Mapping[str, Any], fingerprint: str) -> Dict[str, Any]:
    candidate = result.get("candidate") or {}
    diagnostics = result.get("supervisedDiagnostics") or {}
    gate = result.get("promotionGate") or {}
    policy = copy.deepcopy(
        candidate.get("policy")
        or result.get("policy")
        or result.get("candidatePolicy")
        or result.get("bestPolicy")
        or {}
    )
    candidate_kind = (
        "SUPERVISED_V9"
        if diagnostics.get("outerWalkForwardAccepted") is True
        else "BASELINE_FALLBACK"
    )
    stable_model = {
        "artifactVersion": VERSION,
        "datasetFingerprint": fingerprint,
        "searchVersion": result.get("searchVersion"),
        "candidateKind": candidate_kind,
        "policy": policy,
        "policyDigest": candidate.get("policyDigest"),
        "trainingGameCount": gate.get("trainingGameCount"),
        "walkForwardGameCount": gate.get("walkForwardGameCount"),
        "untouchedHoldoutGameCount": gate.get("untouchedHoldoutGameCount"),
        "selectedL2": diagnostics.get("selectedL2"),
        "selectedBlend": diagnostics.get("selectedBlend"),
        "selectedTemperature": diagnostics.get("selectedTemperature"),
        "featureVersion": diagnostics.get("featureVersion"),
        "featureCount": diagnostics.get("featureCount"),
        "frozenBeforeUntouchedHoldout": diagnostics.get(
            "holdoutEvaluatedAfterFreeze"
        )
        is True,
        "holdoutLabelsUsedForFitOrSelection": diagnostics.get(
            "holdoutLabelsUsedForFitOrSelection"
        ),
    }
    model_digest = _digest(stable_model)
    eligible = bool(
        policy
        and candidate_kind == "SUPERVISED_V9"
        and stable_model["frozenBeforeUntouchedHoldout"]
        and stable_model["holdoutLabelsUsedForFitOrSelection"] is False
    )
    payload = {
        "schemaVersion": "MLB-V7-SHADOW-CANDIDATE-HANDOFF-v1",
        "artifactVersion": VERSION,
        "artifactType": "FROZEN_SHADOW_MODEL",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "datasetFingerprint": fingerprint,
        "searchVersion": result.get("searchVersion"),
        "candidateKind": candidate_kind,
        "policy": policy,
        "policyDigest": candidate.get("policyDigest"),
        "modelDigest": model_digest,
        "promotionPassed": gate.get("passed") is True,
        "promotionAuthority": False,
        "productionAuthority": False,
        "eligibleForCanonicalSeed": eligible,
        "frozenBeforeUntouchedHoldout": stable_model[
            "frozenBeforeUntouchedHoldout"
        ],
        "holdoutLabelsUsedForFitOrSelection": stable_model[
            "holdoutLabelsUsedForFitOrSelection"
        ],
        "trainingGameCount": stable_model["trainingGameCount"],
        "walkForwardGameCount": stable_model["walkForwardGameCount"],
        "untouchedHoldoutGameCount": stable_model["untouchedHoldoutGameCount"],
        "requiresCanonicalChronologicalReevaluation": True,
        "requiresFresh200GameUntouchedAudit": True,
    }
    payload["digest"] = _digest(
        {key: value for key, value in payload.items() if key != "createdAtUtc"}
    )
    return payload


def _argument(name: str) -> str | None:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return None
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


def _replace_argument(name: str, value: str) -> None:
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        raise ValueError(f"missing value for {name}")
    sys.argv[index + 1] = value


def _remove_argument(name: str) -> None:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return
    del sys.argv[index : index + 2]


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _install_training_record_bridge() -> None:
    """Compose immutable overlays and expose them to the legacy pair-feature API."""
    import mlb_historical_optimizer_v7_recovery_entrypoint as runtime

    handler = runtime.base.optimizer_handler
    if getattr(handler, "_INQSI_V7_FEATURE_BRIDGE_LOADER_INSTALLED", False):
        feature_bridge.install(repairs)
        return

    os.environ.setdefault("MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED", "true")
    os.environ.setdefault("MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED", "true")
    os.environ.setdefault("MLB_V8_HISTORICAL_BBS_TABLE", "parlay_platform_snapshots")
    os.environ.setdefault("MLB_V8_HISTORICAL_CONTEXT_OVERLAY_ENABLED", "true")
    os.environ.setdefault("MLB_V8_HISTORICAL_CONTEXT_OVERLAY_REQUIRED", "true")
    os.environ.setdefault(
        "MLB_V8_HISTORICAL_CONTEXT_TABLE", "parlay_platform_snapshots"
    )

    original_loader = handler._load_training_records

    def load_training_records_with_context(state: Mapping[str, Any], *args, **kwargs):
        raw_records = original_loader(state, *args, **kwargs)
        bbs_records, bbs_proof = bbs_overlay.load_and_apply(raw_records)
        context_records, context_proof = context_overlay.load_and_apply(bbs_records)
        bridged_records, bridge_proof = feature_bridge.materialize_training_signals(
            context_records,
            supervised_v9,
        )

        blockers = []
        if bbs_proof.get("status") != "APPLIED":
            blockers.append("historical_bbs_overlay_not_applied")
        if context_proof.get("status") != "APPLIED":
            blockers.append("historical_target_context_overlay_not_applied")
        bbs_applied = int(bbs_proof.get("appliedGameCount") or 0)
        target_applied = int(context_proof.get("appliedGameCount") or 0)
        if bbs_applied <= 0:
            blockers.append("historical_bbs_context_empty")
        if target_applied <= 0:
            blockers.append("historical_target_context_empty")
        if int(bridge_proof.get("priorSnapshotRecordCount") or 0) != bbs_applied:
            blockers.append("bbs_prior_bridge_record_count_mismatch")
        if int(bridge_proof.get("priorSignalPairCount") or 0) != bbs_applied:
            blockers.append("bbs_prior_not_projected_into_team_signals")
        if int(bridge_proof.get("targetSnapshotRecordCount") or 0) != target_applied:
            blockers.append("target_context_bridge_record_count_mismatch")
        if int(bridge_proof.get("targetSignalPairCount") or 0) != target_applied:
            blockers.append("target_context_not_projected_into_team_signals")
        if bridge_proof.get("priorAndTargetSignalsComposed") is not True:
            blockers.append("prior_and_target_signal_composition_unproven")
        if bridge_proof.get("datasetFingerprint") != feature_bridge.dataset_fingerprint(
            bridged_records
        ):
            blockers.append("feature_aware_dataset_fingerprint_mismatch")

        _TRAINING_BRIDGE_EVIDENCE.clear()
        _TRAINING_BRIDGE_EVIDENCE.update(
            {
                "historicalBbsFundamentals": copy.deepcopy(bbs_proof),
                "historicalTargetGameContext": copy.deepcopy(context_proof),
                "trainingSignalMaterialization": copy.deepcopy(bridge_proof),
                "featureBridgeVersion": feature_bridge.VERSION,
                "blockers": sorted(set(blockers)),
            }
        )
        if blockers:
            raise RuntimeError(
                "historical V7 feature bridge failed:" + ",".join(sorted(set(blockers)))
            )
        return bridged_records

    handler._load_training_records = load_training_records_with_context
    handler._INQSI_V7_FEATURE_BRIDGE_LOADER_INSTALLED = True
    feature_bridge.install(repairs)


def _enforce_report_integrity(output: str | None) -> tuple[bool, Dict[str, Any]]:
    if not output:
        return False, {}
    path = Path(output)
    if not path.exists() or not path.stat().st_size:
        return False, {}
    value = json.loads(path.read_text())
    blockers = list(value.get("blockers") or [])
    diagnostics = ((value.get("supervisedCandidate") or {}).get("diagnostics") or {})
    if value.get("shadowRefitPerformed") is True:
        if diagnostics.get("strictBinaryLabels") is not True:
            blockers.append("strict_binary_label_contract_missing")
        if diagnostics.get("v8ExpansionFallbackEnabled") is not True:
            blockers.append("v8_expansion_fallback_not_enabled")
        if diagnostics.get("holdoutEvaluatedAfterFreeze") is not True:
            blockers.append("holdout_not_proven_post_freeze")
        if diagnostics.get("holdoutLabelsUsedForFitOrSelection") is not False:
            blockers.append("holdout_used_for_fit_or_selection")

    evidence = copy.deepcopy(_TRAINING_BRIDGE_EVIDENCE)
    if not evidence:
        blockers.append("v7_feature_bridge_evidence_missing")
    else:
        blockers.extend(evidence.pop("blockers", []))
        value.update(evidence)
        materialization = value.get("trainingSignalMaterialization") or {}
        if value.get("datasetFingerprint") != materialization.get(
            "datasetFingerprint"
        ):
            blockers.append("published_dataset_fingerprint_not_feature_aware")
        population = ((value.get("featurePopulation") or {}).get("features") or {})
        populated = sum(
            int((population.get(name) or {}).get("nonzeroCount") or 0)
            for name in ("starterAvailable", "bullpenAvailable", "lineupAvailable")
        )
        prior_populated = int(
            (population.get("bbsPriorAvailable") or {}).get("nonzeroCount") or 0
        )
        value["legacyFundamentalsTrainingColumnNonzeroCount"] = populated
        value["priorHistoryTrainingColumnNonzeroCount"] = prior_populated
        if int(materialization.get("targetSignalPairCount") or 0) > 0 and populated <= 0:
            blockers.append("target_context_did_not_reach_legacy_training_columns")
        if (
            int(materialization.get("priorSignalPairCount") or 0) > 0
            and prior_populated <= 0
        ):
            blockers.append("bbs_prior_context_did_not_reach_training_columns")

    blockers = sorted(set(blockers))
    value["blockers"] = blockers
    value["ok"] = value.get("ok") is True and not blockers
    value["integrityEnforcedByWrapper"] = True
    value["featureAwareRefitEnabled"] = True
    _atomic_write_json(path, value)
    return value["ok"] is True, value


def main() -> int:
    repairs.candidate_handoff = candidate_handoff
    _install_training_record_bridge()
    output = _argument("--output")
    handoff_output = _argument("--handoff-output")
    if not output:
        raise ValueError("--output is required")
    final_report = Path(output)
    temporary_report = final_report.with_name(
        f".{final_report.name}.evaluation-{os.getpid()}.json"
    )
    _replace_argument("--output", str(temporary_report))
    _remove_argument("--handoff-output")
    try:
        original_code = original.main()
        report_ok, report = _enforce_report_integrity(str(temporary_report))
        if temporary_report.exists() and temporary_report.stat().st_size:
            final_report.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary_report, final_report)
        if (
            handoff_output
            and report.get("shadowRefitPerformed") is True
            and isinstance(report.get("canonicalCandidateHandoff"), Mapping)
        ):
            _atomic_write_json(
                Path(handoff_output), report["canonicalCandidateHandoff"]
            )
        return 0 if original_code == 0 and report_ok else 1
    finally:
        temporary_report.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
