from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_manifest_lists_only_existing_reviewed_files():
    manifest = json.loads(
        (ROOT / "runtime_reports/mlb_v15_11_1_release_manifest.json").read_text()
    )
    assert manifest["version"] == "MLB-V15.11.1-HISTORICAL-DAILY-OPTIMIZER-HARDENED"
    assert manifest["releaseState"] == "BUILT_NOT_PROMOTED"
    assert manifest["automaticWagerAllowed"] is False
    assert manifest["untouchedAuditReplayAllowed"] is False
    assert manifest["legacyAuthorityRemovalTiming"] == (
        "ONLY_AFTER_REAL_GATE_AND_EXPLICIT_WRITE_ONCE_CUTOVER"
    )
    authoritative = manifest["authoritativeFiles"]
    assert authoritative == sorted(set(authoritative))
    missing = [path for path in authoritative if not (ROOT / path).is_file()]
    assert missing == []


def test_release_status_does_not_claim_real_training_or_deployment():
    status = json.loads(
        (ROOT / "runtime_reports/mlb_v15_11_1_release_status.json").read_text()
    )
    assert status["releaseState"] == "BUILT_NOT_PROMOTED"
    assert status["realHistoricalEvidencePassed"] is False
    assert status["productionCutoverExecuted"] is False
    assert status["currentProductionAuthorityChanged"] is False
    assert status["automaticWagerAllowed"] is False
    assert status["minimumTrainingGames"] == 1000
    assert status["minimumValidationGames"] == 200
    assert status["minimumAuditGames"] == 200
    assert status["dailyAccuracyHardMinimum"] == 0.8
    assert status["dailyAccuracyStretchTarget"] == 0.9


def test_paid_and_production_workflow_is_manual_only():
    workflow = (
        ROOT / ".github/workflows/mlb-historical-optimizer-v15-11.yml"
    ).read_text()
    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "I AUTHORIZE PAID THE ODDS API HISTORICAL USAGE" in workflow
    assert "PROMOTE MLB V15.11.1 HISTORICAL DAILY OPTIMIZER ONLY" in workflow
    assert "audit_claim_s3_uri" in workflow
    assert "execute_cutover" in workflow


def test_command_module_is_source_not_transport_placeholder():
    core = (
        ROOT / "scripts/mlb_historical_daily_optimizer_v15_11.py"
    ).read_text()
    hardened = (
        ROOT / "scripts/mlb_historical_daily_optimizer_v15_11_hardened.py"
    ).read_text()
    assert core.startswith("#!/usr/bin/env python3")
    assert hardened.startswith("#!/usr/bin/env python3")
    assert "TEST_PLACEHOLDER" not in core
    assert "TEST_PLACEHOLDER" not in hardened
    assert "execute_backfill" in core
    assert "claim_audit_once_s3" in hardened
