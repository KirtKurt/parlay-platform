#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAMBDA_TASK_ROOT = Path(
    os.environ.get("INQSI_MLB_LAMBDA_TASK_ROOT") or ROOT / "hello_world"
).resolve()


def main() -> int:
    env = dict(os.environ)
    # Lambda adds LAMBDA_TASK_ROOT after Python startup. Keeping hello_world on
    # PYTHONPATH would auto-import its sitecustomize and mask missing explicit
    # runtime installation, which caused the production failure after 8c12be1.
    inherited_pythonpath = []
    for value in (env.get("PYTHONPATH") or "").split(os.pathsep):
        if not value:
            continue
        try:
            if Path(value).resolve() == LAMBDA_TASK_ROOT:
                continue
        except OSError:
            pass
        inherited_pythonpath.append(value)
    env.update(
        {
            "AWS_DEFAULT_REGION": env.get("AWS_DEFAULT_REGION") or "us-east-1",
            "AWS_REGION": env.get("AWS_REGION") or "us-east-1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "SNAPSHOTS_TABLE": "",
            "INQSI_MLB_ALLOW_LOCAL_FILE_CHAMPION": "false",
            "INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED": "false",
            "INQSI_MLB_ML_AUTO_PROMOTE": "false",
            "INQSI_MLB_LAMBDA_TASK_ROOT": str(LAMBDA_TASK_ROOT),
            "PYTHONPATH": os.pathsep.join(inherited_pythonpath),
        }
    )
    code = r'''
import json
import os
import sys
from pathlib import Path

task_root = Path(os.environ["INQSI_MLB_LAMBDA_TASK_ROOT"]).resolve()
assert task_root.is_dir(), task_root
assert all(
    not entry or Path(entry).resolve() != task_root
    for entry in sys.path
), {"taskRootLoadedBeforeStartupCompleted": str(task_root), "sysPath": sys.path}
assert "mlb_game_winner_engine" not in sys.modules
assert "mlb_last_possible_prediction_gate" not in sys.modules
sys.path.insert(0, str(task_root))

# Preserve the main ApiFunction pull-history import contract while validating
# the dedicated, read-only MLB function with Lambda-realistic startup order.
import frontend_app
import inqsi_pull_history
import mlb_v3_read_api

assert callable(frontend_app.lambda_handler)
assert callable(inqsi_pull_history.handle_pull_history_route)
event = {
    "path": "/v1/mlb/model/version",
    "rawPath": "/v1/mlb/model/version",
    "httpMethod": "GET",
    "queryStringParameters": None,
}
response = mlb_v3_read_api.lambda_handler(event, None)
assert response.get("statusCode") == 200, response
body = json.loads(response.get("body") or "{}")
assert body.get("ok") is True, body
assert body.get("engine_import_ok") is True, body
assert body.get("model_version") == "INQSI-MLB-v4.0-canonical-probability-aws-v2-shadow-manual-first", body
assert body.get("productionAuthoritySource") == "persisted_canonical_rules_market_prediction_v2_shadow_only", body
assert body.get("automaticPromotionPolicy") == "disabled_manual_review_creates_shadow_pointer_only", body
assert body.get("legacyV1AuthorityEnabled") is False, body
assert body.get("awsNativeTrainingInstalled") is True, body
assert body.get("awsNativeTrainingAuthority") is False, body
assert body.get("awsNativeTrainingHealthSource") == "separate_mode_specific_status_contract", body
assert body.get("firstPromotionRequiresManualReview") is True, body
assert body.get("manualReviewCreatesShadowApprovalOnly") is True, body
assert body.get("v2InferenceConsumerInstalled") is False, body
assert body.get("runtimeAuthorityActivationAvailable") is False, body
assert body.get("requiredWinnerPickPolicy") == "one_official_locked_winner_prediction_for_every_mlb_game", body
assert body.get("playablePolicy") == "playability_is_separate_and_may_be_false_for_an_official_prediction", body
assert body.get("apiRuntimeVersion") == "MLB-V3-READ-API-v4-persisted-canonical-v2-shadow", body
assert body.get("readOnly") is True, body
assert str(body.get("ml_optimization_version") or "").startswith("MLB-ML-OPTIMIZATION-v3"), body
runtime = body.get("ml_runtime_install") or {}
assert runtime.get("ok") is True, runtime
assert runtime.get("version") == (
    "MLB-ML-RUNTIME-INSTALL-v4.2-signal-policy-prelock-persistence-"
    "verified-stage-promotion-authority-aws-v2-shadow-manual-first"
), runtime
assert runtime.get("steps", {}).get("sourceHonestFundamentalsV2") is True, runtime
assert runtime.get("steps", {}).get(
    "canonicalProbabilityAndPersistedPrelockAuthority"
) is True, runtime
assert runtime.get("steps", {}).get(
    "providerNeutralCalibrationAndActionability"
) is True, runtime
assert runtime.get("steps", {}).get("signalPolicyV13Installed") is True, runtime
assert runtime.get("signalPolicyV13Version") == "MLB-SIGNAL-POLICY-v1.7-reversal-instability-gate", runtime
policy = runtime.get("accuracyTargetPolicy") or {}
assert policy.get("rolling24hAllGamesAuditTargetPct") == 90.0, policy
assert policy.get("minimumRolling24hSlateAccuracyPct") == 90.0, policy
assert policy.get("minimumOutcomeUntouchedAccuracyPct") == 90.0, policy
assert policy.get("recommendationReliabilityThresholdPct") == 90.0, policy
assert policy.get("selectedUntouchedTestPlayabilityAccuracyTargetPct") == 90.0, policy
assert policy.get("minimumExactOddsCoveragePct") == 90.0, policy
assert policy.get("rolling24hSlateAccuracyProgressMilestonesPct") == [50.0, 60.0, 70.0, 80.0], policy
assert policy.get("rolling24hSlateAccuracyProgressMilestonesReportingOnly") is True, policy
assert policy.get("rolling24hAccuracyAffectsPromotion") is False, policy
assert policy.get("automaticPromotionAfterApplicableGates") is False, policy
assert policy.get("firstPromotionRequiresManualReview") is True, policy
assert policy.get("legacyV1AuthorityEnabled") is False, policy
assert policy.get("roiPromotionGateRequired") is False, policy
assert policy.get("everyGameRetainsOfficialPick") is True, policy
assert policy.get("everyGameRetainsVisibleLockedPrediction") is True, policy
assert policy.get("playabilitySeparateFromOfficialPick") is True, policy
assert policy.get("individualGameOfficialPickProbabilityFloorPct") == 60.0, policy
assert policy.get("multipleReversalsRequireIndependentConfirmationForOfficialStatus") is True, policy
required = {
    "accuracyTargetsSeparated",
    "legacyReliabilityOverlaySafety",
    "legacyV1ChampionRuntimeInstalledForShadowDiagnostics",
    "legacyV1AuthorityDisabled",
    "v2ShadowManualFirst",
    "officialSemanticsFinalized",
    "immutableFeatureFreeze",
    "immutableLockedStorageAuthority",
    "exactCleanCohortVectorPatch",
    "officialFreezeBridge",
    "canonicalLockedStorageFinalizer",
    "lastPrelockPromotionAuthority",
    "canonicalProbabilityAndPersistedPrelockAuthority",
    "providerNeutralCalibrationAndActionability",
    "signalPolicyV13Installed",
    "legacyFinalGateDisabled",
}
missing = sorted(name for name in required if (runtime.get("steps") or {}).get(name) is not True)
assert not missing, {"missingRuntimeSteps": missing, "runtime": runtime}

read_calls = []
original_reader = mlb_v3_read_api.ENGINE.read_persisted_predictions
try:
    def capture_persisted_reader(date, *, store, limit):
        read_calls.append({"date": date, "store": store, "limit": limit})
        return {"ok": True, "predictions": [], "count": 0}
    mlb_v3_read_api.ENGINE.read_persisted_predictions = capture_persisted_reader
    read_event = {
        "path": "/v1/mlb/predictions",
        "rawPath": "/v1/mlb/predictions",
        "httpMethod": "GET",
        "queryStringParameters": {"date": "2026-07-16", "store": "true", "limit": "7"},
    }
    read_response = mlb_v3_read_api.lambda_handler(read_event, None)
finally:
    mlb_v3_read_api.ENGINE.read_persisted_predictions = original_reader
assert read_response.get("statusCode") == 200, read_response
read_body = json.loads(read_response.get("body") or "{}")
assert read_calls == [{"date": "2026-07-16", "store": False, "limit": 7}], read_calls
assert read_body.get("readOnly") is True, read_body

original_runtime_status = mlb_v3_read_api.ENGINE.MLB_ML_RUNTIME_INSTALL_V3
original_reader = mlb_v3_read_api.ENGINE.read_persisted_predictions
fail_closed_calls = []
try:
    mlb_v3_read_api.ENGINE.MLB_ML_RUNTIME_INSTALL_V3 = {
        **original_runtime_status,
        "ok": False,
    }
    mlb_v3_read_api.ENGINE.read_persisted_predictions = (
        lambda *args, **kwargs: fail_closed_calls.append((args, kwargs))
    )
    failed_response = mlb_v3_read_api.lambda_handler(read_event, None)
finally:
    mlb_v3_read_api.ENGINE.MLB_ML_RUNTIME_INSTALL_V3 = original_runtime_status
    mlb_v3_read_api.ENGINE.read_persisted_predictions = original_reader
assert failed_response.get("statusCode") == 503, failed_response
failed_body = json.loads(failed_response.get("body") or "{}")
assert fail_closed_calls == [], fail_closed_calls
assert failed_body.get("predictions") == [], failed_body
assert failed_body.get("winner_predictions") == [], failed_body
print(json.dumps({
    "ok": True,
    "modelVersion": body.get("model_version"),
    "runtimeVersion": runtime.get("version"),
    "runtimeSteps": runtime.get("steps"),
}, indent=2))
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    print(result.stdout.strip())
    print("MLB Lambda cold import, read-only, fail-closed, pull-history, and versioned prelock signal-policy contracts verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
