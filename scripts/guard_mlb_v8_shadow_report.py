#!/usr/bin/env python3
"""Publish the canonical V8 shadow report without allowing stale regression."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


REPORT_GUARD_VERSION = "MLB-V8-SHADOW-REPORT-GUARD-v1"


class ReportGuardError(ValueError):
    """Raised when an incoming report cannot be used as authoritative evidence."""


@dataclass(frozen=True)
class UpdateDecision:
    action: str
    reason: str
    incoming_created_at_utc: str
    existing_created_at_utc: Optional[str]

    @property
    def publish(self) -> bool:
        return self.action == "PUBLISH"

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": REPORT_GUARD_VERSION,
            "action": self.action,
            "reason": self.reason,
            "incomingCreatedAtUtc": self.incoming_created_at_utc,
            "existingCreatedAtUtc": self.existing_created_at_utc,
        }


def _read_object(path: Path, *, required: bool) -> Optional[dict[str, Any]]:
    if not path.exists():
        if required:
            raise ReportGuardError(f"required report is missing: {path}")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if required:
            raise ReportGuardError(f"report is not valid UTF-8 JSON: {path}") from exc
        return None
    if not isinstance(value, dict):
        if required:
            raise ReportGuardError(f"report must be a JSON object: {path}")
        return None
    return value


def _parse_created_at(report: Mapping[str, Any], *, required: bool) -> Optional[datetime]:
    raw = report.get("createdAtUtc")
    if raw in (None, ""):
        if required:
            raise ReportGuardError("incoming report createdAtUtc is missing")
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        if required:
            raise ReportGuardError("incoming report createdAtUtc is invalid") from exc
        return None
    if parsed.tzinfo is None:
        if required:
            raise ReportGuardError("incoming report createdAtUtc must include a timezone")
        return None
    return parsed.astimezone(timezone.utc)


def _canonical_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _numeric_run_id(report: Mapping[str, Any]) -> Optional[int]:
    raw = str(report.get("runId") or "").strip()
    return int(raw) if raw.isdigit() else None


def decide_update(
    incoming: Mapping[str, Any],
    existing: Optional[Mapping[str, Any]],
) -> UpdateDecision:
    incoming_time = _parse_created_at(incoming, required=True)
    assert incoming_time is not None
    incoming_time_text = _canonical_time(incoming_time)

    if existing is None:
        return UpdateDecision("PUBLISH", "canonical_report_missing_or_invalid", incoming_time_text, None)

    existing_time = _parse_created_at(existing, required=False)
    if existing_time is None:
        return UpdateDecision("PUBLISH", "canonical_timestamp_missing_or_invalid", incoming_time_text, None)

    existing_time_text = _canonical_time(existing_time)
    if incoming_time > existing_time:
        return UpdateDecision("PUBLISH", "incoming_report_is_newer", incoming_time_text, existing_time_text)
    if incoming_time < existing_time:
        return UpdateDecision("SKIP", "incoming_report_is_stale", incoming_time_text, existing_time_text)

    if dict(incoming) == dict(existing):
        return UpdateDecision("SKIP", "incoming_report_is_duplicate", incoming_time_text, existing_time_text)

    incoming_run_id = _numeric_run_id(incoming)
    existing_run_id = _numeric_run_id(existing)
    if incoming_run_id is not None and existing_run_id is not None:
        if incoming_run_id > existing_run_id:
            return UpdateDecision("PUBLISH", "equal_timestamp_higher_run_id", incoming_time_text, existing_time_text)
        return UpdateDecision("SKIP", "equal_timestamp_nonadvancing_run_id", incoming_time_text, existing_time_text)

    return UpdateDecision("SKIP", "equal_timestamp_incomparable_evidence", incoming_time_text, existing_time_text)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = source.read_bytes()
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(source_bytes)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def guarded_update(*, incoming_path: Path, destination_path: Path) -> UpdateDecision:
    incoming = _read_object(incoming_path, required=True)
    assert incoming is not None
    existing = _read_object(destination_path, required=False)
    decision = decide_update(incoming, existing)
    if decision.publish:
        _atomic_copy(incoming_path, destination_path)
    return decision


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--incoming", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    try:
        decision = guarded_update(
            incoming_path=args.incoming,
            destination_path=args.destination,
        )
    except ReportGuardError as exc:
        print(
            json.dumps(
                {
                    "version": REPORT_GUARD_VERSION,
                    "action": "ERROR",
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(decision.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
