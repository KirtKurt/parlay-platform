#!/usr/bin/env python3
"""Run V9 shadow evaluation with a durable frozen-model handoff artifact.

This wrapper fixes the legacy handoff resolver, which only checked top-level policy
fields even though V9 stores the frozen policy under result['candidate']['policy'].
The generated artifact remains shadow-only and has no production or promotion
authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping

import mlb_historical_v7_priority_repairs_v1 as repairs
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


def main() -> int:
    repairs.candidate_handoff = candidate_handoff
    return original.main()


if __name__ == "__main__":
    raise SystemExit(main())
