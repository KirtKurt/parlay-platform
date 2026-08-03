#!/usr/bin/env python3
"""Publish a latest JSON pointer without truncation or stale-run rollback.

Recurring MLB workflows race each other after rebasing onto ``main``.  This
module keeps the latest pointer monotonic, preserves the last valid document
when a producer fails, and replaces files atomically so an interrupted copy can
never leave an empty or partially written report.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

TIMESTAMP_FIELDS = (
    "createdAtUtc",
    "completedAtUtc",
    "checkedAt",
    "generatedAtUtc",
    "stateUpdatedAtUtc",
    "updatedAtUtc",
)
SUPERVISED_PROOF_TYPES = {
    "MLB_SUPERVISED_SHADOW_AWS_EVALUATION",
    "MLB_SUPERVISED_SHADOW_V2_EVIDENCE",
}
CONTEXT_FIELDS = (
    "historicalContext",
    "historicalFundamentals",
    # Backward-compatible read support for evidence produced before the
    # official/internal provider migration. No provider call is made here.
    "historicalBbsFundamentals",
)
APPLIED = "APPLIED"


def _read(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        raise ValueError(f"empty or missing JSON evidence: {path}")
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


def _context(value: Mapping[str, Any]) -> tuple[str | None, Mapping[str, Any]]:
    for field in CONTEXT_FIELDS:
        candidate = value.get(field)
        if isinstance(candidate, Mapping):
            return field, candidate
    return None, {}


def _revision(value: Mapping[str, Any]) -> int | None:
    for field in (
        "activePointerRevision",
        "contextPointerRevision",
        "pointerRevision",
        "revision",
    ):
        if field in value and value.get(field) not in (None, ""):
            try:
                return int(value[field])
            except (TypeError, ValueError):
                return None
    return None


def evidence_regression_reason(
    candidate: Mapping[str, Any], current: Mapping[str, Any]
) -> str | None:
    """Return a fail-closed reason when newer evidence loses durable authority."""
    if current.get("ok") is True and candidate.get("ok") is False:
        return "successful_evidence_cannot_be_replaced_by_failed_evidence"

    if str(candidate.get("proofType") or "") == str(current.get("proofType") or ""):
        current_revision = _revision(current)
        candidate_revision = _revision(candidate)
        if (
            current_revision is not None
            and candidate_revision is not None
            and candidate_revision < current_revision
        ):
            return "evidence_revision_regressed"

    if not (_is_supervised(candidate) and _is_supervised(current)):
        return None

    candidate_field, candidate_context = _context(candidate)
    current_field, current_context = _context(current)
    current_status = str(current_context.get("status") or "")
    candidate_status = str(candidate_context.get("status") or "")

    if current_status == APPLIED and candidate_status != APPLIED:
        if current_field == "historicalBbsFundamentals":
            return "applied_bbd_evidence_cannot_be_replaced_by_disabled_or_missing_overlay"
        return "applied_historical_context_cannot_be_replaced_by_disabled_or_missing_context"

    if current_status == APPLIED and candidate_status == APPLIED:
        current_revision = _integer(current_context.get("pointerRevision"))
        candidate_revision = _integer(candidate_context.get("pointerRevision"))
        if candidate_revision < current_revision:
            if current_field == "historicalBbsFundamentals" or candidate_field == "historicalBbsFundamentals":
                return "historical_bbd_pointer_revision_regressed"
            return "historical_context_pointer_revision_regressed"

        current_applied = _integer(current_context.get("appliedGameCount"))
        candidate_applied = _integer(candidate_context.get("appliedGameCount"))
        same_corpus = _integer(candidate_context.get("recordCount")) == _integer(
            current_context.get("recordCount")
        )
        if same_corpus and candidate_applied < current_applied:
            if current_field == "historicalBbsFundamentals" or candidate_field == "historicalBbsFundamentals":
                return "historical_bbd_applied_game_count_regressed"
            return "historical_context_applied_game_count_regressed"

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


def _atomic_copy_json(source: Path, destination: Path) -> None:
    """Copy one validated JSON object and atomically replace the destination."""
    _read(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        _read(temporary_path)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def publish(candidate_path: Path, existing_path: Path, output_path: Path) -> dict[str, Any]:
    candidate = _read(candidate_path)
    candidate_timestamp = evidence_time(candidate)
    if candidate_timestamp is None:
        raise ValueError("candidate evidence has no valid timestamp")

    current = (
        _read(existing_path)
        if existing_path.exists() and existing_path.stat().st_size > 0
        else {}
    )
    rejection_reason = evidence_regression_reason(candidate, current) if current else None
    selected = not current or candidate_is_newer(candidate, current)

    if selected:
        _atomic_copy_json(candidate_path, output_path)
    elif output_path.resolve() != existing_path.resolve():
        _atomic_copy_json(existing_path, output_path)

    return {
        "published": selected,
        "rejectionReason": rejection_reason,
        "candidateTimestamp": candidate_timestamp.isoformat(),
        "existingTimestamp": (
            evidence_time(current).isoformat()
            if current and evidence_time(current)
            else None
        ),
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
