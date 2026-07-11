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
    env.update(
        {
            "AWS_DEFAULT_REGION": env.get("AWS_DEFAULT_REGION") or "us-east-1",
            "AWS_REGION": env.get("AWS_REGION") or "us-east-1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "SNAPSHOTS_TABLE": "",
            "INQSI_MLB_ALLOW_LOCAL_FILE_CHAMPION": "false",
            "INQSI_MLB_ML_AUTO_PROMOTE": "false",
            "PYTHONPATH": str(HELLO_WORLD),
        }
    )
    code = r'''
import json
import frontend_app
import inqsi_pull_history
import mlb_game_winner_engine as engine

assert callable(inqsi_pull_history.handle_pull_history_route)
event = {
    "resource": "/{proxy+}",
    "path": "/v1/mlb/model/version",
    "rawPath": "/v1/mlb/model/version",
    "httpMethod": "GET",
    "headers": {},
    "queryStringParameters": None,
    "pathParameters": {"proxy": "v1/mlb/model/version"},
    "requestContext": {"stage": "Prod", "httpMethod": "GET"},
    "body": None,
    "isBase64Encoded": False,
}
response = frontend_app.lambda_handler(event, None)
assert response.get("statusCode") == 200, response
body = json.loads(response.get("body") or "{}")
assert body.get("ok") is True, body
assert body.get("engine_import_ok") is True, body
assert str(body.get("model_version") or "").startswith("INQSI-MLB-v3.0"), body
assert str(body.get("ml_optimization_version") or "").startswith("MLB-ML-OPTIMIZATION-v3"), body
runtime = body.get("ml_runtime_install") or {}
assert runtime.get("ok") is True, runtime
required = {
    "legacyReliabilityOverlaySafety",
    "singleDdbChampionAuthority",
    "officialSemanticsFinalized",
    "immutableFeatureFreeze",
    "exactCleanCohortVectorPatch",
    "officialFreezeBridge",
}
missing = sorted(name for name in required if (runtime.get("steps") or {}).get(name) is not True)
assert not missing, {"missingRuntimeSteps": missing, "runtime": runtime}
print(json.dumps({
    "ok": True,
    "modelVersion": body.get("model_version"),
    "optimizationVersion": body.get("ml_optimization_version"),
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
    print("ApiFunction MLB v3 cold import and route contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
