from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_r4_production_acceptance_fixtures_use_current_cutover_contract() -> None:
    source = (ROOT / "tests" / "unit" / "test_mlb_production_acceptance.py").read_text(
        encoding="utf-8"
    )

    # The acceptance fixtures derive the active cutoff from the production
    # experiment contract; the historical test-function label is not authority.
    assert "experiment.PRODUCTION_RELEASE_CUTOFF_UTC" in source
    assert "preCutoffQuarantinedFinalGameCount" in source
    assert "postCutoffDefects" in source
    assert '"2026-07-24"' in source
