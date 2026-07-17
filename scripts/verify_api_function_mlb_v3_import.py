#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"


def main() -> int:
    env = dict(os.environ)
    inherited_pythonpath = env.get("PYTHONPATH")
    env.update({
        "AWS_DEFAULT_REGION": env.get("AWS_DEFAULT_REGION") or "us-east-1",
        "AWS_REGION": env.get("AWS_REGION") or "us-east-1",
        "AWS_EC2_METADATA_DISABLED": "true",
        "SNAPSHOTS_TABLE": "",
        "INQSI_MLB_ALLOW_LOCAL_FILE_CHAMPION": "false",
        "INQSI_MLB_ML_AUTO_PROMOTE": "false",
        "PYTHONPATH": os.pathsep.join(
            value for value in (str(HELLO_WORLD), inherited_pythonpath) if value
        ),
    })
    code = r'''
import json
import inqsi_pull_history
import mlb_v3_read_api
assert callable(inqsi_pull_history.handle_pull_history_route)
event = {"path":"/v1/mlb/model/version","rawPath":"/v1/mlb/model/version","httpMethod":"GET","queryStringParameters":None}
response = mlb_v3_read_api.lambda_handler(event, None)
assert response.get("statusCode") == 200, response
body = json.loads(response.get("body") or "{}")
assert body.get("ok") is True, body
assert body.get("engine_import_ok") is True, body
assert body.get("model_version") == "INQSI-MLB-v3.1-90pct-rolling-slate-automatic-authority", body
assert body.get("productionAuthoritySource") == "gate_promoted_DynamoDB_champion_bundle_only", body
assert body.get("automaticPromotionPolicy") == "authoritative_AWS_audit_only_after_independent_90pct_authority_gates", body
assert body.get("requiredWinnerPickPolicy") == "one_official_locked_winner_prediction_for_every_mlb_game", body
assert body.get("playablePolicy") == "playability_is_separate_and_may_be_false_for_an_official_prediction", body
assert body.get("apiRuntimeVersion") == "MLB-V3-READ-API-v3-read-only", body
assert body.get("readOnly") is True, body
assert str(body.get("ml_optimization_version") or "").startswith("MLB-ML-OPTIMIZATION-v3"), body
runtime = body.get("ml_runtime_install") or {}
assert runtime.get("ok") is True, runtime
assert runtime.get("version") == "MLB-ML-RUNTIME-INSTALL-v3.8-verified-stage-promotion-authority", runtime
policy = runtime.get("accuracyTargetPolicy") or {}
assert policy.get("rolling24hAllGamesAuditTargetPct") == 90.0, policy
assert policy.get("minimumRolling24hSlateAccuracyPct") == 90.0, policy
assert policy.get("minimumOutcomeUntouchedAccuracyPct") == 90.0, policy
assert policy.get("recommendationReliabilityThresholdPct") == 90.0, policy
assert policy.get("selectedUntouchedTestPlayabilityAccuracyTargetPct") == 90.0, policy
assert policy.get("minimumExactOddsCoveragePct") == 90.0, policy
assert policy.get("rolling24hSlateAccuracyProgressMilestonesPct") == [50.0, 60.0, 70.0, 80.0], policy
assert policy.get("rolling24hSlateAccuracyProgressMilestonesReportingOnly") is True, policy
assert policy.get("automaticPromotionAfterApplicableGates") is True, policy
assert policy.get("roiPromotionGateRequired") is False, policy
assert policy.get("everyGameRetainsOfficialPick") is True, policy
assert policy.get("playabilitySeparateFromOfficialPick") is True, policy
assert "individualGameOfficialPickProbabilityFloorPct" in policy, policy
assert policy.get("individualGameOfficialPickProbabilityFloorPct") is None, policy
required = {"accuracyTargetsSeparated","legacyReliabilityOverlaySafety","singleDdbChampionAuthority","officialSemanticsFinalized","immutableFeatureFreeze","immutableLockedStorageAuthority","exactCleanCohortVectorPatch","officialFreezeBridge","canonicalLockedStorageFinalizer","lastPrelockPromotionAuthority","legacyFinalGateDisabled"}
missing = sorted(name for name in required if (runtime.get("steps") or {}).get(name) is not True)
assert not missing, {"missingRuntimeSteps": missing, "runtime": runtime}

read_calls = []
original_predict_all = mlb_v3_read_api.ENGINE.predict_all
try:
    def capture_predict_all(date, *, store, limit):
        read_calls.append({"date": date, "store": store, "limit": limit})
        return {"ok": True, "predictions": [], "count": 0}
    mlb_v3_read_api.ENGINE.predict_all = capture_predict_all
    read_event = {
        "path": "/v1/mlb/predictions",
        "rawPath": "/v1/mlb/predictions",
        "httpMethod": "GET",
        "queryStringParameters": {"date": "2026-07-16", "store": "true", "limit": "7"},
    }
    read_response = mlb_v3_read_api.lambda_handler(read_event, None)
finally:
    mlb_v3_read_api.ENGINE.predict_all = original_predict_all
assert read_response.get("statusCode") == 200, read_response
read_body = json.loads(read_response.get("body") or "{}")
assert read_calls == [{"date": "2026-07-16", "store": False, "limit": 7}], read_calls
assert read_body.get("readOnly") is True, read_body

original_runtime_status = mlb_v3_read_api.ENGINE.MLB_ML_RUNTIME_INSTALL_V3
original_predict_all = mlb_v3_read_api.ENGINE.predict_all
fail_closed_calls = []
try:
    mlb_v3_read_api.ENGINE.MLB_ML_RUNTIME_INSTALL_V3 = {**original_runtime_status, "ok": False}
    mlb_v3_read_api.ENGINE.predict_all = lambda *args, **kwargs: fail_closed_calls.append((args, kwargs))
    failed_response = mlb_v3_read_api.lambda_handler(read_event, None)
finally:
    mlb_v3_read_api.ENGINE.MLB_ML_RUNTIME_INSTALL_V3 = original_runtime_status
    mlb_v3_read_api.ENGINE.predict_all = original_predict_all
assert failed_response.get("statusCode") == 503, failed_response
failed_body = json.loads(failed_response.get("body") or "{}")
assert fail_closed_calls == [], fail_closed_calls
assert failed_body.get("predictions") == [], failed_body
assert failed_body.get("winner_predictions") == [], failed_body
print(json.dumps({"ok":True,"modelVersion":body.get("model_version"),"runtime":runtime}, indent=2))
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=90)
    if result.returncode:
        sys.stderr.write(result.stdout); sys.stderr.write(result.stderr); return result.returncode
    print(result.stdout.strip())
    print("Dedicated MLB v3 Lambda cold import and runtime contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
