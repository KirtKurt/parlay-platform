"""Stamp clean ARB Python sources before SAM build; verify that identity live.

No deployment or code-writing agent is invoked. The stamp is an uncommitted
build artifact. Existing release/repair workflows own deployment and secrets.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))
from release_identity import FIELDS, MANIFEST_NAME, RUN_ID, SHA1, read_identity, source_fingerprint


def stamp(source: Path, run_id: str, run_attempt: str) -> dict[str, Any]:
    if not RUN_ID.fullmatch(run_id) or not RUN_ID.fullmatch(run_attempt):
        raise ValueError("WORKFLOW_RUN_ID_AND_ATTEMPT_REQUIRED")
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=source, text=True).strip())
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if not SHA1.fullmatch(revision):
        raise ValueError("INVALID_GIT_REVISION")
    relative = source.resolve().relative_to(root.resolve()).as_posix()
    subprocess.run(["git", "diff", "--exit-code", "--quiet", "HEAD", "--", relative], cwd=root, check=True)
    tracked = subprocess.check_output(["git", "ls-files", "-z", "--", relative], cwd=root)
    tracked_paths = {p.decode("utf-8") for p in tracked.split(b"\0") if p}
    actual_python = {p.relative_to(root).as_posix() for p in source.rglob("*.py") if "__pycache__" not in p.parts}
    tracked_python = {p for p in tracked_paths if p.endswith(".py") and "/__pycache__/" not in p}
    if actual_python != tracked_python:
        raise ValueError("UNTRACKED_OR_MISSING_PYTHON_SOURCE")
    if relative + "/" + MANIFEST_NAME in tracked_paths:
        raise ValueError("RELEASE_MANIFEST_MUST_BE_BUILD_ONLY")
    source_hash, count = source_fingerprint(source)
    data = {
        "schema_version": 1, "revision": revision,
        "python_source_sha256": source_hash, "python_source_files": count,
        "workflow_run_id": run_id, "workflow_run_attempt": run_attempt,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    target = source / MANIFEST_NAME
    if target.is_symlink():
        raise ValueError("MANIFEST_SYMLINK")
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checked = read_identity(source)
    if checked.get("status") != "verified":
        raise ValueError("STAMP_READBACK_FAILED")
    return checked


def assert_live_identity(health: Any, expected: dict[str, Any]) -> dict[str, Any]:
    if expected.get("status") != "verified":
        raise ValueError("LOCAL_RELEASE_IDENTITY_UNVERIFIED")
    if not isinstance(health, dict) or health.get("ok") is not True or health.get("places_bets") is not False:
        raise ValueError("LIVE_ARB_HEALTH_FAILED")
    actual = health.get("release")
    if not isinstance(actual, dict) or actual.get("status") != "verified":
        raise ValueError("LIVE_RELEASE_IDENTITY_UNVERIFIED")
    if any(type(actual.get(key)) is not type(expected[key]) or actual.get(key) != expected[key] for key in FIELDS):
        raise ValueError("LIVE_RELEASE_IDENTITY_MISMATCH")
    return {"ok": True, "kind": "ARB_PRIMARY_LAMBDA_PYTHON_RELEASE_IDENTITY", "release": {key: actual[key] for key in FIELDS}}


def verify_live(api_url: str, expected: dict[str, Any], *, attempts: int = 6) -> dict[str, Any]:
    if expected.get("status") != "verified":
        raise ValueError("LOCAL_RELEASE_IDENTITY_UNVERIFIED")
    if not api_url.startswith("https://"):
        raise ValueError("HTTPS_API_URL_REQUIRED")
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(api_url.rstrip("/") + "/v1/arb/health", headers={"accept": "application/json"})
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise ValueError("OVERSIZE_HEALTH_RESPONSE")
                health = json.loads(raw)
            proof = assert_live_identity(health, expected)
            proof["checked_at"] = datetime.now(timezone.utc).isoformat()
            proof["attempt"] = attempt + 1
            return proof
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt + 1 == attempts:
                raise
            time.sleep(min(2 * (attempt + 1), 10))
    raise ValueError("NO_VERIFICATION_ATTEMPTS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("stamp", "verify"))
    parser.add_argument("--api-url", default="")
    parser.add_argument("--output", default="arb-release-proof.json")
    args = parser.parse_args()
    try:
        if args.mode == "stamp":
            result = stamp(SOURCE, os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", ""))
        else:
            result = verify_live(args.api_url, read_identity(SOURCE))
            Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        # Preserve a failed proof without logging responses or environment values.
        reason = str(exc) if re.fullmatch(r"[A-Z][A-Z_]{1,79}", str(exc)) else "RELEASE_PROOF_FAILED"
        failure = {"ok": False, "reason": reason, "error_type": type(exc).__name__,
                   "checked_at": datetime.now(timezone.utc).isoformat()}
        if args.mode == "verify":
            Path(args.output).write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
