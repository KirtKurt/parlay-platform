from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


def _push_paths() -> list[str]:
    source = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^  push:\n(?P<body>.*?)(?=^  workflow_dispatch:)",
        source,
    )
    assert match is not None
    body = match.group("body")
    assert "paths-ignore:" not in body
    assert re.search(r"(?m)^    paths:$", body)
    return re.findall(r'(?m)^      - "([^"]+)"$', body)


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def test_push_scope_includes_every_real_shared_sam_deploy_source() -> None:
    patterns = _push_paths()

    for path in (
        "template.yaml",
        "samconfig.toml",
        "backend/src/app.py",
        "hello_world/mlb_daily_pick_lock.py",
        ".github/workflows/deploy.yml",
        ".deploy-trigger",
        "DEPLOY_TRIGGER_PULL_HISTORY.txt",
        "ops/deploy-triggers/manual.txt",
    ):
        assert _matches(path, patterns), path


def test_push_scope_excludes_reports_proofs_tests_and_unrelated_workflows() -> None:
    patterns = _push_paths()

    for path in (
        "runtime_reports/mlb_scoring_guard_status_latest.json",
        "runtime/tennis_autonomy_status.json",
        "docs/runtime/mlb-historical-live-status.json",
        ".github/workflows/mlb-30m-progress-pulse.yml",
        ".github/workflows/unified-mlb-learning-recovery-once.yml",
        "scripts/report_mlb_30m_progress.py",
        "tests/unit/test_mlb_r7_overnight_queue_control.py",
        "soccer_auto/app.py",
        "soccer-auto-template.yaml",
    ):
        assert not _matches(path, patterns), path


def test_push_scope_has_no_broad_control_plane_or_proof_patterns() -> None:
    patterns = _push_paths()

    assert ".github/workflows/**" not in patterns
    assert "scripts/**" not in patterns
    assert "tests/**" not in patterns
    assert "runtime/**" not in patterns
    assert "runtime_reports/**" not in patterns
    assert "docs/**" not in patterns


def test_run_blocks_stay_below_github_expression_limit_with_margin() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    run_blocks = [
        step["run"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if "run" in step
    ]

    # GitHub rejects a workflow before creating a job when one scalar exceeds
    # its 21,000-character expression limit.  Leave margin for its internal
    # expression serialization instead of testing right at the boundary.
    assert max(map(len, run_blocks)) < 20_500
