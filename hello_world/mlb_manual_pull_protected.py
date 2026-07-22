from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

mlb_scoring_run_proof = None
_scoring_proof_import_error = None
try:
    import mlb_scoring_run_proof
except Exception as exc:
    _scoring_proof_import_error = str(exc)

mlb_ml_runtime_install_v3 = None
try:
    import mlb_ml_runtime_install_v3

    _raw_runtime_status = mlb_ml_runtime_install_v3.install()
except Exception as exc:
    _raw_runtime_status = {
        "applied": False,
        "ok": False,
        "steps": {},
        "errors": [str(exc)],
    }


_REQUIRED_RUNTIME_STEPS = {
    "accuracyTargetsSeparated",
    "legacyReliabilityOverlaySafety",
    "sourceHonestFundamentals",
    "sourceHonestFundamentalsV2",
    "legacyV1ChampionRuntimeInstalledForShadowDiagnostics",
    "legacyV1AuthorityDisabled",
    "v2ShadowManualFirst",
    "officialSemanticsFinalized",
    "exactCleanCohortVectorPatch",
    "officialFreezeBridge",
    "immutableFeatureFreeze",
    "immutableLockedStorageAuthority",
    "canonicalLockedStorageFinalizer",
    "lastPrelockPromotionAuthority",
    "canonicalProbabilityAndPersistedPrelockAuthority",
    "providerNeutralCalibrationAndActionability",
    "legacyFinalGateDisabled",
    "scoringRunProof",
}

if isinstance(_raw_runtime_status, dict):
    ML_RUNTIME_INSTALL_STATUS = dict(_raw_runtime_status)
else:
    ML_RUNTIME_INSTALL_STATUS = {
        "applied": False,
        "ok": False,
        "steps": {},
        "errors": [
            "mlb_ml_runtime_install_v3.install() returned a non-dictionary status"
        ],
    }

_runtime_errors = ML_RUNTIME_INSTALL_STATUS.get("errors")
if not isinstance(_runtime_errors, list):
    _runtime_errors = [str(_runtime_errors)] if _runtime_errors else []
ML_RUNTIME_INSTALL_STATUS["errors"] = _runtime_errors

_runtime_steps = ML_RUNTIME_INSTALL_STATUS.get("steps")
if not isinstance(_runtime_steps, dict):
    _runtime_steps = {}
    ML_RUNTIME_INSTALL_STATUS["steps"] = _runtime_steps
    ML_RUNTIME_INSTALL_STATUS["errors"].append(
        "mlb_ml_runtime_install_v3.install() returned invalid step status"
    )

_runtime_steps["scoringRunProof"] = mlb_scoring_run_proof is not None
ML_RUNTIME_INSTALL_STATUS["scoringProofVersion"] = getattr(
    mlb_scoring_run_proof, "VERSION", None
)
if _scoring_proof_import_error:
    ML_RUNTIME_INSTALL_STATUS["errors"].append(
        f"mlb_scoring_run_proof import failed: {_scoring_proof_import_error}"
    )

_missing_runtime_steps = sorted(
    name for name in _REQUIRED_RUNTIME_STEPS if _runtime_steps.get(name) is not True
)
_expected_runtime_version = getattr(mlb_ml_runtime_install_v3, "VERSION", None)
ML_RUNTIME_INSTALL_STATUS["expectedVersion"] = _expected_runtime_version
if ML_RUNTIME_INSTALL_STATUS.get("applied") is not True:
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
if ML_RUNTIME_INSTALL_STATUS.get("ok") is not True:
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
if ML_RUNTIME_INSTALL_STATUS.get("errors"):
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
if (
    not _expected_runtime_version
    or ML_RUNTIME_INSTALL_STATUS.get("version") != _expected_runtime_version
):
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
if _missing_runtime_steps:
    ML_RUNTIME_INSTALL_STATUS["ok"] = False
    ML_RUNTIME_INSTALL_STATUS["missingRequiredSteps"] = _missing_runtime_steps

# Do not even import the HOT candidate writer until the exact runtime has been
# installed and attested. This makes correctness independent of usercustomize.
mlb_manual_pull = None
if ML_RUNTIME_INSTALL_STATUS.get("ok") is True:
    try:
        import mlb_manual_pull as _mlb_manual_pull

        mlb_manual_pull = _mlb_manual_pull
        ML_RUNTIME_INSTALL_STATUS["candidateWriterImported"] = True
    except Exception as exc:
        ML_RUNTIME_INSTALL_STATUS["ok"] = False
        ML_RUNTIME_INSTALL_STATUS["candidateWriterImported"] = False
        ML_RUNTIME_INSTALL_STATUS["errors"].append(
            f"mlb_manual_pull import failed after runtime installation: {exc}"
        )
else:
    ML_RUNTIME_INSTALL_STATUS["candidateWriterImported"] = False

ADMIN_TOKEN = os.environ.get("INQSI_ADMIN_API_TOKEN", "")


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(body)
    payload["mlRuntimeInstallation"] = ML_RUNTIME_INSTALL_STATUS
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token",
            "access-control-allow-methods": "POST,OPTIONS",
        },
        "body": json.dumps(payload),
    }


def _is_scheduled(event: Dict[str, Any]) -> bool:
    return not (event.get("httpMethod") or event.get("requestContext"))


def _runtime_failure(event: Dict[str, Any]) -> Dict[str, Any]:
    body = {
        "ok": False,
        "sport": "mlb",
        "error": "MLB_ML_PULL_RUNTIME_NOT_READY",
        "status": ML_RUNTIME_INSTALL_STATUS,
    }
    if _is_scheduled(event):
        raise RuntimeError(
            "MLB_SCHEDULED_PULL_PREREQUISITE_FAILED:"
            + json.dumps(body, default=str, sort_keys=True)
        )
    return _resp(500, body)


def _attach_runtime_status(response: Any) -> Any:
    if not isinstance(response, dict):
        return response
    out = dict(response)
    body = out.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        payload = {"rawBody": body}
    if isinstance(payload, dict):
        payload["mlRuntimeInstallation"] = ML_RUNTIME_INSTALL_STATUS
        out["body"] = json.dumps(payload, default=str)
    return out


def _raise_scheduled_delegate_failure(event: Dict[str, Any], response: Any) -> None:
    """Make EventBridge observe a failed delegated HOT pull as a failed invocation."""
    if not _is_scheduled(event) or not isinstance(response, dict):
        return
    body = response.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        payload = {"rawBody": body}
    try:
        status_code = int(response.get("statusCode") or 200)
    except Exception:
        status_code = 500
    candidate_failures = []
    try:
        provider_game_count = int(payload.get("count") or 0)
    except Exception:
        provider_game_count = 0
    if provider_game_count > 0:
        manifests = payload.get("provider_schedule_manifests")
        manifest_counts: Dict[str, int] = {}
        if not isinstance(manifests, list) or not manifests:
            candidate_failures.append("provider_schedule_manifest_missing")
        else:
            for manifest in manifests:
                if not isinstance(manifest, dict):
                    candidate_failures.append("provider_schedule_manifest_invalid")
                    continue
                game_date = str(manifest.get("game_date_et") or "")
                try:
                    manifest_count = int(manifest.get("gameCount") or 0)
                except Exception:
                    manifest_count = -1
                if not game_date or game_date in manifest_counts:
                    candidate_failures.append("provider_schedule_manifest_date_invalid_or_duplicate")
                else:
                    manifest_counts[game_date] = manifest_count
                if (
                    manifest.get("ok") is not True
                    or manifest.get("immutable") is not True
                    or manifest.get("fullProviderSchedule") is not True
                    or manifest.get("boundToCanonicalPull") is not True
                    or manifest_count <= 0
                    or not manifest.get("version")
                    or not manifest.get("fingerprint")
                    or not manifest.get("pk")
                    or not manifest.get("sk")
                ):
                    candidate_failures.append(
                        f"provider_schedule_manifest_authority_invalid:{game_date or 'unknown'}"
                    )
            if sum(count for count in manifest_counts.values() if count > 0) != provider_game_count:
                candidate_failures.append("provider_schedule_manifest_count_mismatch")
        if payload.get("providerScheduleManifestComplete") is not True:
            candidate_failures.append("provider_schedule_manifest_incomplete")
        winner_results = payload.get("game_winner_predictions")
        if not isinstance(winner_results, list) or not winner_results:
            candidate_failures.append("winner_prediction_results_missing")
        else:
            winner_dates = set()
            for result in winner_results:
                if not isinstance(result, dict):
                    candidate_failures.append("winner_prediction_result_invalid")
                    continue
                game_date = str(result.get("game_date_et") or "")
                winner_dates.add(game_date)
                if result.get("ok") is not True:
                    candidate_failures.append(
                        f"winner_prediction_failed:{game_date or 'unknown'}"
                    )
                if result.get("allGamesPredicted") is not True:
                    candidate_failures.append(
                        f"winner_prediction_coverage_incomplete:{game_date or 'unknown'}"
                    )
                if result.get("preLockStorageComplete") is False:
                    candidate_failures.append(
                        f"prelock_storage_incomplete:{game_date or 'unknown'}"
                    )
                try:
                    candidate_count = int(result.get("preLockStorageCandidateCount") or 0)
                    stored_count = int(result.get("preLockStoredCount") or 0)
                    game_count = int(result.get("gameCount") or 0)
                except Exception:
                    candidate_count = stored_count = game_count = -1
                if candidate_count <= 0 or candidate_count != stored_count or candidate_count != game_count:
                    candidate_failures.append(
                        f"prelock_candidate_count_mismatch:{game_date or 'unknown'}"
                    )
                if game_date not in manifest_counts or game_count != manifest_counts.get(game_date):
                    candidate_failures.append(
                        f"winner_prediction_manifest_count_mismatch:{game_date or 'unknown'}"
                    )
            if winner_dates != set(manifest_counts):
                candidate_failures.append("winner_prediction_manifest_date_mismatch")
        scoring_proofs = payload.get("scoring_proofs")
        if payload.get("scoringProofComplete") is not True:
            candidate_failures.append("scoring_run_proof_incomplete")
        if not isinstance(scoring_proofs, list) or len(scoring_proofs) != len(manifest_counts):
            candidate_failures.append("scoring_run_proof_count_mismatch")
        elif any(not isinstance(item, dict) or item.get("ok") is not True for item in scoring_proofs):
            candidate_failures.append("scoring_run_proof_failed")
    if status_code >= 400 or payload.get("ok") is False or candidate_failures:
        if candidate_failures:
            payload = dict(payload)
            payload["candidatePersistenceFailures"] = candidate_failures
        raise RuntimeError(
            "MLB_SCHEDULED_PULL_FAILED:"
            + json.dumps(payload, default=str, sort_keys=True)
        )


def _header(event: Dict[str, Any], name: str) -> str:
    headers = event.get("headers") or {}
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value or "")
    return ""


def _auth_error(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # EventBridge scheduled invocations have no HTTP request context and remain allowed.
    if not (event.get("httpMethod") or event.get("requestContext")):
        return None
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return None
    if not ADMIN_TOKEN:
        return _resp(500, {"ok": False, "sport": "mlb", "error": "INQSI_ADMIN_API_TOKEN_NOT_CONFIGURED"})
    token = _header(event, "x-inqsi-admin-token").strip()
    auth = _header(event, "authorization").strip()
    if auth.lower().startswith("bearer "):
        auth = auth.split(" ", 1)[1].strip()
    if token == ADMIN_TOKEN or auth == ADMIN_TOKEN:
        return None
    return _resp(401, {"ok": False, "sport": "mlb", "error": "ADMIN_TOKEN_REQUIRED"})


def lambda_handler(event, context):
    event = event or {}
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return _resp(200, {"ok": True})
    if ML_RUNTIME_INSTALL_STATUS.get("ok") is not True or mlb_manual_pull is None:
        return _runtime_failure(event)
    auth_error = _auth_error(event)
    if auth_error is not None:
        return auth_error
    response = _attach_runtime_status(mlb_manual_pull.lambda_handler(event, context))
    response = mlb_scoring_run_proof.attach_and_store(response, event)
    _raise_scheduled_delegate_failure(event, response)
    return response
