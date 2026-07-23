#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_DIR = ROOT / ".bootstrap"
EXPECTED = (
    "hello_world/mlb_temporal_features_v1.py",
    "hello_world/mlb_official_lock_quality_gate.py",
    "hello_world/mlb_accuracy_target_policy_v1.py",
    "scripts/verify_mlb_accuracy_target_separation.py",
    "tests/unit/test_mlb_official_lock_quality_gate.py",
    "tests/unit/test_mlb_temporal_features_v2.py",
    "tests/unit/test_mlb_reversal_similarity_v2.py",
    "tests/unit/test_mlb_precision_admission_gate_v1.py",
    "tests/unit/test_mlb_official_lock_quality_gate_v2.py",
    "runtime_reports/mlb_reversal_signal_research_20260723.json",
    "docs/mlb_reversal_signal_policy_v2.md",
)


def main() -> int:
    parts = sorted(PAYLOAD_DIR.glob("mlb-reversal-payload-*.b64"))
    if len(parts) != 4:
        raise RuntimeError(f"expected four payload chunks, found {len(parts)}")
    encoded = "".join(path.read_text(encoding="utf-8").strip() for path in parts)
    payload = base64.b64decode(encoded, validate=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise RuntimeError(f"link member prohibited: {member.name}")
            target = (ROOT / member.name).resolve()
            if target != ROOT and ROOT not in target.parents:
                raise RuntimeError(f"path traversal prohibited: {member.name}")
        archive.extractall(ROOT)
    missing = [path for path in EXPECTED if not (ROOT / path).is_file()]
    if missing:
        raise RuntimeError(f"payload did not materialize expected files: {missing}")
    print(f"materialized {len(EXPECTED)} MLB reversal precision-gate files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
