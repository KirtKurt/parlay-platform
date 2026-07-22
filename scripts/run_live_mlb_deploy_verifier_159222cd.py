#!/usr/bin/env python3
"""Run the read-only verifier and always persist a compact JSON evidence record."""

from __future__ import annotations

import json
import os
from pathlib import Path

import verify_live_mlb_deploy_159222cd as verifier


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]
    output = Path(os.environ.get("SUMMARY_PATH", "live_mlb_deploy_verification.json"))
    summary: dict[str, object] = {
        "targetGitSha": verifier.TARGET_SHA,
        "targetWorkflow": verifier.TARGET_WORKFLOW,
        "apiBase": verifier.API_BASE,
        "readOnly": True,
    }
    error: Exception | None = None
    try:
        run = verifier.locate_deployment(repository, token)
        run_id = int(run["id"])
        run_attempt = int(run.get("run_attempt") or 1)
        summary["deploymentRun"] = {
            "id": run_id,
            "attempt": run_attempt,
            "name": run.get("name"),
            "event": run.get("event"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "headBranch": run.get("head_branch"),
            "headSha": run.get("head_sha"),
            "createdAt": run.get("created_at"),
            "updatedAt": run.get("updated_at"),
            "url": run.get("html_url"),
        }
        jobs = verifier.summarize_jobs(repository, token, run_id)
        summary["deploymentJobs"] = jobs
        if run.get("conclusion") != "success":
            raise RuntimeError(f"Exact deployment concluded {run.get('conclusion')}")
        deploy_jobs = [job for job in jobs if job.get("name") == "deploy"]
        if len(deploy_jobs) != 1 or deploy_jobs[0].get("conclusion") != "success":
            raise RuntimeError("Exact deployment job is not uniquely successful")
        summary["deploymentIdentity"] = verifier.verify_identity(
            repository,
            token,
            run_id,
            run_attempt,
        )
        summary["livePublicReads"] = verifier.verify_live_reads()
        summary["ok"] = True
    except Exception as exc:  # evidence must survive any verifier failure
        error = exc
        summary["ok"] = False
        summary["errorType"] = type(exc).__name__
        summary["error"] = str(exc)
    finally:
        output.write_text(
            json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        print("VERIFICATION_EVIDENCE " + json.dumps(summary, sort_keys=True, default=str))
    if error is not None:
        raise error


if __name__ == "__main__":
    main()
