from __future__ import annotations

from pathlib import Path

from scripts import remove_mlb_bbd_active_runtime as migration


def test_no_bbd_migration_references_only_present_active_workflows():
    missing = [
        str(path)
        for path in migration.ACTIVE_WORKFLOW_PATHS
        if not (migration.ROOT / path).is_file()
    ]

    assert missing == []
    assert Path(
        ".github/workflows/deploy-mlb-ranked-v15-10.yml"
    ) not in migration.ACTIVE_WORKFLOW_PATHS
    assert Path(".github/workflows/deploy.yml") in migration.ACTIVE_WORKFLOW_PATHS


def test_retired_fundamentals_dispatcher_matches_committed_workflow():
    path = migration.RETIRED_FUNDAMENTALS_WORKFLOW
    source = (migration.ROOT / path).read_text(encoding="utf-8")
    generated = migration.retired_fundamentals_dispatcher()

    assert generated == source
    assert "\\\n            --repo" in generated
    assert migration.patch_workflow(path, source) == source
