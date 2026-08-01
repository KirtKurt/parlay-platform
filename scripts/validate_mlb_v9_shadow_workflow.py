#!/usr/bin/env python3
"""Run V9 compile/tests and persist exact per-suite failure evidence."""
from __future__ import annotations

import json
import os
import py_compile
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

OUTPUT = Path(os.environ.get("MLB_V9_VALIDATION_EVIDENCE", "/tmp/mlb-v9-validation.json"))
SOURCES = (
    "hello_world/mlb_historical_supervised_v9.py",
    "hello_world/mlb_historical_supervised_v9_integrity_v2.py",
    "hello_world/mlb_historical_rematerialization_waiting_repair_v1.py",
    "hello_world/mlb_historical_v7_context_features_v1.py",
    "hello_world/mlb_historical_v7_feature_bridge_v1.py",
    "hello_world/mlb_historical_v7_learning_cadence_v1.py",
    "hello_world/mlb_historical_v7_prior_signal_bridge_v1.py",
    "hello_world/mlb_historical_v7_priority_repairs_v1.py",
    "hello_world/mlb_historical_v7_selective_objective_v1.py",
    "hello_world/mlb_historical_v7_selective_search_v2.py",
    "hello_world/mlb_historical_optimizer_v7_recovery_entrypoint.py",
    "scripts/run_mlb_historical_supervised_v9_shadow.py",
    "scripts/run_mlb_historical_supervised_v9_shadow_cadence.py",
    "scripts/run_mlb_historical_supervised_v9_shadow_v2.py",
    "scripts/resolve_mlb_historical_artifacts_bucket.py",
    "scripts/validate_mlb_v9_shadow_workflow.py",
)
TESTS = (
    "tests/unit/test_mlb_historical_supervised_v9.py",
    "tests/unit/test_mlb_historical_supervised_v9_integrity_v2.py",
    "tests/unit/test_run_mlb_historical_supervised_v9_shadow.py",
    "tests/unit/test_run_mlb_historical_supervised_v9_shadow_cadence.py",
    "tests/unit/test_run_mlb_historical_supervised_v9_shadow_v2.py",
    "tests/unit/test_resolve_mlb_historical_artifacts_bucket.py",
    "tests/unit/test_mlb_historical_rematerialization_waiting_repair_v1.py",
    "tests/unit/test_mlb_historical_v7_context_features_v1.py",
    "tests/unit/test_mlb_historical_v7_feature_bridge_v1.py",
    "tests/unit/test_mlb_historical_v7_learning_cadence_v1.py",
    "tests/unit/test_mlb_historical_v7_prior_signal_bridge_v1.py",
    "tests/unit/test_mlb_historical_v7_priority_repairs_v1.py",
    "tests/unit/test_mlb_historical_v7_selective_objective_v1.py",
    "tests/unit/test_mlb_historical_v7_selective_search_v2.py",
    "tests/unit/test_mlb_v7_v9_workflow_recovery_contract.py",
    "tests/unit/test_validate_mlb_v9_shadow_workflow_identity.py",
)


def _tail(value: str, limit: int = 12000) -> str:
    return value[-limit:]


def _expected_source_sha() -> str:
    """Return the commit actually checked out by the workflow.

    On pull_request events GitHub sets GITHUB_SHA to a synthetic merge commit,
    while the workflow intentionally checks out pull_request.head.sha. Reading
    the event payload keeps source identity strict without rejecting valid PRs.
    """
    override = str(os.environ.get("MLB_V9_EXPECTED_SOURCE_SHA") or "").strip()
    if override:
        return override
    event_path = str(os.environ.get("GITHUB_EVENT_PATH") or "").strip()
    if event_path:
        try:
            payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
            pull_request = payload.get("pull_request") if isinstance(payload, dict) else None
            head = pull_request.get("head") if isinstance(pull_request, dict) else None
            sha = str(head.get("sha") or "").strip() if isinstance(head, dict) else ""
            if sha:
                return sha
        except (OSError, ValueError, TypeError):
            pass
    return str(os.environ.get("GITHUB_SHA") or "").strip()


def main() -> int:
    expected_source_sha = _expected_source_sha()
    evidence = {
        "proofType": "MLB_V9_WORKFLOW_VALIDATION",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha": expected_source_sha,
        "workflowSha": os.environ.get("GITHUB_SHA"),
        "compile": [],
        "tests": [],
        "ok": False,
        "blockers": [],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        for source in SOURCES:
            try:
                py_compile.compile(source, doraise=True)
                evidence["compile"].append({"path": source, "ok": True})
            except Exception as exc:
                evidence["compile"].append({
                    "path": source,
                    "ok": False,
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                    "tracebackTail": _tail(traceback.format_exc()),
                })
                evidence["blockers"].append(f"compile_failed:{source}")

        env = dict(os.environ)
        env["PYTHONPATH"] = "hello_world:."
        for test_path in TESTS:
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=short", test_path],
                cwd=Path.cwd(),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            item = {
                "path": test_path,
                "ok": completed.returncode == 0,
                "returnCode": completed.returncode,
                "outputTail": _tail(completed.stdout or ""),
            }
            evidence["tests"].append(item)
            if completed.returncode != 0:
                evidence["blockers"].append(f"test_failed:{test_path}")

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        evidence["checkedOutSha"] = (head.stdout or "").strip()
        evidence["sourceIdentityMatched"] = bool(
            expected_source_sha and evidence["checkedOutSha"] == expected_source_sha
        )
        if expected_source_sha and not evidence["sourceIdentityMatched"]:
            evidence["blockers"].append("checked_out_sha_mismatch")
        evidence["ok"] = not evidence["blockers"]
    except Exception as exc:
        evidence["blockers"].append("validation_runner_failed")
        evidence["errorType"] = type(exc).__name__
        evidence["error"] = str(exc)
        evidence["tracebackTail"] = _tail(traceback.format_exc())
    OUTPUT.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
