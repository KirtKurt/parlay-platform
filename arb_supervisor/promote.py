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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arb_supervisor.validate_candidate import SHA_RE, validate_candidate

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
POLL_SECONDS = 10
DISCOVERY_TIMEOUT = 180
VALIDATION_TIMEOUT = 25 * 60
DEPLOYMENT_TIMEOUT = 50 * 60


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


def verify_pr(data: dict[str, Any], *, branch: str, head_sha: str, require_draft: bool, repository: str) -> None:
    expected = {
        "state": "open",
        "base": "main",
        "branch": branch,
        "sha": head_sha,
        "creator": "github-actions[bot]",
        "head_repo": repository,
        "base_repo": repository,
    }
    actual = {
        "state": data.get("state"),
        "base": (data.get("base") or {}).get("ref"),
        "branch": (data.get("head") or {}).get("ref"),
        "sha": (data.get("head") or {}).get("sha"),
        "creator": (data.get("user") or {}).get("login"),
        "head_repo": ((data.get("head") or {}).get("repo") or {}).get("full_name"),
        "base_repo": ((data.get("base") or {}).get("repo") or {}).get("full_name"),
    }
    if actual != expected or bool(data.get("merged")):
        raise PromotionError(f"PR_IDENTITY_MISMATCH:{actual}")
    if require_draft and data.get("draft") is not True:
        raise PromotionError("PR_NOT_DRAFT")


def require_pr_ci(repo: str, number: int, head_sha: str) -> None:
    """Never replace pending/blocked PR Actions approval with a new dispatch."""
    rows = api(repo, f"actions/runs?head_sha={head_sha}&event=pull_request&per_page=100")
    runs = rows.get("workflow_runs") if isinstance(rows, dict) else None
    if not isinstance(runs, list) or not runs or rows.get("total_count", 0) > 100:
        raise PromotionError("PR_CI_EVIDENCE_MISSING_OR_INCOMPLETE")
    relevant = [r for r in runs if r.get("head_sha") == head_sha
                and r.get("event") == "pull_request"
                and any(p.get("number") == number for p in r.get("pull_requests", []))]
    latest = {}
    for run in relevant:
        key = run.get("workflow_id")
        if key not in latest or run.get("id", 0) > latest[key].get("id", 0):
            latest[key] = run
    if not latest:
        raise PromotionError("PR_CI_EVIDENCE_MISSING_OR_INCOMPLETE")
    for run in latest.values():
        if run.get("conclusion") == "action_required":
            raise PromotionError(f"PR_ACTIONS_APPROVAL_REQUIRED:{run.get('id')}")
        if run.get("status") != "completed" or run.get("conclusion") not in ("success", "skipped"):
            raise PromotionError(f"PR_CI_NOT_GREEN:{run.get('id')}")
    build_runs = [r for r in latest.values()
                  if r.get("path") == ".github/workflows/inqsi-arb-deploy.yml"
                  and r.get("conclusion") == "success"]
    if len(build_runs) != 1:
        raise PromotionError("PR_BUILD_EVIDENCE_MISSING")
    require_jobs(repo, build_runs[0]["id"], {"test-build"})


def require_jobs(repo: str, run_id: int, expected: set[str]) -> list[dict[str, Any]]:
    result = api(repo, f"actions/runs/{run_id}/jobs?per_page=100")
    jobs = result.get("jobs") if isinstance(result, dict) else None
    if not isinstance(jobs, list) or result.get("total_count", 0) > 100:
        raise PromotionError("JOB_EVIDENCE_INCOMPLETE")
    for name in expected:
        matches = [j for j in jobs if j.get("name") == name]
        if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
            raise PromotionError(f"REQUIRED_JOB_NOT_GREEN:{name}")
    return jobs


def run_matches(run: dict[str, Any], *, workflow: str, title: str,
                trusted_sha: str, dispatched_at: str) -> bool:
    return (run.get("display_title") == title
            and run.get("event") == "workflow_dispatch"
            and run.get("head_branch") == "main"
            and run.get("head_sha") == trusted_sha
            and run.get("path") == f".github/workflows/{workflow}"
            and run.get("created_at", "") >= dispatched_at)


def wait_for_run(*, repo: str, workflow: str, expected_title: str,
                 trusted_sha: str, dispatched_at: str, timeout: int) -> dict[str, Any]:
    binding = dict(workflow=workflow, title=expected_title,
                   trusted_sha=trusted_sha, dispatched_at=dispatched_at)
    deadline = time.monotonic() + DISCOVERY_TIMEOUT
    found_id = None
    while time.monotonic() < deadline:
        data = api(repo, f"actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=100")
        rows = data.get("workflow_runs") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise PromotionError("INVALID_RUN_LIST")
        matches = [r for r in rows if run_matches(r, **binding)]
        if len(matches) > 1:
            raise PromotionError("AMBIGUOUS_DISPATCH_RECEIPT")
        if matches:
            found_id = matches[0]["id"]
            break
        time.sleep(POLL_SECONDS)
    if found_id is None:
        raise PromotionError(f"WORKFLOW_RUN_NOT_FOUND:{workflow}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = api(repo, f"actions/runs/{found_id}")
        if not isinstance(run, dict) or run.get("id") != found_id or not run_matches(run, **binding):
            raise PromotionError("DISPATCH_RECEIPT_IDENTITY_CHANGED")
        if run.get("status") == "completed":
            if run.get("conclusion") != "success":
                raise PromotionError(f"WORKFLOW_FAILED:{workflow}:{run.get('conclusion')}")
            return run
        time.sleep(POLL_SECONDS)
    raise PromotionError(f"WORKFLOW_TIMEOUT:{workflow}:{found_id}")


def dispatch_bound(repo: str, workflow: str, title: str, fields: dict[str, str],
                   timeout: int) -> dict[str, Any]:
    trusted_sha = api(repo, "branches/main")["commit"]["sha"]
    if not SHA_RE.fullmatch(trusted_sha):
        raise PromotionError("INVALID_TRUSTED_MAIN_SHA")
    nonce = uuid.uuid4().hex
    dispatched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    args = ["gh", "workflow", "run", workflow, "--repo", repo, "--ref", "main"]
    for key, value in {**fields, "dispatch_nonce": nonce}.items():
        args.extend(["-f", f"{key}={value}"])
    command(args)
    return wait_for_run(repo=repo, workflow=workflow, expected_title=f"{title} {nonce}",
                        trusted_sha=trusted_sha, dispatched_at=dispatched_at, timeout=timeout)


def ensure_no_relevant_main_advance(repo_dir: Path, base_sha: str) -> str:
    command(["git", "fetch", "--no-tags", "origin", "main"], cwd=repo_dir)
    current_main = command(["git", "rev-parse", "origin/main"], cwd=repo_dir)
    command(["git", "merge-base", "--is-ancestor", base_sha, current_main], cwd=repo_dir)
    if current_main == base_sha:
        return current_main
    output = command([
        "git", "diff", "--no-renames", "--name-only", base_sha, current_main, "--",
        "inqsi-arb", ".github/workflows/inqsi-arb-*", "arb_supervisor",
        ".github/workflows/arb-supervisor-validate.yml",
    ], cwd=repo_dir)
    if output.strip():
        raise PromotionError("RELEVANT_MAIN_ADVANCED")
    return current_main


def dispatch_validation(repo: str, branch: str, candidate_sha: str, base_sha: str) -> dict[str, Any]:
    run = dispatch_bound(repo, "arb-supervisor-validate.yml", f"ARB supervisor {candidate_sha}",
                         {"candidate_sha": candidate_sha, "candidate_branch": branch,
                          "base_sha": base_sha}, VALIDATION_TIMEOUT)
    require_jobs(repo, run["id"], {"validate-exact-candidate"})
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


def verify_merge_parent(repo: str, merge_sha: str, checked_main: str, head_sha: str) -> None:
    merged = api(repo, f"git/commits/{merge_sha}")
    if [p.get("sha") for p in merged.get("parents", [])] != [checked_main]:
        raise PromotionError(f"MERGED_BASE_CHANGED_DEPLOYMENT_BLOCKED:{merge_sha}")
    # Verify the landed ARB tree is the validated candidate, even if unrelated
    # files changed on main before the checked merge parent.
    for prefix in ("inqsi-arb", "arb_supervisor"):
        expected = api(repo, f"git/trees/{head_sha}")
        actual = api(repo, f"git/trees/{merge_sha}")
        a = [t.get("sha") for t in expected.get("tree", []) if t.get("path") == prefix]
        b = [t.get("sha") for t in actual.get("tree", []) if t.get("path") == prefix]
        if len(a) != 1 or a != b:
            raise PromotionError(f"MERGED_TREE_MISMATCH:{merge_sha}:{prefix}")


def dispatch_deploy(repo: str, merge_sha: str) -> dict[str, Any]:
    run = dispatch_bound(repo, "inqsi-arb-deploy.yml", f"Deploy Inqsi Arb {merge_sha}",
                         {"arb_merge_sha": merge_sha}, DEPLOYMENT_TIMEOUT)
    require_jobs(repo, run["id"], {"test-build", "deploy-verify"})
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
    verify_pr(initial, branch=args.branch, head_sha=args.head_sha, require_draft=True, repository=args.repository)
    ensure_no_relevant_main_advance(repo_dir, args.base_sha)

    require_pr_ci(args.repository, args.pr_number, args.head_sha)
    validation = dispatch_validation(args.repository, args.branch, args.head_sha, args.base_sha)

    # Re-read after asynchronous validation and re-check main drift before any mutation.
    before_merge = pr_info(args.repository, args.pr_number)
    verify_pr(before_merge, branch=args.branch, head_sha=args.head_sha, require_draft=True, repository=args.repository)
    ensure_no_relevant_main_advance(repo_dir, args.base_sha)

    mark_ready(args.repository, args.pr_number)
    ready = pr_info(args.repository, args.pr_number)
    verify_pr(ready, branch=args.branch, head_sha=args.head_sha, require_draft=False, repository=args.repository)
    if ready.get("draft") is True:
        raise PromotionError("PR_STILL_DRAFT")

    require_pr_ci(args.repository, args.pr_number, args.head_sha)
    checked_main = ensure_no_relevant_main_advance(repo_dir, args.base_sha)
    merge_sha = merge_exact(args.repository, args.pr_number, args.head_sha)
    verify_merge_parent(args.repository, merge_sha, checked_main, args.head_sha)
    proof: dict[str, Any] = {
        "ok": True,
        "repository": args.repository,
        "pr_number": args.pr_number,
        "candidate_sha": args.head_sha,
        "base_sha": args.base_sha,
        "changed_files": paths,
        "validation_run_id": validation.get("id"),
        "merge_sha": merge_sha,
        "deployed": False,
    }

    if args.auto_deploy:
        deployment = dispatch_deploy(args.repository, merge_sha)
        proof.update({"deployed": True, "deployment_run_id": deployment.get("id")})

    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(1)
