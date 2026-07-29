#!/usr/bin/env python3
"""Publish a JSON latest-pointer only when the candidate evidence is newer.

Multiple GitHub workflows may evaluate the same shadow model. A slower, older run
must never replace a newer canonical latest report after rebasing onto main.
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


def candidate_is_newer(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
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
    selected = not current or candidate_is_newer(candidate, current)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if selected:
        shutil.copyfile(candidate_path, output_path)
    elif output_path.resolve() != existing_path.resolve():
        shutil.copyfile(existing_path, output_path)
    return {
        "published": selected,
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
