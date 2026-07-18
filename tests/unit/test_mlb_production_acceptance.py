from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from enforce_mlb_production_acceptance import build_acceptance


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def pull_guard(**updates):
    base = {
        "guardPassed": True,
        "officialScheduleVerified": True,
        "pullsRequired": True,
        "fresh": True,
        "missingCleanScheduledSlots": [],
        "duplicateOrExtraPullsSinceStart": 0,
        "preStartPollutedPullCount": 0,
        "slateDateEt": "2026-07-18",
    }
    base.update(updates)
    return base


def verifier(**updates):
    base = {
        "ok": True,
        "blockers": [],
        "gameCount": 16,
        "predictionCount": 16,
        "allGamesPredicted": True,
        "lock": {"locked": False, "lockDue": False},
    }
    base.update(updates)
    return base


def audit(summary=None, optimization=None, **updates):
    base = {
        "ok": True,
        "createdAtUtc": NOW.isoformat(),
        "summary": summary or {
            "targetAccuracyPct": 90.0,
            "completedFinalGames": 0,
            "gradedPredictionCount": 0,
            "missingPredictionCount": 0,
            "officialPredictionCount": 0,
            "rolling24hOfficialAccuracyPct": None,
            "rolling24hAllGamesAccuracyPct": None,
        },
        "mlOptimizationV3": optimization or {
            "cleanRowCount": 2,
            "quarantinedRowCount": 226,
            "mode": "SHADOW_CHALLENGER",
            "automaticPromotionEnabled": True,
        },
    }
    base.update(updates)
    return base


def test_pregame_clean_slate_is_infrastructure_accepted_but_accuracy_unmeasurable():
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=audit(),
        now_utc=NOW,
    )
    assert result["ok"] is True
    assert result["infrastructureOk"] is True
    assert result["accuracyStatus"] == "UNMEASURABLE_NO_GRADED_PREDICTIONS"
    assert result["accuracyTargetMet"] is None


def test_missing_prediction_is_an_infrastructure_failure():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 14,
        "gradedPredictionCount": 1,
        "missingPredictionCount": 13,
        "officialPredictionCount": 1,
        "rolling24hOfficialAccuracyPct": 0.0,
        "rolling24hAllGamesAccuracyPct": 0.0,
    }
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=audit(summary=summary),
        now_utc=NOW,
    )
    assert result["ok"] is False
    assert "COMPLETED_GAMES_WITHOUT_IMMUTABLE_GRADEABLE_PREDICTIONS" in result["infrastructureBlockers"]


def test_below_ninety_accuracy_is_a_model_failure_after_clean_coverage():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 10,
        "gradedPredictionCount": 10,
        "missingPredictionCount": 0,
        "officialPredictionCount": 10,
        "rolling24hOfficialAccuracyPct": 80.0,
        "rolling24hAllGamesAccuracyPct": 80.0,
    }
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=audit(summary=summary),
        now_utc=NOW,
    )
    assert result["infrastructureOk"] is True
    assert result["ok"] is False
    assert result["accuracyStatus"] == "BELOW_TARGET"
    assert "ROLLING_24H_OFFICIAL_ACCURACY_BELOW_90_PCT" in result["modelBlockers"]


def test_ninety_percent_clean_window_passes():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 10,
        "gradedPredictionCount": 10,
        "missingPredictionCount": 0,
        "officialPredictionCount": 10,
        "rolling24hOfficialAccuracyPct": 90.0,
        "rolling24hAllGamesAccuracyPct": 90.0,
    }
    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=audit(summary=summary),
        now_utc=NOW,
    )
    assert result["ok"] is True
    assert result["accuracyStatus"] == "TARGET_MET"
    assert result["accuracyTargetMet"] is True
