#!/usr/bin/env python3
"""Trusted, non-executable bootstrap policy for autonomous ARB promotion.

Executable source/tests/dependencies require ordinary review until worker,
validation and production credential isolation have been qualified. A textual
source denylist cannot establish that boundary.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import Iterable

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRANCH_RE = re.compile(r"^agent/inqsi-arb-aec-[0-9]+$")
MAX_FILES = 2
MAX_CHANGED_LINES = 600
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 128 * 1024
_ALLOWED_EXACT = {"inqsi-arb/ARB_BACKLOG.md", "inqsi-arb/ARB_STATUS.md"}


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
    # NUL delimiting preserves unusual filenames; --no-renames exposes deletions
    # at the old path as well as additions at the new path.
    raw = subprocess.check_output(
        ["git", "diff", "--no-renames", "--name-only", "-z", base_sha, candidate_sha], cwd=repo)
    return [p.decode("utf-8") for p in raw.split(b"\0") if p]


def validate_paths(paths: Iterable[str]) -> list[str]:
    items = list(paths)
    if not items:
        raise ValueError("NO_CHANGED_FILES")
    if len(items) > MAX_FILES:
        raise ValueError("TOO_MANY_CHANGED_FILES")
    for path in items:
        if path not in _ALLOWED_EXACT:
            raise ValueError(f"PATH_REQUIRES_REVIEW:{path}")
    return items


def validate_blob_limits(repo: Path, candidate_sha: str, paths: list[str]) -> None:
    total = 0
    for path in paths:
        entry = _git("ls-tree", candidate_sha, "--", path, cwd=repo)
        if not entry:
            raise ValueError("DOCUMENT_DELETION_REQUIRES_REVIEW")
        metadata, actual_path = entry.split("\t", 1)
        mode, kind, sha = metadata.split()
        if mode != "100644" or kind != "blob" or actual_path != path:
            raise ValueError("NON_REGULAR_DOCUMENT")
        size = int(_git("cat-file", "-s", sha, cwd=repo))
        if size > MAX_FILE_BYTES:
            raise ValueError("FILE_TOO_LARGE")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("CANDIDATE_BYTES_TOO_LARGE")
        blob = subprocess.check_output(["git", "cat-file", "blob", sha], cwd=repo)
        if b"\0" in blob:
            raise ValueError("BINARY_CHANGE_NOT_ELIGIBLE")
        try:
            blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("NON_UTF8_DOCUMENT") from exc


def validate_candidate(repo: Path, branch: str, candidate_sha: str, base_sha: str) -> list[str]:
    validate_identity(branch, candidate_sha, base_sha)
    parents = _git("rev-list", "--parents", "-n", "1", candidate_sha, cwd=repo).split()[1:]
    if parents != [base_sha]:
        raise ValueError("CANDIDATE_PARENT_MISMATCH")
    paths = validate_paths(changed_files(repo, base_sha, candidate_sha))
    # Bound blob sizes before reading content or producing a diff.
    validate_blob_limits(repo, candidate_sha, paths)
    stats = _git("diff", "--no-renames", "--numstat", base_sha, candidate_sha, cwd=repo)
    total = 0
    for line in stats.splitlines():
        added, deleted, _ = line.split("\t", 2)
        if added == "-" or deleted == "-":
            raise ValueError("BINARY_CHANGE_NOT_ELIGIBLE")
        total += int(added) + int(deleted)
    if total > MAX_CHANGED_LINES:
        raise ValueError("CANDIDATE_TOO_LARGE")
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
    print(f"ARB non-executable bootstrap policy passed: {len(paths)} changed files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
