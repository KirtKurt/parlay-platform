#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_accuracy_target_policy_v1 as policy
import mlb_ml_champion_challenger_v1 as legacy_champion
import mlb_ml_promotion_policy_v2 as promotion_v2
import mlb_official_lock_quality_gate as official_gate


def main() -> int:
    installed = policy.install()
    assert installed.get("ok") is True, installed

    # Ninety percent remains visible as an aspiration/reliability dashboard,
    # but it cannot activate, suspend, or replace production authority.
    assert installed["rolling24hAllGamesAuditTargetPct"] == 90.0
    assert installed["rolling24hAccuracyAffectsPromotion"] is False
    assert installed["rolling24hSlateAccuracyProgressMilestonesReportingOnly"] is True
    assert installed["automaticPromotionAfterApplicableGates"] is False
    assert installed["firstPromotionRequiresManualReview"] is True
    assert installed["legacyV1AuthorityEnabled"] is False
    assert legacy_champion.AUTO_PROMOTE is False
    assert legacy_champion.AUTOMATIC_PROMOTION_SUPPORTED is False

    # Every game keeps its immutable winner; the 60% classifier is only an
    # audit/training-quality disposition and never suppresses display or lock.
    assert installed["everyGameRetainsOfficialPick"] is True
    assert installed["everyGameRetainsVisibleLockedPrediction"] is True
    assert installed["playabilitySeparateFromOfficialPick"] is True
    assert official_gate.MIN_OFFICIAL_PROBABILITY_PCT == 60.0

    # The new promotion contract is the production contract.
    assert promotion_v2.MIN_TOTAL_CLEAN_ROWS == 500
    assert promotion_v2.MIN_PROSPECTIVE_TEST_ROWS == 100
    assert promotion_v2.MIN_PROSPECTIVE_SELECTED_RECOMMENDATIONS == 100
    assert promotion_v2.MAX_CALIBRATION_ERROR == 0.08
    assert promotion_v2.MIN_ACCURACY_LIFT_PCT_POINTS == 1.0
    assert promotion_v2.ASPIRATIONAL_ACCURACY_PCT == 90.0

    assert os.environ.get("INQSI_MLB_ROLLING_24H_ALL_GAMES_TARGET_ACCURACY") == "90.0"
    workflow = (ROOT / ".github/workflows/mlb-rolling-24h-audit.yml").read_text(
        encoding="utf-8"
    )
    assert "INQSI_MLB_ML_AUTO_PROMOTE: 'false'" in workflow
    assert "INQSI_MLB_ML_AUDIT_STORE: 'false'" in workflow
    assert "Commit rolling audit proof and ML artifacts" not in workflow

    print(
        "MLB targets verified: every game retains a locked winner, 90% is dashboard-only, "
        "legacy V1 is inert, and AWS V2 uses prospective market-skill/manual-first promotion."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
