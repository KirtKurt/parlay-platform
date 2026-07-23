from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_r4_production_acceptance_fixtures_are_post_cutover() -> None:
    source = (ROOT / "tests" / "unit" / "test_mlb_production_acceptance.py").read_text(
        encoding="utf-8"
    )

    assert "NOW = datetime(2026, 7, 25, 3, 0, tzinfo=timezone.utc)" in source
    assert '"2026-07-24"' in source
    assert (
        "test_pre_r4_missing_locks_are_quarantined_from_post_cutoff_acceptance"
        in source
    )

    assert "NOW = datetime(2026, 7, 23, 3, 0, tzinfo=timezone.utc)" not in source
    assert (
        "test_pre_r3_missing_locks_are_quarantined_from_post_cutoff_acceptance"
        not in source
    )
