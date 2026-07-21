#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


TRAINER_VERSION = "MLB-ML-AWS-TRAINING-v1-persisted-cutover-selection-ledger-shadow"
EXPERIMENT_VERSION = "MLB-ML-EXPERIMENT-v2-fixed-slate-future-prospective-cutover"
EXPERIMENT_ID = "mlb-v2-2026-07-22-future-prospective-r3"
RELEASE_CUTOFF_UTC = "2026-07-22T04:00:00+00:00"
EXECUTION_CONCURRENCY_CONTROL = {
    "version": "MLB-ML-EXECUTION-LEASE-v1-shared-ddb-conditional",
    "strategy": "dynamodb_conditional_lease",
    "scope": "one_global_lease_across_experiments_and_modes",
    "leasePartitionKey": (
        "MLB_ML_EXPERIMENT#V2#mlb-v2-2026-07-21-future-prospective-r2"
    ),
    "migrationAnchorExperimentId": (
        "mlb-v2-2026-07-21-future-prospective-r2"
    ),
    "leaseKey": "EXECUTION_LEASE",
    "leaseSeconds": 960,
    "protectedExecutionModes": [
        "manual_review",
        "selection_capture",
        "training",
    ],
    "acquiredForRun": True,
    "expiredLeaseReclaimEnabled": True,
    "ownerConditionalRelease": True,
    "reservedLambdaConcurrencyRequired": False,
}


def _parse_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _identity_errors(
    value: Any,
    *,
    expected_git_sha: str,
    expected_template_sha256: str,
    prefix: str,
) -> List[str]:
    identity = value if isinstance(value, dict) else {}
    errors: List[str] = []
    if identity.get("gitSha") != expected_git_sha:
        errors.append(f"{prefix}_git_identity_mismatch")
    if identity.get("templateSha256") != expected_template_sha256:
        errors.append(f"{prefix}_template_identity_mismatch")
    return errors


def _contract_errors(
    payload: Dict[str, Any],
    *,
    prefix: str,
    expected_git_sha: str,
    expected_template_sha256: str,
) -> List[str]:
    errors: List[str] = []
    if payload.get("version") != TRAINER_VERSION:
        errors.append(f"{prefix}_trainer_version_mismatch")
    if payload.get("experimentId") != EXPERIMENT_ID:
        errors.append(f"{prefix}_experiment_identity_mismatch")
    if payload.get("releaseCutoffUtc") != RELEASE_CUTOFF_UTC:
        errors.append(f"{prefix}_release_cutoff_mismatch")
    errors.extend(
        _identity_errors(
            payload.get("deploymentIdentity"),
            expected_git_sha=expected_git_sha,
            expected_template_sha256=expected_template_sha256,
            prefix=prefix,
        )
    )
    return errors


def _execution_lease_errors(payload: Dict[str, Any], *, prefix: str) -> List[str]:
    actual = payload.get("executionConcurrencyControl")
    if not isinstance(actual, dict):
        return [f"{prefix}_execution_lease_evidence_missing"]
    return [
        f"{prefix}_execution_lease_{key}_mismatch"
        for key, expected in EXECUTION_CONCURRENCY_CONTROL.items()
        if actual.get(key) != expected
    ]


def verify(
    *,
    training: Dict[str, Any],
    selection_capture: Dict[str, Any],
    status_after: Dict[str, Any],
    invocation_metadata: Iterable[Dict[str, Any]],
    run_started_at: str,
    expected_git_sha: str,
    expected_template_sha256: str,
) -> List[str]:
    errors: List[str] = []
    started = _parse_time(run_started_at)
    if started is None:
        errors.append("run_started_at_invalid")

    invocations = tuple(invocation_metadata)
    if len(invocations) != 3:
        errors.append("lambda_invocation_evidence_count_mismatch")
    for index, invocation in enumerate(invocations):
        if (
            not isinstance(invocation, dict)
            or invocation.get("StatusCode") != 200
            or invocation.get("FunctionError")
        ):
            errors.append(f"lambda_invocation_failed:{index}")

    payloads = (
        ("training", training),
        ("selection_capture", selection_capture),
        ("status_after", status_after),
    )
    for prefix, payload in payloads:
        errors.extend(
            _contract_errors(
                payload,
                prefix=prefix,
                expected_git_sha=expected_git_sha,
                expected_template_sha256=expected_template_sha256,
            )
        )

    if training.get("ok") is not True:
        errors.append("training_run_not_ok")
    if training.get("executionMode") != "training":
        errors.append("training_execution_mode_mismatch")
    if training.get("championChanged") is not False:
        errors.append("training_run_does_not_prove_champion_unchanged")
    if training.get("liveInferenceAuthority") is not False:
        errors.append("training_run_does_not_prove_shadow_only_authority")
    if training.get("automaticPromotionEnabled") is not False:
        errors.append("training_run_automatic_promotion_not_disabled")
    if not isinstance(training.get("milestones"), dict) or not training.get("milestones"):
        errors.append("training_run_milestones_missing")

    if selection_capture.get("ok") is not True:
        errors.append("selection_capture_run_not_ok")
    if selection_capture.get("executionMode") != "selection_capture":
        errors.append("selection_capture_execution_mode_mismatch")
    if selection_capture.get("liveInferenceAuthority") is not False:
        errors.append("selection_capture_enabled_live_inference_authority")
    if selection_capture.get("historicalTrainingScanInvoked") is not False:
        errors.append("selection_capture_invoked_historical_training_scan")
    if selection_capture.get("modelTrained") is not False:
        errors.append("selection_capture_trained_model")

    for prefix, payload in (
        ("training", training),
        ("selection_capture", selection_capture),
    ):
        errors.extend(_execution_lease_errors(payload, prefix=prefix))
        created = _parse_time(payload.get("createdAtUtc"))
        if created is None or started is None or created < started:
            errors.append(f"{prefix}_run_not_fresh_for_deploy")
        if not str(payload.get("status") or ""):
            errors.append(f"{prefix}_run_status_missing")

    if status_after.get("ok") is not True:
        errors.append("status_after_not_ok")
    if status_after.get("automaticPromotionEnabled") is not False:
        errors.append("automatic_promotion_not_disabled")
    if status_after.get("firstPromotionRequiresManualReview") is not True:
        errors.append("manual_first_promotion_not_required")
    if status_after.get("v2InferenceConsumerInstalled") is not False:
        errors.append("v2_inference_consumer_must_remain_uninstalled")
    if status_after.get("runtimeAuthorityActivationAvailable") is not False:
        errors.append("runtime_authority_activation_must_remain_unavailable")
    manifest = status_after.get("manifest")
    if not isinstance(manifest, dict) or not manifest:
        errors.append("fresh_manifest_missing")
        manifest = {}
    if manifest.get("version") != EXPERIMENT_VERSION:
        errors.append("manifest_version_mismatch")
    if manifest.get("experimentId") != EXPERIMENT_ID:
        errors.append("manifest_experiment_identity_mismatch")
    if manifest.get("releaseContractId") != EXPERIMENT_ID:
        errors.append("manifest_release_contract_identity_mismatch")
    if manifest.get("releaseCutoffUtc") != RELEASE_CUTOFF_UTC:
        errors.append("manifest_release_cutoff_mismatch")
    manifest_digest = str(manifest.get("manifestDigest") or "")
    if not manifest_digest:
        errors.append("manifest_digest_missing")
    if (
        training.get("experimentManifestDigest")
        and training.get("experimentManifestDigest") != manifest_digest
    ):
        errors.append("training_manifest_digest_mismatch")

    for health_key, expected_mode, run in (
        ("trainingHealth", "training", training),
        ("selectionCaptureHealth", "selection_capture", selection_capture),
    ):
        prefix = expected_mode
        health = status_after.get(health_key)
        if not isinstance(health, dict):
            errors.append(f"{prefix}_health_missing")
            continue
        if health.get("ok") is not True:
            errors.append(f"{prefix}_health_not_ok")
        if health.get("executionMode") != expected_mode:
            errors.append(f"{prefix}_health_execution_mode_mismatch")
        if health.get("deploymentIdentityMatches") is not True:
            errors.append(f"{prefix}_health_identity_mismatch")
        latest = health.get("latestRun")
        if not isinstance(latest, dict) or not latest:
            errors.append(f"{prefix}_latest_run_missing")
            continue
        if latest != run:
            errors.append(f"{prefix}_latest_run_does_not_match_deploy_run")
        errors.extend(
            _contract_errors(
                latest,
                prefix=f"{prefix}_latest_run",
                expected_git_sha=expected_git_sha,
                expected_template_sha256=expected_template_sha256,
            )
        )

    return sorted(set(errors))


def _read(path: str) -> Dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--training", required=True)
    parser.add_argument("--selection-capture", required=True)
    parser.add_argument("--status-after", required=True)
    parser.add_argument("--training-invocation", required=True)
    parser.add_argument("--selection-capture-invocation", required=True)
    parser.add_argument("--status-after-invocation", required=True)
    parser.add_argument("--run-started-at", required=True)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-template-sha256", required=True)
    args = parser.parse_args()

    status_after = _read(args.status_after)
    print(json.dumps(status_after, indent=2, sort_keys=True, default=str))
    errors = verify(
        training=_read(args.training),
        selection_capture=_read(args.selection_capture),
        status_after=status_after,
        invocation_metadata=(
            _read(args.training_invocation),
            _read(args.selection_capture_invocation),
            _read(args.status_after_invocation),
        ),
        run_started_at=args.run_started_at,
        expected_git_sha=args.expected_git_sha,
        expected_template_sha256=args.expected_template_sha256,
    )
    if errors:
        for error in errors:
            print(error)
        return 1
    print("Fresh AWS-native MLB trainer training and selection health verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
