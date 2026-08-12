#!/usr/bin/env python3
"""Decide whether V10 must consume a newer material historical state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

VERSION = "MLB-V10-MATERIAL-HANDOFF-v1-fail-closed"
_NUMERIC_FIELDS = (
    "eligibleGameCount",
    "completeSlateCount",
    "featureRematerializedSlateCount",
)


def _i(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _state(value: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = value.get("state")
    return nested if isinstance(nested, Mapping) else value


def historical_anchor(value: Mapping[str, Any]) -> dict[str, Any]:
    state = _state(value)
    completed = list(state.get("completedSlates") or [])
    return {
        "eligibleGameCount": _i(state.get("eligibleGameCount")),
        "completeSlateCount": _i(
            state.get("completeSlateCount") or len(completed)
        ),
        "featureRematerializedSlateCount": _i(
            state.get("featureRematerializedSlateCount")
        ),
        "featureDatasetVersion": str(state.get("featureDatasetVersion") or ""),
    }


def report_anchor(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("ok") is not True:
        return None
    explicit = value.get("cadenceAnchor")
    if isinstance(explicit, Mapping):
        return {
            "eligibleGameCount": _i(explicit.get("eligibleGameCount")),
            "completeSlateCount": _i(explicit.get("completeSlateCount")),
            "featureRematerializedSlateCount": _i(
                explicit.get("featureRematerializedSlateCount")
            ),
            "featureDatasetVersion": str(
                explicit.get("featureDatasetVersion") or ""
            ),
        }
    state = value.get("state") if isinstance(value.get("state"), Mapping) else {}
    proof = (
        value.get("canonicalCorpusProof")
        if isinstance(value.get("canonicalCorpusProof"), Mapping)
        else {}
    )
    return {
        "eligibleGameCount": _i(
            state.get("eligibleGameCount") or value.get("settledGameCount")
        ),
        "completeSlateCount": _i(
            state.get("completeSlateCount") or proof.get("completedSlateCount")
        ),
        "featureRematerializedSlateCount": _i(
            state.get("featureRematerializedSlateCount")
            or state.get("completeSlateCount")
            or proof.get("completedSlateCount")
        ),
        "featureDatasetVersion": str(
            state.get("featureDatasetVersion")
            or value.get("featureDatasetVersion")
            or ""
        ),
    }


def decide(
    historical_status: Mapping[str, Any],
    v10_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    current = historical_anchor(historical_status)
    previous = report_anchor(v10_report)
    blockers: list[str] = []
    changed: list[str] = []

    if historical_status.get("ok") is False:
        blockers.append("historical_status_not_ok")
    for field in _NUMERIC_FIELDS:
        if current[field] <= 0:
            blockers.append(f"historical_anchor_missing:{field}")
    if not current["featureDatasetVersion"]:
        blockers.append("historical_anchor_missing:featureDatasetVersion")

    if previous is None:
        if not blockers:
            changed.append("v10_report_missing_or_invalid")
    else:
        for field in _NUMERIC_FIELDS:
            if current[field] < previous[field]:
                blockers.append(
                    f"historical_state_regressed:{field}:"
                    f"{current[field]}<{previous[field]}"
                )
            elif current[field] > previous[field]:
                changed.append(field)
        if current["featureDatasetVersion"] != previous["featureDatasetVersion"]:
            changed.append("featureDatasetVersion")

    dispatch_required = bool(changed) and not blockers
    if blockers:
        reason = "HISTORICAL_MATERIAL_STATE_INVALID_OR_REGRESSED"
    elif dispatch_required:
        reason = "V10_BEHIND_MATERIAL_HISTORICAL_STATE"
    else:
        reason = "V10_MATERIAL_STATE_CURRENT"

    return {
        "ok": not blockers,
        "version": VERSION,
        "dispatchRequired": dispatch_required,
        "reason": reason,
        "changedFields": sorted(set(changed)),
        "blockers": sorted(set(blockers)),
        "historicalAnchor": current,
        "v10Anchor": previous,
    }


def _load(path: Path, *, optional: bool = False) -> Mapping[str, Any] | None:
    if not path.exists():
        if optional:
            return None
        raise FileNotFoundError(path)
    try:
        value = json.loads(path.read_text())
    except Exception:
        if optional:
            return None
        raise
    if not isinstance(value, Mapping):
        if optional:
            return None
        raise ValueError(f"JSON document is not an object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-status", required=True)
    parser.add_argument("--v10-report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    status = _load(Path(args.historical_status))
    report = _load(Path(args.v10_report), optional=True)
    assert status is not None
    result = decide(status, report)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
