#!/usr/bin/env python3
"""Trusted policy checks for autonomously promoted Inqsi ARB candidates.

This module intentionally lives outside the Codex worker write allowlist. It is
used by the supervisor validation workflow before an Actions-created ARB PR may
be merged automatically.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Iterable

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRANCH_RE = re.compile(r"^agent/inqsi-arb-aec-[0-9]+$")
MAX_FILES = 12
MAX_CHANGED_LINES = 600

_ALLOWED_EXACT = {
    "inqsi-arb/ARB_BACKLOG.md",
    "inqsi-arb/ARB_STATUS.md",
}
_ALLOWED_PREFIXES = (
    "inqsi-arb/src/",
    "inqsi-arb/tests/",
)

# These are writable by the coding worker for draft proposals but are never
# eligible for unattended promotion because they define authority, deployment,
# infrastructure or the supervisor itself.
_NEVER_AUTO_PREFIXES = (
    "inqsi-arb/ops/",
    ".github/",
    "arb_supervisor/",
)
_NEVER_AUTO_EXACT = {
    "inqsi-arb/template.yaml",
    "inqsi-arb/sportsbook-template.yaml",
    "inqsi-arb/samconfig.toml",
}

_DANGEROUS_ADDED_SOURCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bplace[_ -]?bet\b",
        r"\bsubmit[_ -]?wager\b",
        r"\bexecute[_ -]?wager\b",
        r"\bsportsbook[_ -]?(?:login|credential)\b",
        r"\bsam\s+deploy\b",
        r"\baws\s+cloudformation\b",
        r"\bos\.system\s*\(",
        r"\bsubprocess\.(?:run|Popen|call|check_call|check_output)\s*\(",
    )
)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def validate_identity(branch: str, candidate_sha: str, base_sha: str) -> None:
    if not BRANCH_RE.fullmatch(branch):
        raise ValueError("INVALID_AEC_BRANCH")
    if not SHA_RE.fullmatch(candidate_sha) or not SHA_RE.fullmatch(base_sha):
        raise ValueError("INVALID_COMMIT_SHA")
    if candidate_sha == base_sha:
        raise ValueError("EMPTY_CANDIDATE")


def changed_files(repo: Path, base_sha: str, candidate_sha: str) -> list[str]:
    output = _git("diff", "--name-only", base_sha, candidate_sha, cwd=repo)
    return [line for line in output.splitlines() if line]


def _changed_line_count(repo: Path, base_sha: str, candidate_sha: str) -> int:
    output = _git("diff", "--numstat", base_sha, candidate_sha, cwd=repo)
    total = 0
    for line in output.splitlines():
        if not line:
            continue
        added, deleted, _ = line.split("\t", 2)
        # Binary changes are not eligible for autonomous promotion.
        if added == "-" or deleted == "-":
            raise ValueError("BINARY_CHANGE_NOT_ELIGIBLE")
        total += int(added) + int(deleted)
    return total


def validate_paths(paths: Iterable[str]) -> list[str]:
    items = list(paths)
    if not items:
        raise ValueError("NO_CHANGED_FILES")
    if len(items) > MAX_FILES:
        raise ValueError("TOO_MANY_CHANGED_FILES")
    for path in items:
        if path in _NEVER_AUTO_EXACT or path.startswith(_NEVER_AUTO_PREFIXES):
            raise ValueError(f"HIGH_RISK_PATH:{path}")
        if path in _ALLOWED_EXACT or path.startswith(_ALLOWED_PREFIXES):
            continue
        raise ValueError(f"PATH_NOT_AUTO_PROMOTABLE:{path}")
    return items


def validate_added_source(repo: Path, base_sha: str, candidate_sha: str) -> None:
    """Reject a small set of authority-expanding primitives in added source lines.

    This is an additional defense, not a general-purpose security scanner.
    """
    diff = _git("diff", "--unified=0", base_sha, candidate_sha, "--", "inqsi-arb/src", cwd=repo)
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        code = line[1:]
        for pattern in _DANGEROUS_ADDED_SOURCE_PATTERNS:
            if pattern.search(code):
                raise ValueError(f"DANGEROUS_ADDED_SOURCE:{pattern.pattern}")


def validate_candidate(repo: Path, branch: str, candidate_sha: str, base_sha: str) -> list[str]:
    validate_identity(branch, candidate_sha, base_sha)
    # The candidate must be a direct descendant of the controller-selected base.
    parent = _git("rev-parse", f"{candidate_sha}^", cwd=repo)
    if parent != base_sha:
        raise ValueError("CANDIDATE_PARENT_MISMATCH")
    paths = validate_paths(changed_files(repo, base_sha, candidate_sha))
    if _changed_line_count(repo, base_sha, candidate_sha) > MAX_CHANGED_LINES:
        raise ValueError("CANDIDATE_TOO_LARGE")
    validate_added_source(repo, base_sha, candidate_sha)
    subprocess.run(["git", "diff", "--check", base_sha, candidate_sha], cwd=repo, check=True)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--branch", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--base-sha", required=True)
    args = parser.parse_args()
    paths = validate_candidate(args.repo.resolve(), args.branch, args.candidate_sha, args.base_sha)
    print(f"ARB supervisor policy passed: {len(paths)} changed files")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
