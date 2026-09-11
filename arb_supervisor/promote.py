#!/usr/bin/env python3
"""Supervisor-owned autonomous promotion for validated Inqsi ARB Codex PRs.

The Codex subprocess never imports or edits this module and never receives the
GitHub/AWS publication credentials used by surrounding workflows. This helper
uses GitHub's `gh` CLI only after the coding wrapper has independently validated,
committed, pushed, and opened a draft PR.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from arb_supervisor.validate_candidate import SHA_RE, validate_candidate

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
POLL_SECONDS = 2
MAX_POLLS = 90


class PromotionError(RuntimeError):
    pass


def command(args: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode != 0:
        # Do not echo command environments or token-bearing output.
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "command failed"
        raise PromotionError(f"COMMAND_FAILED:{args[0]}:{detail[:240]}")
    return result.stdout.strip()


def gh_json(args: list[str]) -> Any:
    raw = command(["gh", *args])
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PromotionError("INVALID_GITHUB_JSON") from exc


def api(repo: str, suffix: str) -> Any:
    return gh_json(["api", f"repos/{repo}/{suffix}"])


def pr_info(repo: str, number: int) -> dict[str, Any]:
    data = api(repo, f"pulls/{number}")
    if not isinstance(data, dict):
        raise PromotionError("INVALID_PR_RESPONSE")
    return data


def verify_pr(data: dict[str, Any], *, branch: str, head_sha: str, require_draft: bool) -> None:
    expected = {
        "state": "open",
        "base": "main",
        "branch": branch,
        "sha": head_sha,
        "creator": "github-actions[bot]",
    }
    actual = {
        "state": data.get("state"),
        "base": (data.get("base") or {}).get("ref"),
        "branch": (data.get("head") or {}).get("ref"),
        "sha": (data.get("head") or {}).get("sha"),
        "creator": (data.get("user") or {}).get("login"),
    }
    if actual != expected or bool(data.get("merged")):
        raise PromotionError(f"PR_IDENTITY_MISMATCH:{actual}")
    if require_draft and data.get("draft") is not True:
        raise PromotionError("PR_NOT_DRAFT")


def wait_for_run(*, workflow: str, expected_title: str, event: str = "workflow_dispatch") -> dict[str, Any]:
    found_id: int | None = None
    for _ in range(MAX_POLLS):
        rows = gh_json([
            "run", "list", "--workflow", workflow, "--event", event, "--limit", "30",
            "--json", "databaseId,displayTitle,status,conclusion,headSha,createdAt",
        ])
        if not isinstance(rows, list):
            raise PromotionError("INVALID_RUN_LIST")
        matching = [row for row in rows if row.get("displayTitle") == expected_title]
        if matching:
            matching.sort(key=lambda row: row.get("databaseId", 0), reverse=True)
            found_id = int(matching[0]["databaseId"])
            break
        time.sleep(POLL_SECONDS)
    if found_id is None:
        raise PromotionError(f"WORKFLOW_RUN_NOT_FOUND:{workflow}")

    for _ in range(MAX_POLLS):
        run = gh_json(["run", "view", str(found_id), "--json", "databaseId,displayTitle,status,conclusion,headSha,event,jobs"])
        if run.get("status") == "completed":
            if run.get("conclusion") != "success":
                raise PromotionError(f"WORKFLOW_FAILED:{workflow}:{run.get('conclusion')}")
            return run
        time.sleep(POLL_SECONDS)
    raise PromotionError(f"WORKFLOW_TIMEOUT:{workflow}:{found_id}")


def ensure_no_relevant_main_advance(repo_dir: Path, base_sha: str) -> str:
    command(["git", "fetch", "--no-tags", "origin", "main"], cwd=repo_dir)
    current_main = command(["git", "rev-parse", "origin/main"], cwd=repo_dir)
    if current_main == base_sha:
        return current_main
    output = command([
        "git", "diff", "--name-only", base_sha, current_main, "--",
        "inqsi-arb", ".github/workflows/inqsi-arb-*", "arb_supervisor",
        ".github/workflows/arb-supervisor-validate.yml",
    ], cwd=repo_dir)
    if output.strip():
        raise PromotionError("RELEVANT_MAIN_ADVANCED")
    return current_main


def dispatch_validation(branch: str, candidate_sha: str, base_sha: str) -> dict[str, Any]:
    command([
        "gh", "workflow", "run", "arb-supervisor-validate.yml", "--ref", "main",
        "-f", f"candidate_sha={candidate_sha}",
        "-f", f"candidate_branch={branch}",
        "-f", f"base_sha={base_sha}",
    ])
    title = f"ARB supervisor {candidate_sha}"
    run = wait_for_run(workflow="arb-supervisor-validate.yml", expected_title=title)
    jobs = {job.get("name"): job.get("conclusion") for job in run.get("jobs") or []}
    if jobs.get("validate-exact-candidate") != "success":
        raise PromotionError(f"TRUSTED_VALIDATION_JOB_NOT_GREEN:{jobs}")
    return run


def mark_ready(repo: str, pr_number: int) -> None:
    command(["gh", "pr", "ready", str(pr_number), "--repo", repo])


def merge_exact(repo: str, pr_number: int, head_sha: str) -> str:
    result = gh_json([
        "api", "--method", "PUT", f"repos/{repo}/pulls/{pr_number}/merge",
        "-f", f"sha={head_sha}", "-f", "merge_method=squash",
    ])
    if not isinstance(result, dict) or result.get("merged") is not True:
        raise PromotionError(f"MERGE_REJECTED:{(result or {}).get('message') if isinstance(result, dict) else 'invalid'}")
    merge_sha = result.get("sha")
    if not isinstance(merge_sha, str) or not SHA_RE.fullmatch(merge_sha):
        raise PromotionError("INVALID_MERGE_SHA")
    return merge_sha


def dispatch_deploy(merge_sha: str) -> dict[str, Any]:
    command([
        "gh", "workflow", "run", "inqsi-arb-deploy.yml", "--ref", "main",
        "-f", f"arb_merge_sha={merge_sha}",
    ])
    title = f"Deploy Inqsi Arb {merge_sha}"
    run = wait_for_run(workflow="inqsi-arb-deploy.yml", expected_title=title)
    jobs = {job.get("name"): job.get("conclusion") for job in run.get("jobs") or []}
    if jobs.get("test-build") != "success" or jobs.get("deploy-verify") != "success":
        raise PromotionError(f"DEPLOY_JOBS_NOT_GREEN:{jobs}")
    return run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=Path, default=Path("."))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--auto-deploy", action="store_true")
    args = parser.parse_args()

    if not REPO_RE.fullmatch(args.repository):
        raise PromotionError("INVALID_REPOSITORY")
    if not os.environ.get("GITHUB_TOKEN"):
        raise PromotionError("GITHUB_TOKEN_REQUIRED")

    repo_dir = args.repo_dir.resolve()
    paths = validate_candidate(repo_dir, args.branch, args.head_sha, args.base_sha)
    initial = pr_info(args.repository, args.pr_number)
    verify_pr(initial, branch=args.branch, head_sha=args.head_sha, require_draft=True)
    ensure_no_relevant_main_advance(repo_dir, args.base_sha)

    validation = dispatch_validation(args.branch, args.head_sha, args.base_sha)

    # Re-read after asynchronous validation and re-check main drift before any mutation.
    before_merge = pr_info(args.repository, args.pr_number)
    verify_pr(before_merge, branch=args.branch, head_sha=args.head_sha, require_draft=True)
    ensure_no_relevant_main_advance(repo_dir, args.base_sha)

    mark_ready(args.repository, args.pr_number)
    ready = pr_info(args.repository, args.pr_number)
    verify_pr(ready, branch=args.branch, head_sha=args.head_sha, require_draft=False)
    if ready.get("draft") is True:
        raise PromotionError("PR_STILL_DRAFT")

    merge_sha = merge_exact(args.repository, args.pr_number, args.head_sha)
    proof: dict[str, Any] = {
        "ok": True,
        "repository": args.repository,
        "pr_number": args.pr_number,
        "candidate_sha": args.head_sha,
        "base_sha": args.base_sha,
        "changed_files": paths,
        "validation_run_id": validation.get("databaseId"),
        "merge_sha": merge_sha,
        "deployed": False,
    }

    if args.auto_deploy:
        deployment = dispatch_deploy(merge_sha)
        proof.update({"deployed": True, "deployment_run_id": deployment.get("databaseId")})

    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(1)
