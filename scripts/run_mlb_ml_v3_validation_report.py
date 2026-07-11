#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "runtime_reports" / "mlb_ml_v3_validation_latest.json"
COMMANDS = [
    [sys.executable, "scripts/verify_mlb_official_prediction_semantics.py"],
    [sys.executable, "scripts/verify_mlb_complete_slate_coverage.py"],
    [sys.executable, "scripts/verify_mlb_ml_optimization_v3.py"],
]


def main() -> int:
    results = []
    for command in COMMANDS:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        results.append({
            "command": command,
            "returnCode": completed.returncode,
            "ok": completed.returncode == 0,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        })
    payload = {
        "ok": all(item["ok"] for item in results),
        "proofType": "MLB_ML_V3_VALIDATION",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
