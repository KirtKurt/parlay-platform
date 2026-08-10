#!/usr/bin/env python3
"""Attach absolute challenger calibration metrics from the immutable experiment.

The lightweight Lambda status response intentionally carries an immutable artifact
pointer instead of the full experiment. This helper verifies the exact S3 object
against that pointer, extracts only the selected candidate's walk-forward and
untouched-holdout metrics, and enriches the input consumed by the public status
renderer. No candidate, gate, or production authority is changed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


VERSION = "MLB-HISTORICAL-STATUS-EXPERIMENT-ENRICHMENT-v1"


def _unwrap_experiment(value: Any) -> Mapping[str, Any]:
    current = value
    for _ in range(5):
        if isinstance(current, Mapping) and isinstance(
            current.get("candidate"), Mapping
        ):
            return current
        if not isinstance(current, Mapping):
            break
        nested = None
        for key in ("data", "payload", "experiment", "searchResult", "result"):
            if isinstance(current.get(key), Mapping):
                nested = current[key]
                break
        if nested is None:
            break
        current = nested
    raise ValueError("historical experiment does not contain a candidate")


def _metric_partition(
    candidate: Mapping[str, Any], name: str
) -> dict[str, Any]:
    value = candidate.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"historical experiment candidate omitted {name}")
    required = (
        "gameCount",
        "dayCount",
        "meanDailyAccuracy",
        "minimumDailyAccuracy",
        "brierScore",
        "logLoss",
    )
    missing = [key for key in required if value.get(key) is None]
    if missing:
        raise ValueError(
            f"historical experiment {name} omitted metrics:" + ",".join(missing)
        )
    return {
        "gameCount": value.get("gameCount"),
        "dayCount": value.get("dayCount"),
        "overallAccuracy": value.get("overallAccuracy"),
        "meanDailyAccuracy": value.get("meanDailyAccuracy"),
        "minimumDailyAccuracy": value.get("minimumDailyAccuracy"),
        "minimumSlateCoverage": value.get("minimumSlateCoverage"),
        "brierScore": value.get("brierScore"),
        "logLoss": value.get("logLoss"),
        "policyDigest": value.get("policyDigest"),
    }


def enrich(
    status_response: Mapping[str, Any],
    experiment_bytes: bytes,
) -> dict[str, Any]:
    if status_response.get("ok") is not True:
        raise ValueError("optimizer status is not OK")
    output = copy.deepcopy(dict(status_response))
    state = output.get("state")
    if not isinstance(state, dict):
        raise ValueError("optimizer status omitted state")
    latest = state.get("latestExperiment")
    if not isinstance(latest, dict):
        raise ValueError("optimizer status omitted latest experiment")
    pointer = latest.get("artifact")
    if not isinstance(pointer, Mapping):
        raise ValueError("latest experiment omitted immutable artifact pointer")

    expected_sha = str(pointer.get("sha256") or "").lower()
    actual_sha = hashlib.sha256(experiment_bytes).hexdigest()
    if expected_sha and actual_sha != expected_sha:
        raise ValueError(
            f"historical experiment sha256 mismatch:{actual_sha}"
        )

    raw = json.loads(experiment_bytes.decode("utf-8"))
    experiment = _unwrap_experiment(raw)
    candidate = experiment.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError("historical experiment candidate is invalid")

    experiment_gate = experiment.get("promotionGate")
    status_gate = latest.get("promotionGate")
    if isinstance(experiment_gate, Mapping) and isinstance(status_gate, Mapping):
        for key in (
            "settledGameCount",
            "trainingGameCount",
            "walkForwardGameCount",
            "untouchedHoldoutGameCount",
            "passed",
        ):
            if (
                experiment_gate.get(key) is not None
                and status_gate.get(key) is not None
                and experiment_gate.get(key) != status_gate.get(key)
            ):
                raise ValueError(
                    f"historical experiment/status gate mismatch:{key}"
                )

    candidate_digest = str(candidate.get("policyDigest") or "")
    status_digest = str(latest.get("candidatePolicyDigest") or "")
    if candidate_digest and status_digest and candidate_digest != status_digest:
        raise ValueError("historical experiment candidate digest mismatch")

    latest["walkForwardMetrics"] = _metric_partition(
        candidate, "walkForward"
    )
    latest["untouchedHoldoutMetrics"] = _metric_partition(
        candidate, "untouchedHoldout"
    )
    latest["absoluteCalibrationMetricsSource"] = {
        "version": VERSION,
        "verifiedSha256": actual_sha,
        "bucket": pointer.get("bucket"),
        "key": pointer.get("key"),
        "versionId": pointer.get("versionId"),
        "candidatePolicyDigest": candidate_digest or status_digest or None,
        "productionAuthorityChanged": False,
        "promotionGateChanged": False,
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    status = json.loads(args.status.read_text(encoding="utf-8"))
    enriched = enrich(status, args.experiment.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(enriched, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
