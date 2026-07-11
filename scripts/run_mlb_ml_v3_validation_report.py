#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "runtime_reports" / "mlb_ml_v3_validation_latest.json"
TEMPLATE = ROOT / "template.yaml"
COMMANDS = [
    [sys.executable, "scripts/patch_template_mlb_v1.py"],
    [sys.executable, "scripts/patch_template_mlb_security.py"],
    [sys.executable, "scripts/patch_template_mlb_results_routes.py"],
    [sys.executable, "scripts/verify_mlb_official_prediction_semantics.py"],
    [sys.executable, "scripts/verify_mlb_schedule_invariants.py"],
]


def main() -> int:
    results = []
    original_template = TEMPLATE.read_bytes() if TEMPLATE.exists() else None
    try:
        for command in COMMANDS:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            results.append(
                {
                    "command": command,
                    "returnCode": completed.returncode,
                    "ok": completed.returncode == 0,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            )
            if completed.returncode != 0:
                break
    finally:
        # The audit must mirror deploy-time patching without changing the source
        # template committed to the repository.
        if original_template is not None:
            TEMPLATE.write_bytes(original_template)

    payload = {
        "ok": all(item["ok"] for item in results) and len(results) == len(COMMANDS),
        "proofType": "MLB_ML_V3_AND_DEPLOY_INVARIANT_VALIDATION",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "templatePatchOrderMirrorsDeploy": True,
        "sourceTemplateRestoredAfterValidation": original_template is not None,
        "policy": "Patch the SAM template exactly as deploy.yml does, run the mandatory production invariant, then restore the source template.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
