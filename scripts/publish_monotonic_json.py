#!/usr/bin/env python3
"""Publish a JSON latest-pointer only when evidence advances monotonically.

Multiple GitHub workflows may evaluate the same shadow model. A slower, older run
must never replace a newer canonical latest report after rebasing onto main. For
V8 supervised evidence, a newer timestamp also cannot erase a previously applied
point-in-time BBD overlay or replace successful evidence with a failed run.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

TIMESTAMP_FIELDS = (
    "createdAtUtc",
    "checkedAt",
    "generatedAtUtc",
    "stateUpdatedAtUtc",
    "updatedAtUtc",
)
SUPERVISED_PROOF_TYPES = {
    "MLB_SUPERVISED_SHADOW_AWS_EVALUATION",
    "MLB_SUPERVISED_SHADOW_V2_EVIDENCE",
}
BBD_APPLIED = "APPLIED"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evidence_time(value: Mapping[str, Any]) -> datetime | None:
    for field in TIMESTAMP_FIELDS:
        parsed = _time(value.get(field))
        if parsed is not None:
            return parsed
    return None


def _run_number(value: Mapping[str, Any]) -> int | None:
    try:
        return int(str(value.get("runId") or ""))
    except ValueError:
        return None


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_supervised(value: Mapping[str, Any]) -> bool:
    return str(value.get("proofType") or "") in SUPERVISED_PROOF_TYPES


def _bbs(value: Mapping[str, Any]) -> Mapping[str, Any]:
    candidate = value.get("historicalBbsFundamentals") or {}
    return candidate if isinstance(candidate, Mapping) else {}


def evidence_regression_reason(
    candidate: Mapping[str, Any], current: Mapping[str, Any]
) -> str | None:
    """Return a fail-closed reason when newer evidence loses durable authority."""
    if current.get("ok") is True and candidate.get("ok") is False:
        return "successful_evidence_cannot_be_replaced_by_failed_evidence"

    if not (_is_supervised(candidate) and _is_supervised(current)):
        return None

    candidate_bbs = _bbs(candidate)
    current_bbs = _bbs(current)
    current_status = str(current_bbs.get("status") or "")
    candidate_status = str(candidate_bbs.get("status") or "")

    if current_status == BBD_APPLIED and candidate_status != BBD_APPLIED:
        return "applied_bbd_evidence_cannot_be_replaced_by_disabled_or_missing_overlay"

    if current_status == BBD_APPLIED and candidate_status == BBD_APPLIED:
        current_revision = _integer(current_bbs.get("pointerRevision"))
        candidate_revision = _integer(candidate_bbs.get("pointerRevision"))
        if candidate_revision < current_revision:
            return "historical_bbd_pointer_revision_regressed"

        current_applied = _integer(current_bbs.get("appliedGameCount"))
        candidate_applied = _integer(candidate_bbs.get("appliedGameCount"))
        same_corpus = _integer(candidate_bbs.get("recordCount")) == _integer(
            current_bbs.get("recordCount")
        )
        if same_corpus and candidate_applied < current_applied:
            return "historical_bbd_applied_game_count_regressed"

    return None


def candidate_is_newer(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    if evidence_regression_reason(candidate, current):
        return False

    candidate_time = evidence_time(candidate)
    current_time = evidence_time(current)
    if candidate_time is None:
        raise ValueError("candidate evidence has no valid timestamp")
    if current_time is None:
        return True
    if candidate_time != current_time:
        return candidate_time > current_time

    candidate_run = _run_number(candidate)
    current_run = _run_number(current)
    if candidate_run is not None and current_run is not None and candidate_run != current_run:
        return candidate_run > current_run
    return False


def publish(candidate_path: Path, existing_path: Path, output_path: Path) -> dict[str, Any]:
    candidate = _read(candidate_path)
    current = _read(existing_path) if existing_path.exists() and existing_path.stat().st_size else {}
    rejection_reason = evidence_regression_reason(candidate, current) if current else None
    selected = not current or candidate_is_newer(candidate, current)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if selected:
        shutil.copyfile(candidate_path, output_path)
    elif output_path.resolve() != existing_path.resolve():
        shutil.copyfile(existing_path, output_path)
    return {
        "published": selected,
        "rejectionReason": rejection_reason,
        "candidateTimestamp": evidence_time(candidate).isoformat() if evidence_time(candidate) else None,
        "existingTimestamp": evidence_time(current).isoformat() if current and evidence_time(current) else None,
        "candidateRunId": candidate.get("runId"),
        "existingRunId": current.get("runId") if current else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--existing", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = publish(args.candidate, args.existing, args.output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
