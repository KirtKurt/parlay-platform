from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v3 as subject


def mutation(*, manifest=12, canonical=8, terminal=4, missed=0, due=0):
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": "2026-08-03",
        "perGameLockProgress": {
            "manifestGameCount": manifest,
            "canonicalCount": canonical,
            "noPredictionDataCount": terminal,
            "lockOutcomeCount": canonical + terminal,
            "missedCount": missed,
            "dueMissingCount": due,
        },
    }


def official(*, games=15, canonical=10, terminal=5):
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": "2026-08-03",
        "gameCount": games,
        "officialScheduleBacked": True,
        "officialScheduleAuthorityVersion": base.OFFICIAL_SCHEDULE_AUTHORITY_VERSION,
        "officialScheduleAuthoritativeStartTimes": True,
        "officialScheduleGameCount": games,
        "lockedPredictionCount": canonical,
        "noPredictionDataCount": terminal,
        "lockedStatusCount": canonical + terminal,
        "lockStatusComplete": True,
    }


def test_official_full_slate_status_overrides_provider_scoped_mutation_count():
    result = subject.validate_lock_result(
        mutation(manifest=12, canonical=8, terminal=4),
        official(games=15, canonical=10, terminal=5),
        "2026-08-03",
    )

    assert result["manifestGameCount"] == 15
    assert result["canonicalPredictionCount"] == 10
    assert result["terminalNoPredictionCount"] == 5
    assert result["lockOutcomeCount"] == 15
    assert result["terminalCoverageAuthority"] == "official_exact_date_read_status"
    assert result["mutationDiagnosticsProviderScoped"] is True
    assert result["mutationManifestGameCount"] == 12
    assert result["mutationCanonicalPredictionCount"] == 8
    assert result["mutationTerminalNoPredictionCount"] == 4


def test_mutation_missed_or_due_state_still_fails_closed():
    with pytest.raises(
        base.ReconciliationError,
        match="prospective_slate_still_unresolved",
    ):
        subject.validate_lock_result(
            mutation(missed=1),
            official(),
            "2026-08-03",
        )
    with pytest.raises(
        base.ReconciliationError,
        match="prospective_slate_still_unresolved",
    ):
        subject.validate_lock_result(
            mutation(due=1),
            official(),
            "2026-08-03",
        )


def test_official_terminal_coverage_remains_mandatory():
    status = official()
    status["lockedStatusCount"] = 14
    with pytest.raises(
        base.ReconciliationError,
        match="official_status_terminal_counts_inconsistent",
    ):
        subject.validate_lock_result(mutation(), status, "2026-08-03")


def test_wrapper_changes_no_storage_prediction_or_authority_path():
    source = (
        ROOT / "scripts" / "reconcile_mlb_prospective_backlog_v3.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "put_item(",
        "update_item(",
        "delete_item(",
        "predictedWinner",
        "predicted_winner",
        "INQSI_MLB_ML_AUTO_PROMOTE",
        "productionAuthorityChanged = True",
        "liveInferenceAuthority = True",
    ):
        assert forbidden not in source
    assert "official_exact_date_read_status" in source
    assert "prospective_slate_still_unresolved" in source
