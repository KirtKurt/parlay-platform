from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "hello_world"))

from enforce_mlb_production_acceptance import build_acceptance
import mlb_ml_aws_training_v1 as trainer
import mlb_ml_experiment_v2 as experiment


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
    deployment = {"gitSha": "a" * 40, "templateSha256": "b" * 64}
    manifest_digest = "c" * 64

    def mode_health(mode, maximum_age_minutes):
        latest = {
            "ok": True,
            "status": (
                "COLLECTING_TRAIN"
                if mode == "training"
                else "WAITING_FOR_PERSISTED_CHALLENGER"
            ),
            "executionMode": mode,
            "version": trainer.VERSION,
            "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
            "manifestDigest": manifest_digest,
            "createdAtUtc": NOW.isoformat(),
            "deploymentIdentity": deployment,
            "statusFingerprintVersion": trainer.STATUS_FINGERPRINT_VERSION,
            "executionConcurrencyControl": trainer.execution_concurrency_control(
                acquired_for_run=True
            ),
        }
        latest["statusFingerprint"] = trainer._status_fingerprint(latest)
        return {
            "ok": True,
            "executionMode": mode,
            "statusPresent": True,
            "latestRunStatus": (
                "COLLECTING_TRAIN"
                if mode == "training"
                else "WAITING_FOR_PERSISTED_CHALLENGER"
            ),
            "latestRunCreatedAtUtc": NOW.isoformat(),
            "latestRunTimestampValid": True,
            "latestRunAgeMinutes": 0.0,
            "latestRunFresh": True,
            "latestRunMaxAgeMinutes": maximum_age_minutes,
            "deploymentIdentity": deployment,
            "deploymentIdentityMatches": True,
            "latestRun": latest,
            "errors": [],
        }

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
        "mlTrainingV2": {
            "ok": True,
            "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
            "manifestDigest": manifest_digest,
            "manifestReadStable": True,
            "manifestReadBefore": {
                "revision": 0,
                "manifestDigest": manifest_digest,
            },
            "manifestReadAfter": {
                "revision": 0,
                "manifestDigest": manifest_digest,
            },
            "deployedTrainerIdentity": deployment,
            "statusPresent": True,
            "latestRunStatus": "COLLECTING_TRAIN",
            "latestRunCreatedAtUtc": NOW.isoformat(),
            "latestRunTimestampValid": True,
            "latestRunAgeMinutes": 0.0,
            "latestRunFresh": True,
            "latestRunMaxAgeMinutes": 480.0,
            "trainingHealth": mode_health("training", 480.0),
            "selectionCaptureHealth": mode_health("selection_capture", 45.0),
            "deploymentIdentityAgreement": True,
            "manifestPresent": True,
            "manifestValid": True,
            "manifestPhase": "COLLECTING_TRAIN",
            "automaticPromotionEnabled": False,
            "partitionCounts": {
                "train": 0,
                "validation": 0,
                "prospectiveTest": 0,
            },
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


def test_below_ninety_accuracy_is_dashboard_only_after_clean_coverage():
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
    assert result["ok"] is True
    assert result["accuracyStatus"] == "BELOW_TARGET"
    assert result["modelBlockers"] == []
    assert "ASPIRATIONAL_90_PCT_DASHBOARD_TARGET_NOT_MET" in result["warnings"]


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


def test_graded_rows_without_authoritative_official_accuracy_fail_closed():
    summary = {
        "targetAccuracyPct": 90.0,
        "completedFinalGames": 10,
        "gradedPredictionCount": 10,
        "missingPredictionCount": 0,
        "officialPredictionCount": 10,
        "rolling24hOfficialAccuracyPct": None,
        "rolling24hAllGamesAccuracyPct": 80.0,
    }

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=audit(summary=summary),
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert result["infrastructureOk"] is False
    assert result["accuracyStatus"] == "UNMEASURABLE_OFFICIAL_ACCURACY_MISSING"
    assert (
        "AUTHORITATIVE_OFFICIAL_ACCURACY_MISSING_FOR_GRADED_ROWS"
        in result["infrastructureBlockers"]
    )


def test_missing_v2_trainer_status_is_an_infrastructure_failure():
    payload = audit()
    payload.pop("mlTrainingV2")

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert "AWS_V2_TRAINER_STATUS_MISSING_OR_INVALID" in result["infrastructureBlockers"]
    assert "AWS_V2_EXPERIMENT_MANIFEST_NOT_INITIALIZED" in result["infrastructureBlockers"]


def test_uninitialized_v2_manifest_is_an_infrastructure_failure():
    payload = audit()
    payload["mlTrainingV2"].update(
        {"manifestPresent": False, "manifestValid": None}
    )

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert "AWS_V2_EXPERIMENT_MANIFEST_NOT_INITIALIZED" in result["infrastructureBlockers"]
    assert "AWS_V2_EXPERIMENT_MANIFEST_NOT_CREATED_YET" not in result["warnings"]


def test_missing_latest_v2_status_is_an_infrastructure_failure():
    payload = audit()
    payload["mlTrainingV2"]["ok"] = False
    payload["mlTrainingV2"]["automaticPromotionEnabled"] = None
    payload["mlTrainingV2"]["trainingHealth"] = {}

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is False
    assert (
        "AWS_V2_TRAINING_STATUS_MISSING_STALE_OR_INVALID"
        in result["infrastructureBlockers"]
    )
    assert "AWS_V2_AUTOMATIC_PROMOTION_STATE_MISSING" in result["infrastructureBlockers"]


def test_seven_hour_training_status_is_healthy_for_six_hour_schedule():
    payload = audit()
    latest = payload["mlTrainingV2"]["trainingHealth"]["latestRun"]
    latest["createdAtUtc"] = (
        NOW - timedelta(hours=7)
    ).isoformat()
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(),
        verifier=verifier(),
        audit=payload,
        now_utc=NOW,
    )

    assert result["ok"] is True
    assert result["mlDisposition"]["v2Training"]["latestRunAgeMinutes"] == 420.0
    assert result["mlDisposition"]["v2Training"]["latestRunFresh"] is True


def test_training_status_older_than_eight_hours_is_an_infrastructure_failure():
    payload = audit()
    payload["mlTrainingV2"]["ok"] = False
    latest = payload["mlTrainingV2"]["trainingHealth"]["latestRun"]
    latest["createdAtUtc"] = (
        NOW - timedelta(minutes=481)
    ).isoformat()
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert (
        "AWS_V2_TRAINING_STATUS_MISSING_STALE_OR_INVALID"
        in result["infrastructureBlockers"]
    )
    assert result["mlDisposition"]["v2Training"]["trainingHealth"][
        "latestRunFresh"
    ] is False


def test_selection_capture_status_older_than_45_minutes_fails_independently():
    payload = audit()
    payload["mlTrainingV2"]["ok"] = False
    latest = payload["mlTrainingV2"]["selectionCaptureHealth"]["latestRun"]
    latest["createdAtUtc"] = (NOW - timedelta(minutes=46)).isoformat()
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert (
        "AWS_V2_SELECTION_CAPTURE_STATUS_MISSING_STALE_OR_INVALID"
        in result["infrastructureBlockers"]
    )
    assert (
        "AWS_V2_TRAINING_STATUS_MISSING_STALE_OR_INVALID"
        not in result["infrastructureBlockers"]
    )


def test_mode_status_deployment_identity_mismatch_fails_closed():
    payload = audit()
    payload["mlTrainingV2"]["ok"] = False
    payload["mlTrainingV2"]["deploymentIdentityAgreement"] = False
    latest = payload["mlTrainingV2"]["selectionCaptureHealth"]["latestRun"]
    latest["deploymentIdentity"] = {
        "gitSha": "d" * 40,
        "templateSha256": "e" * 64,
    }
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert "AWS_V2_MODE_DEPLOYMENT_IDENTITY_MISMATCH" in result[
        "infrastructureBlockers"
    ]


def test_acceptance_recomputes_status_fingerprint_and_exact_version():
    payload = audit()
    latest = payload["mlTrainingV2"]["trainingHealth"]["latestRun"]
    latest["version"] = "stale-trainer-version"

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert "status_version_mismatch" in result["mlDisposition"]["v2Training"][
        "trainingHealth"
    ]["contractErrors"]
    assert "status_fingerprint_mismatch" in result["mlDisposition"]["v2Training"][
        "trainingHealth"
    ]["contractErrors"]


def test_acceptance_rejects_status_without_acquired_shared_lease():
    payload = audit()
    latest = payload["mlTrainingV2"]["trainingHealth"]["latestRun"]
    latest["executionConcurrencyControl"] = trainer.execution_concurrency_control(
        acquired_for_run=False
    )
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    result = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert result["ok"] is False
    assert "status_execution_lease_contract_mismatch" in result[
        "mlDisposition"
    ]["v2Training"]["trainingHealth"]["contractErrors"]


def test_manifest_advance_invalidates_selection_heartbeat_until_recaptured():
    payload = audit()
    latest = payload["mlTrainingV2"]["selectionCaptureHealth"]["latestRun"]
    latest["manifestDigest"] = "d" * 64
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)

    failed = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )
    assert failed["ok"] is False
    assert "status_manifest_mismatch" in failed["mlDisposition"]["v2Training"][
        "selectionCaptureHealth"
    ]["contractErrors"]

    latest["manifestDigest"] = payload["mlTrainingV2"]["manifestDigest"]
    latest["statusFingerprint"] = trainer._status_fingerprint(latest)
    recaptured = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )
    assert recaptured["ok"] is True


def test_acceptance_independently_rejects_unstable_manifest_status_snapshot():
    payload = audit()
    v2 = payload["mlTrainingV2"]
    v2["manifestReadBefore"] = {
        "revision": 0,
        "manifestDigest": "d" * 64,
    }
    v2["manifestReadAfter"] = {
        "revision": 1,
        "manifestDigest": v2["manifestDigest"],
    }
    # Do not trust the audit's summary boolean without rechecking its evidence.
    v2["manifestReadStable"] = True

    failed = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )

    assert failed["ok"] is False
    assert "AWS_V2_MANIFEST_STATUS_SNAPSHOT_UNSTABLE" in failed[
        "infrastructureBlockers"
    ]
    v2_result = failed["mlDisposition"]["v2Training"]
    assert v2_result["manifestReadStable"] is False
    assert "manifest_changed_during_status_read" in v2_result[
        "trainingHealth"
    ]["contractErrors"]
    assert "manifest_changed_during_status_read" in v2_result[
        "selectionCaptureHealth"
    ]["contractErrors"]

    v2["manifestReadBefore"] = dict(v2["manifestReadAfter"])
    recaptured = build_acceptance(
        pull_guard=pull_guard(), verifier=verifier(), audit=payload, now_utc=NOW
    )
    assert recaptured["ok"] is True
