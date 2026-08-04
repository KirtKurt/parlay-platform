#!/usr/bin/env python3
"""Run V9 shadow evaluation with an atomic frozen-model evidence contract.

The wrapper repairs the legacy handoff resolver, which ignored the frozen nested
candidate policy, enforces V9 integrity diagnostics, and prevents a cancelled run
from publishing a handoff or zero-byte report. It remains read-only and has no
production or promotion authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import mlb_historical_v7_priority_repairs_v1 as repairs

try:
    from scripts import run_mlb_historical_supervised_v9_shadow_feature_aware as original
except ImportError:  # Direct execution from the scripts directory.
    import run_mlb_historical_supervised_v9_shadow_feature_aware as original

VERSION = "MLB-V9-SHADOW-MODEL-ARTIFACT-v1"
METRIC_EVIDENCE_VERSION = "MLB-V9-CURRENT-PARTITION-METRICS-v1"
_METRIC_FIELDS: Dict[str, Sequence[str]] = {
    "gameCount": ("gameCount", "games", "eligibleGameCount"),
    "dayCount": ("dayCount", "days", "slateCount"),
    "overallAccuracy": ("overallAccuracy", "accuracy"),
    "meanDailyAccuracy": ("meanDailyAccuracy",),
    "minimumDailyAccuracy": ("minimumDailyAccuracy", "minDailyAccuracy"),
    "brierScore": ("brierScore", "brier"),
    "logLoss": ("logLoss",),
    "exactSlateCoverage": ("exactSlateCoverage", "coverage"),
    "dailyPassRate": ("dailyPassRate",),
}


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}


def _metric_snapshot(value: Any) -> Dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    snapshot: Dict[str, Any] = {"version": METRIC_EVIDENCE_VERSION}
    for output_name, aliases in _METRIC_FIELDS.items():
        selected = None
        for alias in aliases:
            if alias in source and source.get(alias) is not None:
                selected = source.get(alias)
                break
        snapshot[output_name] = selected
    daily = source.get("daily") if isinstance(source.get("daily"), list) else []
    snapshot["dailyCount"] = len(daily) if daily else snapshot.get("dayCount")
    snapshot["sourceDigest"] = _digest(
        {key: value for key, value in snapshot.items() if key != "sourceDigest"}
    )
    return snapshot


def _snapshot_has_metrics(snapshot: Mapping[str, Any]) -> bool:
    return any(
        snapshot.get(name) is not None
        for name in ("meanDailyAccuracy", "brierScore", "logLoss")
    )


def _snapshot_complete(snapshot: Mapping[str, Any]) -> bool:
    return all(
        snapshot.get(name) is not None
        for name in ("gameCount", "meanDailyAccuracy", "brierScore", "logLoss")
    )


def _metric_errors(
    *,
    selected_walk_forward: Mapping[str, Any],
    selected_holdout: Mapping[str, Any],
    expected_walk_forward: Any,
    expected_holdout: Any,
) -> list[str]:
    errors: list[str] = []
    for name, snapshot, expected in (
        ("walk_forward", selected_walk_forward, expected_walk_forward),
        ("untouched_holdout", selected_holdout, expected_holdout),
    ):
        if not _snapshot_complete(snapshot):
            errors.append(f"{name}_current_partition_metrics_incomplete")
            continue
        try:
            expected_count = int(expected)
            actual_count = int(snapshot.get("gameCount"))
        except (TypeError, ValueError):
            errors.append(f"{name}_game_count_unverifiable")
            continue
        if expected_count != actual_count:
            errors.append(
                f"{name}_game_count_mismatch:{actual_count}!={expected_count}"
            )
    return errors


def candidate_handoff(result: Mapping[str, Any], fingerprint: str) -> Dict[str, Any]:
    candidate = result.get("candidate") or {}
    diagnostics = result.get("supervisedDiagnostics") or {}
    gate = result.get("promotionGate") or {}
    baseline = result.get("baseline") or {}
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
        "frozenBeforeUntouchedHoldout": diagnostics.get("holdoutEvaluatedAfterFreeze") is True,
        "holdoutLabelsUsedForFitOrSelection": diagnostics.get("holdoutLabelsUsedForFitOrSelection"),
    }
    model_digest = _digest(stable_model)

    supervised_walk_forward = _metric_snapshot(
        _mapping(
            candidate.get("walkForward"),
            diagnostics.get("walkForward"),
            result.get("supervisedWalkForward"),
        )
    )
    supervised_holdout_internal = _metric_snapshot(
        _mapping(
            candidate.get("untouchedHoldout"),
            diagnostics.get("untouchedHoldout"),
            result.get("supervisedUntouchedHoldout"),
        )
    )
    baseline_walk_forward = _metric_snapshot(
        _mapping(
            baseline.get("walkForward"),
            result.get("baselineWalkForward"),
        )
    )
    baseline_holdout = _metric_snapshot(
        _mapping(
            baseline.get("untouchedHoldout"),
            result.get("baselineUntouchedHoldout"),
        )
    )

    if candidate_kind == "SUPERVISED_V9":
        selected_walk_forward = supervised_walk_forward
        selected_holdout = supervised_holdout_internal
        supervised_holdout_public: Dict[str, Any] | None = supervised_holdout_internal
    else:
        selected_walk_forward = (
            baseline_walk_forward
            if _snapshot_has_metrics(baseline_walk_forward)
            else supervised_walk_forward
        )
        selected_holdout = baseline_holdout
        # A rejected learned challenger must not gain access to or publish its
        # untouched holdout. Only the selected fallback comparator is public.
        supervised_holdout_public = None

    metric_errors = _metric_errors(
        selected_walk_forward=selected_walk_forward,
        selected_holdout=selected_holdout,
        expected_walk_forward=stable_model["walkForwardGameCount"],
        expected_holdout=stable_model["untouchedHoldoutGameCount"],
    )
    partition_identity = {
        "version": METRIC_EVIDENCE_VERSION,
        "datasetFingerprint": fingerprint,
        "modelDigest": model_digest,
        "candidateKind": candidate_kind,
        "trainingGameCount": stable_model["trainingGameCount"],
        "walkForwardGameCount": stable_model["walkForwardGameCount"],
        "untouchedHoldoutGameCount": stable_model["untouchedHoldoutGameCount"],
        "selectedWalkForwardDigest": selected_walk_forward.get("sourceDigest"),
        "selectedUntouchedHoldoutDigest": selected_holdout.get("sourceDigest"),
        "chronologicalPartitions": copy.deepcopy(
            result.get("chronologicalPartitions")
            or result.get("partitions")
            or result.get("split")
            or {}
        ),
    }
    metric_partition_fingerprint = _digest(partition_identity)

    eligible = bool(
        policy
        and candidate_kind == "SUPERVISED_V9"
        and stable_model["frozenBeforeUntouchedHoldout"]
        and stable_model["holdoutLabelsUsedForFitOrSelection"] is False
        and not metric_errors
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
        "frozenBeforeUntouchedHoldout": stable_model["frozenBeforeUntouchedHoldout"],
        "holdoutLabelsUsedForFitOrSelection": stable_model["holdoutLabelsUsedForFitOrSelection"],
        "trainingGameCount": stable_model["trainingGameCount"],
        "walkForwardGameCount": stable_model["walkForwardGameCount"],
        "untouchedHoldoutGameCount": stable_model["untouchedHoldoutGameCount"],
        "metricEvidenceVersion": METRIC_EVIDENCE_VERSION,
        "metricPartitionFingerprint": metric_partition_fingerprint,
        "metricsCurrentPartition": not metric_errors,
        "metricPublicationErrors": metric_errors,
        "selectedWalkForwardMetrics": selected_walk_forward,
        "selectedUntouchedHoldoutMetrics": selected_holdout,
        "baselineWalkForwardMetrics": baseline_walk_forward,
        "baselineUntouchedHoldoutMetrics": baseline_holdout,
        "supervisedWalkForwardMetrics": supervised_walk_forward,
        "supervisedUntouchedHoldoutMetrics": supervised_holdout_public,
        "requiresCanonicalChronologicalReevaluation": True,
        "requiresFresh200GameUntouchedAudit": True,
    }
    payload["digest"] = _digest({k: v for k, v in payload.items() if k != "createdAtUtc"})
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


def _publish_metric_view(value: Dict[str, Any], handoff: Mapping[str, Any]) -> None:
    walk_forward = handoff.get("selectedWalkForwardMetrics") or {}
    holdout = handoff.get("selectedUntouchedHoldoutMetrics") or {}
    evidence = {
        "version": METRIC_EVIDENCE_VERSION,
        "metricPartitionFingerprint": handoff.get("metricPartitionFingerprint"),
        "metricsCurrentPartition": handoff.get("metricsCurrentPartition"),
        "candidateKind": handoff.get("candidateKind"),
        "walkForward": copy.deepcopy(walk_forward),
        "untouchedHoldout": copy.deepcopy(holdout),
    }
    value["latestMetricEvidence"] = evidence
    candidate_view = dict(value.get("supervisedCandidate") or {})
    candidate_view["metricEvidenceVersion"] = METRIC_EVIDENCE_VERSION
    candidate_view["metricPartitionFingerprint"] = handoff.get("metricPartitionFingerprint")
    candidate_view["metricsCurrentPartition"] = handoff.get("metricsCurrentPartition")
    candidate_view["selectedWalkForwardMetrics"] = copy.deepcopy(walk_forward)
    candidate_view["selectedUntouchedHoldoutMetrics"] = copy.deepcopy(holdout)
    candidate_view["brierScore"] = walk_forward.get("brierScore")
    candidate_view["logLoss"] = walk_forward.get("logLoss")
    candidate_view["untouchedHoldoutBrierScore"] = holdout.get("brierScore")
    candidate_view["untouchedHoldoutLogLoss"] = holdout.get("logLoss")
    value["supervisedCandidate"] = candidate_view


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
        handoff = value.get("canonicalCandidateHandoff")
        if not isinstance(handoff, Mapping):
            blockers.append("canonical_candidate_handoff_missing")
        else:
            _publish_metric_view(value, handoff)
            if handoff.get("metricsCurrentPartition") is not True:
                blockers.append("current_partition_metric_evidence_missing")
            if handoff.get("metricPartitionFingerprint") in (None, ""):
                blockers.append("metric_partition_fingerprint_missing")
    elif isinstance(value.get("canonicalCandidateHandoff"), Mapping):
        _publish_metric_view(value, value["canonicalCandidateHandoff"])

    blockers = sorted(set(blockers))
    value["blockers"] = blockers
    value["ok"] = value.get("ok") is True and not blockers
    value["integrityEnforcedByWrapper"] = True
    value["zeroBytePublicationAllowed"] = False
    _atomic_write_json(path, value)
    return value["ok"] is True, value


def main() -> int:
    repairs.candidate_handoff = candidate_handoff
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
        if not final_report.exists() or not final_report.stat().st_size:
            raise RuntimeError("V9 shadow report publication produced zero bytes")
        return 0 if original_code == 0 and report_ok else 1
    finally:
        temporary_report.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
