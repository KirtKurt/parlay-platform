#!/usr/bin/env python3
"""Run V9 shadow evaluation with a durable frozen-model handoff artifact.

The wrapper repairs the legacy handoff resolver, which ignored the frozen nested
candidate policy, and enforces the V9 integrity diagnostics after report creation.
It remains read-only and has no production or promotion authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

import mlb_historical_v7_priority_repairs_v1 as repairs

try:
    from scripts import run_mlb_historical_supervised_v9_shadow as original
except ImportError:  # Direct execution from the scripts directory.
    import run_mlb_historical_supervised_v9_shadow as original

VERSION = "MLB-V9-SHADOW-MODEL-ARTIFACT-v1"


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
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
        "frozenBeforeUntouchedHoldout": diagnostics.get("holdoutEvaluatedAfterFreeze") is True,
        "holdoutLabelsUsedForFitOrSelection": diagnostics.get("holdoutLabelsUsedForFitOrSelection"),
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
        "frozenBeforeUntouchedHoldout": stable_model["frozenBeforeUntouchedHoldout"],
        "holdoutLabelsUsedForFitOrSelection": stable_model["holdoutLabelsUsedForFitOrSelection"],
        "trainingGameCount": stable_model["trainingGameCount"],
        "walkForwardGameCount": stable_model["walkForwardGameCount"],
        "untouchedHoldoutGameCount": stable_model["untouchedHoldoutGameCount"],
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


def _enforce_report_integrity(output: str | None) -> bool:
    if not output:
        return False
    path = Path(output)
    if not path.exists() or not path.stat().st_size:
        return False
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
    blockers = sorted(set(blockers))
    value["blockers"] = blockers
    value["ok"] = value.get("ok") is True and not blockers
    value["integrityEnforcedByWrapper"] = True
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return value["ok"] is True


def main() -> int:
    repairs.candidate_handoff = candidate_handoff
    original_code = original.main()
    report_ok = _enforce_report_integrity(_argument("--output"))
    return 0 if original_code == 0 and report_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
