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
