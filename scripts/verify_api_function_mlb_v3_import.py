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

EXPECTED_MODEL = "INQSI-MLB-v5.0-ranked-winner-v15.10-active-ensemble"
EXPECTED_RUNTIME = (
    "MLB-ML-RUNTIME-INSTALL-v4.4-ranked-winner-v15.10-"
    "prelock-persistence-verified-stage-promotion-authority-"
    "verified-active-model-authority"
)
EXPECTED_API = "MLB-V3-READ-API-v6-ranked-winner-v15.10"
EXPECTED_SELECTOR = "INQSI-MLB-RANKED-WINNER-v15.10.0-active-ensemble"
EXPECTED_POLICY = "2026-07-24-mlb-ranked-winner-primary-v1"


def main() -> int:
    env = dict(os.environ)
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
    code = rf'''
import json
import os
import sys
from pathlib import Path

task_root = Path(os.environ["INQSI_MLB_LAMBDA_TASK_ROOT"]).resolve()
assert task_root.is_dir(), task_root
assert all(not entry or Path(entry).resolve() != task_root for entry in sys.path)
assert "mlb_game_winner_engine" not in sys.modules
sys.path.insert(0, str(task_root))

import frontend_app
import inqsi_pull_history
import mlb_v3_read_api

assert callable(frontend_app.lambda_handler)
assert callable(inqsi_pull_history.handle_pull_history_route)
response = mlb_v3_read_api.lambda_handler({{
    "path": "/v1/mlb/model/version",
    "rawPath": "/v1/mlb/model/version",
    "httpMethod": "GET",
    "queryStringParameters": None,
}}, None)
assert response.get("statusCode") == 200, response
body = json.loads(response.get("body") or "{{}}")
assert body.get("ok") is True, body
assert body.get("engine_import_ok") is True, body
assert body.get("model_version") == {EXPECTED_MODEL!r}, body
assert body.get("apiRuntimeVersion") == {EXPECTED_API!r}, body
assert body.get("primaryAlgorithm") == {EXPECTED_SELECTOR!r}, body
assert body.get("primaryAlgorithmActive") is True, body
assert body.get("rankedWinnerPolicyVersion") == {EXPECTED_POLICY!r}, body
assert body.get("productionAuthoritySource") == "mlb_ranked_winner_v15_10_active_ensemble", body
assert body.get("allowedProductionOutput") == ["PICK"], body
assert body.get("productionSelectionAllowed") is True, body
assert body.get("automaticWagerAllowed") is False, body
assert body.get("legacyRecommendationAuthority") is False, body
assert body.get("legacyFallbackAllowed") is False, body
assert body.get("precisionHitRateEvidencePassed") is False, body
assert body.get("runtimeAuthorityActivationAvailable") is True, body
assert body.get("requiredWinnerPickPolicy") == "one active-model ranked winner PICK for every valid MLB game", body
assert body.get("readOnly") is True, body

runtime = body.get("ml_runtime_install") or {{}}
assert runtime.get("ok") is True, runtime
assert runtime.get("version") == {EXPECTED_RUNTIME!r}, runtime
assert runtime.get("rankedWinnerAllowedOutput") == ["PICK"], runtime
assert runtime.get("winnerPickRequiredForEveryValidEvent") is True, runtime
assert runtime.get("precisionQualificationSeparateFromPick") is True, runtime
assert runtime.get("legacyRecommendationAuthority") is False, runtime
assert runtime.get("automaticWagerAllowed") is False, runtime
required = {{
    "accuracyTargetsSeparated",
    "legacyReliabilityOverlaySafety",
    "sourceHonestFundamentals",
    "sourceHonestFundamentalsV2",
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
    "rankedWinnerV15_10DirectionInstalled",
    "rankedWinnerV15_10SelectionInstalled",
}}
missing = sorted(name for name in required if (runtime.get("steps") or {{}}).get(name) is not True)
assert not missing, {{"missingRuntimeSteps": missing, "runtime": runtime}}

read_calls = []
original_reader = mlb_v3_read_api.ENGINE.read_persisted_predictions
try:
    def capture(date, *, store, limit):
        read_calls.append({{"date": date, "store": store, "limit": limit}})
        return {{"ok": True, "predictions": [], "count": 0}}
    mlb_v3_read_api.ENGINE.read_persisted_predictions = capture
    read_response = mlb_v3_read_api.lambda_handler({{
        "path": "/v1/mlb/predictions",
        "rawPath": "/v1/mlb/predictions",
        "httpMethod": "GET",
        "queryStringParameters": {{"date": "2026-07-24", "store": "true", "limit": "7"}},
    }}, None)
finally:
    mlb_v3_read_api.ENGINE.read_persisted_predictions = original_reader
assert read_response.get("statusCode") == 200, read_response
assert read_calls == [{{"date": "2026-07-24", "store": False, "limit": 7}}], read_calls
read_body = json.loads(read_response.get("body") or "{{}}")
assert read_body.get("readOnly") is True, read_body
assert read_body.get("primaryAlgorithm") == {EXPECTED_SELECTOR!r}, read_body

original_runtime = mlb_v3_read_api.ENGINE.MLB_ML_RUNTIME_INSTALL_V3
fail_calls = []
try:
    mlb_v3_read_api.ENGINE.MLB_ML_RUNTIME_INSTALL_V3 = {{**original_runtime, "ok": False}}
    mlb_v3_read_api.ENGINE.read_persisted_predictions = lambda *a, **k: fail_calls.append((a, k))
    failed = mlb_v3_read_api.lambda_handler({{
        "path": "/v1/mlb/predictions",
        "rawPath": "/v1/mlb/predictions",
        "httpMethod": "GET",
        "queryStringParameters": {{"date": "2026-07-24"}},
    }}, None)
finally:
    mlb_v3_read_api.ENGINE.MLB_ML_RUNTIME_INSTALL_V3 = original_runtime
    mlb_v3_read_api.ENGINE.read_persisted_predictions = original_reader
assert failed.get("statusCode") == 503, failed
assert fail_calls == [], fail_calls
print(json.dumps({{"ok": True, "modelVersion": body.get("model_version"), "runtimeVersion": runtime.get("version")}}, indent=2))
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
    print("MLB V15.10 Lambda cold import, one-pick authority, read-only, and fail-closed contracts verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
