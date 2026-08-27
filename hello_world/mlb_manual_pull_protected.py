from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

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
}

_WINNER_LIFECYCLE_DEFECT_SCOPE_VERSION = (
    "MLB-WINNER-LIFECYCLE-DEFECT-SCOPE-v1-release-separated"
)

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
        import mlb_canonical_manifest_retry_binding_patch

        manifest_retry_patch = (
            mlb_canonical_manifest_retry_binding_patch.install(_mlb_manual_pull)
        )
        if manifest_retry_patch.get("ok") is not True:
            raise RuntimeError(
                "canonical manifest retry binding patch failed: "
                + json.dumps(manifest_retry_patch, default=str, sort_keys=True)
            )
        mlb_manual_pull = _mlb_manual_pull
        ML_RUNTIME_INSTALL_STATUS["steps"][
            "canonicalManifestRetryBinding"
        ] = True
        ML_RUNTIME_INSTALL_STATUS[
            "canonicalManifestRetryBindingPatch"
        ] = manifest_retry_patch
        ML_RUNTIME_INSTALL_STATUS["candidateWriterImported"] = True
    except Exception as exc:
        ML_RUNTIME_INSTALL_STATUS["ok"] = False
        ML_RUNTIME_INSTALL_STATUS["steps"][
            "canonicalManifestRetryBinding"
        ] = False
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


def _scoped_lifecycle_defects(
    result: Dict[str, Any],
) -> Optional[tuple[bool, bool]]:
    if not isinstance(result, dict):
        return None
    winner_defect = result.get("winnerLifecycleOperationalDefect")
    release_defect = result.get("releasePlayabilityOperationalDefect")
    expected_scopes = []
    if winner_defect is True:
        expected_scopes.append("WINNER_LIFECYCLE")
    if release_defect is True:
        expected_scopes.append("RELEASE_PLAYABILITY")
    if not (
        result.get("operationalDefectScopeVersion")
        == _WINNER_LIFECYCLE_DEFECT_SCOPE_VERSION
        and isinstance(winner_defect, bool)
        and isinstance(release_defect, bool)
        and result.get("operationalDefectScopes") == expected_scopes
        and (result.get("operationalDefect") is True) == bool(expected_scopes)
    ):
        return None
    return winner_defect, release_defect


def _canonical_locked_storage_failures(
    result: Dict[str, Any], game_date: str
) -> list[str]:
    """Validate canonical lock persistence independently of defect scoping."""

    suffix = game_date or "unknown"
    failures = []
    try:
        candidate_count = int(
            result.get("canonicalLockedStorageCandidateCount") or 0
        )
        if candidate_count < 0:
            raise ValueError("negative canonical candidate count")
    except Exception:
        candidate_count = -1
        failures.append(f"canonical_locked_storage_contract_invalid:{suffix}")

    storage_errors = result.get("canonicalLockedStorageErrors")
    if bool(storage_errors):
        failures.append(f"canonical_locked_storage_errors:{suffix}")

    if candidate_count > 0:
        if result.get("canonicalLockedStorageComplete") is not True:
            failures.append(f"canonical_locked_storage_incomplete:{suffix}")
        try:
            stored_count = int(result.get("canonicalLockedStoredCount") or 0)
        except Exception:
            stored_count = -1
        if stored_count != candidate_count:
            failures.append(f"canonical_locked_storage_count_mismatch:{suffix}")
    return failures


def _winner_lifecycle_health(payload: Dict[str, Any]) -> Dict[str, Any]:
    results = [
        result
        for result in (payload.get("game_winner_predictions") or [])
        if isinstance(result, dict)
    ]
    scoped = [
        (result, defects)
        for result in results
        if (defects := _scoped_lifecycle_defects(result)) is not None
    ]
    winner_defect_dates = sorted({
        str(result.get("game_date_et") or "unknown")
        for result, defects in scoped
        if defects[0]
    })
    release_defect_dates = sorted({
        str(result.get("game_date_et") or "unknown")
        for result, defects in scoped
        if defects[1]
    })
    scope_complete = len(scoped) == len(results)
    return {
        "version": _WINNER_LIFECYCLE_DEFECT_SCOPE_VERSION,
        "resultCount": len(results),
        "scopedResultCount": len(scoped),
        "scopeComplete": scope_complete,
        "winnerLifecycleHealthy": (
            not winner_defect_dates if scope_complete else None
        ),
        "winnerLifecycleOperationalDefectDates": winner_defect_dates,
        "releasePlayabilityHealthy": (
            not release_defect_dates if scope_complete else None
        ),
        "releasePlayabilityOperationalDefectDates": release_defect_dates,
        "releasePlayabilityFailClosed": True,
    }


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
        lifecycle_health = _winner_lifecycle_health(payload)
        payload = {
            "winnerLifecycleHealth": lifecycle_health,
            **{
                key: value
                for key, value in payload.items()
                if key != "winnerLifecycleHealth"
            },
        }
        payload["mlRuntimeInstallation"] = ML_RUNTIME_INSTALL_STATUS
        out["body"] = json.dumps(payload, default=str)
    return out


def _raise_scheduled_delegate_failure(event: Dict[str, Any], response: Any) -> None:
    """Make EventBridge observe a failed delegated HOT pull as a failed invocation.

    The storage contract is lifecycle-aware. Open pre-lock predictions must be
    durably written, while post-cutoff status rows such as ``MISSED_LOCK`` or
    ``LOCKED_NO_PREDICTION_DATA`` are evidence, not prediction candidates.
    """

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
                lifecycle_aware = result.get("preLockStorageLifecycleAware") is True
                lifecycle_complete_for_result = (
                    result.get("displayStatusCoverageComplete") is True
                    and result.get("lifecycleCoverageComplete") is True
                    and result.get("preLockStorageComplete") is True
                    and result.get("preLockStorageDispositionComplete") is True
                )
                # A recommendation can be intentionally non-actionable (for example
                # NEGATIVE_EV_GUARD) without being an operational persistence failure.
                # Only fail the scheduled ingest when the result is explicitly an
                # operational defect or its lifecycle/storage contract is incomplete.
                scoped_defects = _scoped_lifecycle_defects(result)
                scoped_winner_defect = (
                    scoped_defects[0] if scoped_defects is not None else None
                )
                # Release/playability assessment failures keep wagering blocked,
                # but they do not mean that the canonical winner or its durable
                # pre-lock storage failed.  A scoped delegate result lets the
                # ingest alarm distinguish those lanes.  Unscoped/older results
                # retain the conservative legacy behavior and fail closed.
                operational_winner_failure = (
                    scoped_winner_defect is True
                    if scoped_winner_defect is not None
                    else result.get("operationalDefect") is True
                )
                canonical_storage_failures = _canonical_locked_storage_failures(
                    result, game_date
                )
                candidate_failures.extend(canonical_storage_failures)
                hard_winner_failure = bool(
                    operational_winner_failure
                    or canonical_storage_failures
                    or (
                        result.get("ok") is not True
                        and not lifecycle_complete_for_result
                    )
                )
                if hard_winner_failure:
                    candidate_failures.append(
                        f"winner_prediction_failed:{game_date or 'unknown'}"
                    )

                if lifecycle_aware:
                    lifecycle_complete = (
                        result.get("displayStatusCoverageComplete") is True
                        and result.get("lifecycleCoverageComplete") is True
                    )
                    if result.get("allGamesPredicted") is not True and not lifecycle_complete:
                        candidate_failures.append(
                            f"winner_prediction_coverage_incomplete:{game_date or 'unknown'}"
                        )
                    if result.get("preLockStorageComplete") is not True:
                        candidate_failures.append(
                            f"prelock_storage_incomplete:{game_date or 'unknown'}"
                        )
                    try:
                        candidate_count = int(result.get("preLockStorageCandidateCount") or 0)
                        stored_count = int(result.get("preLockStoredCount") or 0)
                        game_count = int(result.get("gameCount") or 0)
                        disposition_count = int(result.get("preLockStorageDispositionCount") or 0)
                    except Exception:
                        candidate_count = stored_count = game_count = disposition_count = -1
                    if candidate_count != stored_count:
                        candidate_failures.append(
                            f"prelock_candidate_count_mismatch:{game_date or 'unknown'}"
                        )
                    if (
                        result.get("preLockStorageDispositionComplete") is not True
                        or disposition_count != game_count
                    ):
                        candidate_failures.append(
                            f"prelock_storage_disposition_incomplete:{game_date or 'unknown'}"
                        )
                else:
                    # Compatibility path for an older delegate result. It keeps
                    # the original strict all-games candidate contract until the
                    # lifecycle-aware finalizer is installed in the same artifact.
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
    try:
        _raise_scheduled_delegate_failure(event, response)
    except RuntimeError as exc:
        message = str(exc)
        prefix = "MLB_SCHEDULED_PULL_FAILED:"
        failure_payload = {}
        if message.startswith(prefix):
            try:
                parsed = json.loads(message[len(prefix):])
                failure_payload = parsed if isinstance(parsed, dict) else {}
            except Exception:
                failure_payload = {}
        raw_failures = failure_payload.get("candidatePersistenceFailures")
        failures = (
            [str(failure) for failure in raw_failures]
            if isinstance(raw_failures, list)
            else []
        )
        manifest_only_failure = bool(failures) and all(
            failure == "provider_schedule_manifest_incomplete"
            or failure == "provider_schedule_manifest_missing"
            or failure.startswith("provider_schedule_manifest_authority_invalid:")
            for failure in failures
        )
        if not _is_scheduled(event) or not manifest_only_failure:
            raise
        # The canonical writer is intrinsically idempotent by 15-minute slot.
        # Re-running the same scheduled event once allows a transient authority-
        # binding race to converge without creating a second canonical observation.
        # Mixed failures are never retried here; they remain fail-closed.
        retry_event = dict(event)
        retry_event["force"] = True
        retry_event["manifest_binding_retry"] = True
        response = _attach_runtime_status(
            mlb_manual_pull.lambda_handler(retry_event, context)
        )
        _raise_scheduled_delegate_failure(retry_event, response)
    return response
