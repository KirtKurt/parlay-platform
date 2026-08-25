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

EXPECTED_API = "MLB-V3-READ-API-v7-exact-persisted-prelock-public-read"
EXPECTED_AUTHORITY_CONTRACT = "MLB-AUTO-R7-QUALIFIED-CHAMPION-ONLY-v1"


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
status = int(response.get("statusCode") or 0)
body = json.loads(response.get("body") or "{{}}")
text = json.dumps(body, sort_keys=True).lower()

# The deployment validator accepts exactly two production-safe authority states:
# a genuinely qualified R7 champion, or explicit fail-closed no-champion.
# It must never require or restore retired MLB authority.
assert body.get("apiRuntimeVersion") == {EXPECTED_API!r}, body
assert body.get("authorityContractVersion") == {EXPECTED_AUTHORITY_CONTRACT!r}, body
assert body.get("readOnly") is True, body
assert body.get("legacyFallbackAllowed") is False, body
assert body.get("legacyRecommendationAuthority") is False, body
assert body.get("retiredAuthoritySuppressed") is True, body
assert body.get("retiredV15_10Eligible") is not True, body
assert "v15.10" not in text, body
assert "ranked-winner-v15" not in text, body

if status == 503:
    assert body.get("ok") is False, body
    assert body.get("status") == "NO_QUALIFIED_CHAMPION", body
    assert body.get("error") == "NO_QUALIFIED_CHAMPION", body
    assert body.get("publicationClosed") is True, body
    assert body.get("productionSelectionAllowed") is False, body
    assert body.get("requestedAuthority") == "AWS_ML_PROSPECTIVE_R7", body
    assert body.get("qualifiedChampionRequired") is True, body
    assert body.get("qualifiedChampionPresent") is False, body
    assert body.get("r7ChampionQualified") is False, body
    assert body.get("r7DeploymentIdentity") is None, body
    assert body.get("model_version") is None, body
    assert body.get("primaryAlgorithm") is None, body
    assert body.get("primaryAlgorithmActive") is False, body
    authority_state = "NO_QUALIFIED_CHAMPION"
elif status == 200:
    assert body.get("ok") is True, body
    assert body.get("publicationClosed") is False, body
    assert body.get("productionSelectionAllowed") is True, body
    assert body.get("requestedAuthority") == "AWS_ML_PROSPECTIVE_R7", body
    assert body.get("qualifiedChampionRequired") is True, body
    assert body.get("qualifiedChampionPresent") is True, body
    assert body.get("r7ChampionQualified") is True, body
    deployment_identity = body.get("r7DeploymentIdentity")
    model_version = body.get("model_version")
    primary = body.get("primaryAlgorithm")
    assert deployment_identity, body
    assert model_version, body
    assert primary, body
    safe_identity_text = json.dumps({{
        "r7DeploymentIdentity": deployment_identity,
        "model_version": model_version,
        "primaryAlgorithm": primary,
    }}, sort_keys=True).lower()
    assert "r7" in safe_identity_text, body
    assert "v15.10" not in safe_identity_text, body
    assert "ranked-winner-v15" not in safe_identity_text, body
    authority_state = "QUALIFIED_R7_CHAMPION"
else:
    raise AssertionError({{"unexpectedModelVersionStatus": status, "body": body}})

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
        "queryStringParameters": {{"date": "2026-08-25", "store": "true", "limit": "7"}},
    }}, None)
finally:
    mlb_v3_read_api.ENGINE.read_persisted_predictions = original_reader

read_status = int(read_response.get("statusCode") or 0)
read_body = json.loads(read_response.get("body") or "{{}}")
read_text = json.dumps(read_body, sort_keys=True).lower()
assert read_body.get("readOnly") is True, read_body
assert "v15.10" not in read_text, read_body
assert "ranked-winner-v15" not in read_text, read_body

if authority_state == "NO_QUALIFIED_CHAMPION":
    # Publication is closed before persisted predictions are read or projected.
    assert read_status == 503, read_response
    assert read_calls == [], read_calls
    assert read_body.get("status") == "NO_QUALIFIED_CHAMPION", read_body
    assert read_body.get("publicationClosed") is True, read_body
    assert read_body.get("productionSelectionAllowed") is False, read_body
else:
    assert read_status == 200, read_response
    assert read_calls == [{{"date": "2026-08-25", "store": False, "limit": 7}}], read_calls
    assert read_body.get("productionSelectionAllowed") is True, read_body
    assert read_body.get("qualifiedChampionPresent") is True, read_body

print(json.dumps({{
    "ok": True,
    "authorityState": authority_state,
    "modelVersionStatusCode": status,
    "apiRuntimeVersion": body.get("apiRuntimeVersion"),
    "authorityContractVersion": body.get("authorityContractVersion"),
    "retiredAuthoritySuppressed": body.get("retiredAuthoritySuppressed"),
}}, indent=2))
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
    print(
        "MLB V3 Lambda cold import and qualified-R7-only read authority contract "
        "verified; retired MLB authority remains suppressed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
