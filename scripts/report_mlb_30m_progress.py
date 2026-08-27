#!/usr/bin/env python3
"""Post a read-only 30-minute MLB / MLB AUTO / R7 production pulse.

The script is intentionally limited to:
- CloudFormation resource discovery;
- synchronous read/status Lambda invocations;
- CloudWatch metric reads; and
- a GitHub issue comment used as the notification channel and delta store.

It never calls a trainer mutation mode, DynamoDB, S3, promotion APIs, or any
prediction/lock writer. Production authority remains fail-closed.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
ROOT_STACK = os.environ.get("MLB_ROOT_STACK", "parlay-platform-dev")
AUTO_STACK = os.environ.get("MLB_AUTO_STACK", "parlay-platform-mlb-auto-llm")
ISSUE_NUMBER = int(os.environ.get("MLB_PROGRESS_ISSUE_NUMBER", "567"))
R7_EXPERIMENT_ID = os.environ.get(
    "MLB_R7_EXPERIMENT_ID", "mlb-v2-2026-08-03-future-prospective-r7"
)
REPO = os.environ.get("GITHUB_REPOSITORY", "KirtKurt/parlay-platform")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
RUN_ATTEMPT = os.environ.get("GITHUB_RUN_ATTEMPT", "")
ET = ZoneInfo("America/New_York")
STATE_MARKER = "MLB_PROGRESS_STATE_BASE64"
PULSE_STALE_AFTER_MINUTES = int(os.environ.get("MLB_PROGRESS_STALE_AFTER_MINUTES", "40"))
PULSE_TARGET_CADENCE_MINUTES = int(
    os.environ.get("MLB_PROGRESS_TARGET_CADENCE_MINUTES", "30")
)
PULSE_CADENCE_GRACE_MINUTES = int(
    os.environ.get("MLB_PROGRESS_CADENCE_GRACE_MINUTES", "5")
)
CANONICAL_R7_RECOVERY_WORKFLOW = "unified-mlb-learning-recovery-once.yml"
LEGACY_R7_REPAIR_WORKFLOW = "repair-mlb-training-continuity-now.yml"


class CommandError(RuntimeError):
    pass


def _run(
    args: list[str],
    *,
    check: bool = True,
    text: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=text,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        stderr = (result.stderr or "").strip()[-1000:]
        raise CommandError(f"command failed ({result.returncode}): {' '.join(args[:4])}: {stderr}")
    return result


def _json_loads(value: str, default: Any = None) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return default


def _plain_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}:{str(exc)[:300]}"


def _resolve_function(stack_name: str, logical_id: str) -> tuple[Optional[str], Optional[str]]:
    try:
        result = _run(
            [
                "aws",
                "cloudformation",
                "describe-stack-resource",
                "--stack-name",
                stack_name,
                "--logical-resource-id",
                logical_id,
                "--region",
                AWS_REGION,
                "--query",
                "StackResourceDetail.PhysicalResourceId",
                "--output",
                "text",
            ],
            timeout=60,
        )
        value = (result.stdout or "").strip()
        if not value or value == "None":
            raise RuntimeError("physical function name missing")
        return value, None
    except Exception as exc:
        return None, _plain_error(exc)


def _invoke(function_name: Optional[str], payload: Mapping[str, Any]) -> dict[str, Any]:
    if not function_name:
        return {
            "ok": False,
            "functionName": None,
            "functionError": "function_not_resolved",
            "payload": {},
        }
    with tempfile.TemporaryDirectory(prefix="mlb-pulse-") as tmp:
        response_path = Path(tmp) / "response.json"
        try:
            result = _run(
                [
                    "aws",
                    "lambda",
                    "invoke",
                    "--function-name",
                    function_name,
                    "--region",
                    AWS_REGION,
                    "--cli-binary-format",
                    "raw-in-base64-out",
                    "--payload",
                    json.dumps(payload, separators=(",", ":")),
                    str(response_path),
                ],
                check=False,
                timeout=180,
            )
            metadata = _json_loads(result.stdout or "{}", {}) or {}
            raw = response_path.read_text(encoding="utf-8") if response_path.exists() else ""
            body = _json_loads(raw, {}) or {}
            function_error = metadata.get("FunctionError")
            if result.returncode != 0:
                function_error = (result.stderr or "").strip()[-500:] or "lambda_invoke_failed"
            return {
                "ok": result.returncode == 0 and not function_error,
                "functionName": function_name,
                "functionError": function_error,
                "statusCode": metadata.get("StatusCode"),
                "executedVersion": metadata.get("ExecutedVersion"),
                "payload": body if isinstance(body, dict) else {"raw": raw[:1000]},
            }
        except Exception as exc:
            return {
                "ok": False,
                "functionName": function_name,
                "functionError": _plain_error(exc),
                "payload": {},
            }


def _unwrap_api(invocation: Mapping[str, Any]) -> tuple[Optional[int], dict[str, Any]]:
    payload = invocation.get("payload") or {}
    if not isinstance(payload, Mapping):
        return None, {}
    if "statusCode" not in payload:
        return None, dict(payload)
    status = payload.get("statusCode")
    try:
        http_status = int(status)
    except Exception:
        http_status = None
    body = payload.get("body")
    if isinstance(body, str):
        parsed = _json_loads(body, {})
        return http_status, parsed if isinstance(parsed, dict) else {}
    return http_status, dict(body) if isinstance(body, Mapping) else {}


def _cloudwatch_sum(function_name: Optional[str], metric_name: str, minutes: int = 35) -> Optional[float]:
    if not function_name:
        return None
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    try:
        result = _run(
            [
                "aws",
                "cloudwatch",
                "get-metric-statistics",
                "--namespace",
                "AWS/Lambda",
                "--metric-name",
                metric_name,
                "--dimensions",
                f"Name=FunctionName,Value={function_name}",
                "--start-time",
                start.isoformat(),
                "--end-time",
                end.isoformat(),
                "--period",
                "300",
                "--statistics",
                "Sum",
                "--region",
                AWS_REGION,
                "--output",
                "json",
            ],
            timeout=60,
        )
        payload = _json_loads(result.stdout or "{}", {}) or {}
        total = sum(float(row.get("Sum") or 0.0) for row in payload.get("Datapoints") or [])
        return round(total, 3)
    except Exception:
        return None


def _latest_workflow_run(workflow_file: str) -> dict[str, Any]:
    try:
        result = _run(
            [
                "gh",
                "api",
                f"repos/{REPO}/actions/workflows/{workflow_file}/runs?per_page=1",
            ],
            timeout=60,
        )
        runs = (_json_loads(result.stdout or "{}", {}) or {}).get("workflow_runs") or []
        if not runs:
            return {}
        row = runs[0]
        return {
            "runId": row.get("id"),
            "status": row.get("status"),
            "conclusion": row.get("conclusion"),
            "createdAtUtc": row.get("created_at"),
            "updatedAtUtc": row.get("updated_at"),
            "url": row.get("html_url"),
            "headSha": row.get("head_sha"),
            "event": row.get("event"),
            "workflowFile": workflow_file,
        }
    except Exception as exc:
        return {"error": _plain_error(exc)}


def _latest_continuity_run() -> dict[str, Any]:
    """Return canonical R7 recovery evidence, with an explicit legacy fallback.

    The unified workflow is the current recovery owner. The old continuity
    repair is consulted only when the canonical workflow has never run or its
    API lookup is unavailable, so a stale legacy failure cannot mask current
    recovery progress.
    """
    canonical = _latest_workflow_run(CANONICAL_R7_RECOVERY_WORKFLOW)
    if canonical.get("runId"):
        canonical["workflowKind"] = "canonical_unified_recovery"
        return canonical

    legacy = _latest_workflow_run(LEGACY_R7_REPAIR_WORKFLOW)
    if legacy.get("runId"):
        legacy["workflowKind"] = "legacy_repair_fallback"
        if canonical.get("error"):
            legacy["canonicalLookupError"] = canonical["error"]
        else:
            legacy["canonicalLookupState"] = "no_runs"
        return legacy

    errors = [
        str(value)
        for value in (canonical.get("error"), legacy.get("error"))
        if value
    ]
    return {
        "workflowKind": "unavailable",
        "workflowFile": CANONICAL_R7_RECOVERY_WORKFLOW,
        "error": ";".join(errors) if errors else "no_canonical_or_legacy_runs",
    }


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except Exception:
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> Optional[int]:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _nested(mapping: Mapping[str, Any], path: str, default: Any = None) -> Any:
    value: Any = mapping
    for key in path.split("."):
        if not isinstance(value, Mapping):
            return default
        value = value.get(key)
    return default if value is None else value


def _partition_counts(status: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    direct = status.get("partitionCounts") or {}
    partitions = manifest.get("partitions") or {}
    for name in ("train", "validation", "prospectiveTest"):
        value = direct.get(name) if isinstance(direct, Mapping) else None
        if value is None and isinstance(partitions, Mapping):
            value = _nested(partitions, f"{name}.rowCount")
        result[name] = _integer(value) or 0
    return result


def _metric(container: Any, keys: Iterable[str]) -> Optional[float]:
    if not isinstance(container, Mapping):
        return None
    for key in keys:
        if key in container:
            parsed = _number(container.get(key))
            if parsed is not None:
                return parsed
    for child_key in ("metrics", "classification", "calibration", "summary", "overall"):
        child = container.get(child_key)
        if isinstance(child, Mapping):
            found = _metric(child, keys)
            if found is not None:
                return found
    return None


def _normalise_accuracy(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if 1.0 < value <= 100.0:
        return value / 100.0
    return value


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _grading_count(value: Any, field: str, errors: list[str]) -> Optional[int]:
    parsed = _number(value)
    if parsed is None:
        errors.append(f"{field}_MISSING_OR_NON_NUMERIC")
        return None
    if parsed < 0:
        errors.append(f"{field}_NEGATIVE")
        return None
    if not parsed.is_integer():
        errors.append(f"{field}_NOT_INTEGER")
        return None
    return int(parsed)


def _grading_cohort(
    source: Mapping[str, Any],
    *,
    available: bool,
    name: str,
    key: str,
    graded_field: str,
    correct_field: str,
    accuracy_field: str,
    recent_days_field: Optional[str] = None,
) -> dict[str, Any]:
    """Extract one grading cohort atomically and verify its arithmetic.

    Counts and accuracy always come from the same source mapping. Legitimate
    zero values are preserved; they never trigger a fallback to another
    cohort. Invalid tuples stay visible as diagnostics but their accuracy is
    not presented as trusted telemetry.
    """
    result: dict[str, Any] = {
        "name": name,
        "key": key,
        "available": available,
        "valid": None,
        "gradedPicks": None,
        "correctPicks": None,
        "accuracy": None,
        "reportedAccuracy": None,
        "expectedAccuracy": None,
        "recentDays": None,
        "errors": [],
    }
    if not available:
        return result

    errors: list[str] = []
    graded = _grading_count(source.get(graded_field), "GRADED", errors)
    correct = _grading_count(source.get(correct_field), "CORRECT", errors)
    reported_accuracy = _normalise_accuracy(_number(source.get(accuracy_field)))
    recent_days = (
        _integer(source.get(recent_days_field)) if recent_days_field else None
    )

    if correct is not None and graded is not None and correct > graded:
        errors.append("CORRECT_EXCEEDS_GRADED")
    if reported_accuracy is not None and not 0.0 <= reported_accuracy <= 1.0:
        errors.append("ACCURACY_OUT_OF_RANGE")

    expected_accuracy: Optional[float] = None
    if graded is not None and correct is not None and correct <= graded:
        expected_accuracy = correct / graded if graded else None
        if graded > 0:
            if reported_accuracy is None:
                errors.append("ACCURACY_MISSING_WITH_GRADED_PICKS")
            elif abs(reported_accuracy - expected_accuracy) > 0.00001:
                errors.append("ACCURACY_COUNT_MISMATCH")
        elif reported_accuracy not in (None, 0.0):
            errors.append("ACCURACY_PRESENT_WITH_ZERO_GRADED_PICKS")

    valid = not errors
    result.update(
        {
            "valid": valid,
            "gradedPicks": graded,
            "correctPicks": correct,
            "accuracy": reported_accuracy if valid and graded else None,
            "reportedAccuracy": reported_accuracy,
            "expectedAccuracy": expected_accuracy,
            "recentDays": recent_days,
            "errors": errors,
        }
    )
    return result


def _extract_state(
    *,
    r7_invocation: Mapping[str, Any],
    model_invocation: Mapping[str, Any],
    today_invocation: Mapping[str, Any],
    auto_invocation: Mapping[str, Any],
    auto_invocations_35m: Optional[float],
    auto_errors_35m: Optional[float],
    continuity_run: Mapping[str, Any],
    discovery_errors: list[str],
) -> dict[str, Any]:
    model_http, model = _unwrap_api(model_invocation)
    today_http, today = _unwrap_api(today_invocation)
    auto_http, auto = _unwrap_api(auto_invocation)
    r7 = r7_invocation.get("payload") or {}
    if not isinstance(r7, Mapping):
        r7 = {}

    training_health = _first_mapping(r7.get("trainingHealth"))
    latest = _first_mapping(training_health.get("latestRun"), r7.get("latestStatus"))
    selection_health = _first_mapping(r7.get("selectionCaptureHealth"))
    selection_latest = _first_mapping(
        selection_health.get("latestRun"), r7.get("latestSelectionCaptureStatus")
    )
    manifest = _first_mapping(r7.get("manifest"))
    candidate = _first_mapping(r7.get("latestCandidate"))
    champion = _first_mapping(r7.get("champion"))
    continuity = _first_mapping(latest.get("canonicalSlateContinuity"))
    milestones = _first_mapping(latest.get("milestones"))
    counts = _partition_counts(latest, manifest)

    validation = _first_mapping(latest.get("validation"), candidate.get("validation"))
    prospective = _first_mapping(
        latest.get("prospectiveTest"), candidate.get("prospectiveTest")
    )
    gate = _first_mapping(latest.get("promotionGate"), candidate.get("promotionGate"))
    promotion = _first_mapping(latest.get("promotion"), candidate.get("promotion"))

    processed_dates = continuity.get("processedSlateDates") or []
    finalized_dates = continuity.get("finalizedGameSlateDates") or continuity.get(
        "finalizedSlateDates"
    ) or []
    if not finalized_dates:
        authorities = continuity.get("finalizedSlateAuthorities") or {}
        if isinstance(authorities, Mapping):
            finalized_dates = sorted(authorities)

    selection_capture = _first_mapping(selection_latest.get("selectionCapture"))
    model_id = model.get("model_version") or model.get("modelId")
    predictions = today.get("winner_predictions") or today.get("predictions") or []
    prediction_count = today.get("count")
    if prediction_count is None and isinstance(predictions, list):
        prediction_count = len(predictions)

    card = _first_mapping(auto.get("card"))
    audit_value = auto.get("audit")
    autonomy_value = auto.get("autonomyState")
    audit = _first_mapping(audit_value)
    autonomy = _first_mapping(autonomy_value)
    picks = card.get("picks") or []
    pick_count = card.get("gameCount")
    if pick_count is None and isinstance(picks, list):
        pick_count = len(picks)
    authority_counts = {
        "BEDROCK_LLM": 0,
        "AWS_ML_PROSPECTIVE_R7": 0,
        "UNKNOWN": 0,
    }
    card_authority = str(card.get("decisionAuthority") or "").strip()
    if isinstance(picks, list) and picks:
        for pick in picks:
            authority = (
                str((pick or {}).get("decisionAuthority") or card_authority).strip()
                if isinstance(pick, Mapping)
                else card_authority
            )
            key = authority if authority in authority_counts else "UNKNOWN"
            authority_counts[key] += 1
    elif _integer(pick_count):
        key = card_authority if card_authority in authority_counts else "UNKNOWN"
        authority_counts[key] = int(_integer(pick_count) or 0)
    invocations = _number(auto_invocations_35m)
    errors = _number(auto_errors_35m)
    error_rate = errors / invocations if invocations and errors is not None else None
    slate_date = str(auto.get("slateDateEt") or "unknown")
    current_slate_grading = _grading_cohort(
        audit,
        available=isinstance(audit_value, Mapping),
        name="current_slate",
        key=f"current_slate:{slate_date}",
        graded_field="graded",
        correct_field="correct",
        accuracy_field="accuracy",
    )
    trailing_grading = _grading_cohort(
        autonomy,
        available=isinstance(autonomy_value, Mapping),
        name="trailing_14_days",
        key=f"trailing_14_days:as_of:{slate_date}",
        graded_field="recentGradedPicks",
        correct_field="recentCorrectPicks",
        accuracy_field="recentAccuracy",
        recent_days_field="recentDays",
    )
    if current_slate_grading["available"]:
        primary_grading = current_slate_grading
    elif trailing_grading["available"]:
        primary_grading = trailing_grading
    else:
        primary_grading = {
            "name": "unavailable",
            "key": f"unavailable:{slate_date}",
            "available": False,
            "valid": None,
            "gradedPicks": None,
            "correctPicks": None,
            "accuracy": None,
            "errors": [],
        }

    training_status = latest.get("status")
    model_trained = bool(
        latest.get("modelTrained") is True
        or candidate.get("artifactDigest")
        or candidate.get("modelId")
    )
    promotion_decision = gate.get("promotionDecision") or gate.get("decision") or promotion.get(
        "reason"
    )
    gate_passed = gate.get("ok") is True or promotion_decision in {
        "PROMOTE",
        "AUTO_SHADOW_APPROVAL_ELIGIBLE",
        "PASS",
    }
    live_authority = bool(
        latest.get("liveInferenceAuthority") is True
        or promotion.get("runtimeAuthorityActivated") is True
    )
    production_changed = bool(latest.get("productionAuthorityChanged") is True)

    blockers: list[str] = list(discovery_errors)
    for value in (
        r7_invocation.get("functionError"),
        model_invocation.get("functionError"),
        today_invocation.get("functionError"),
        auto_invocation.get("functionError"),
    ):
        if value:
            blockers.append(str(value))
    for value in (
        continuity.get("blocker"),
        continuity.get("blockedSlateDate"),
        latest.get("waitReason"),
        latest.get("failure", {}).get("code") if isinstance(latest.get("failure"), Mapping) else None,
        auto.get("error"),
    ):
        if value:
            blockers.append(str(value))
    if model.get("status") == "NO_QUALIFIED_CHAMPION":
        blockers.append("NO_QUALIFIED_CHAMPION")
    if errors and errors > 0:
        blockers.append(f"MLB_AUTO_LAMBDA_ERRORS_35M:{int(errors)}")
    if authority_counts["UNKNOWN"] > 0:
        blockers.append(
            f"MLB_AUTO_UNKNOWN_PICK_AUTHORITY:{authority_counts['UNKNOWN']}"
        )
    if (
        authority_counts["AWS_ML_PROSPECTIVE_R7"] > 0
        and model.get("qualifiedChampionPresent") is not True
    ):
        blockers.append(
            "MLB_AUTO_R7_AUTHORITY_WITH_NO_QUALIFIED_CHAMPION:"
            + str(authority_counts["AWS_ML_PROSPECTIVE_R7"])
        )
    for blocker_name, cohort in (
        ("CURRENT_SLATE", current_slate_grading),
        ("TRAILING_14_DAY", trailing_grading),
    ):
        if cohort.get("available") and cohort.get("valid") is False:
            blockers.append(
                f"MLB_AUTO_{blocker_name}_GRADING_INVALID:"
                + ",".join(str(item) for item in cohort.get("errors") or [])
            )

    state = {
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "experimentId": R7_EXPERIMENT_ID,
        "mlb": {
            "endpointHttpStatus": model_http,
            "todayHttpStatus": today_http,
            "authorityStatus": model.get("status") or model.get("error"),
            "activeModelId": model_id,
            "qualifiedChampionPresent": bool(model.get("qualifiedChampionPresent") is True),
            "publicationClosed": bool(model.get("publicationClosed") is True),
            "productionSelectionAllowed": bool(model.get("productionSelectionAllowed") is True),
            "winnerPredictionCount": _integer(prediction_count) or 0,
            "retiredAuthoritySuppressed": bool(model.get("retiredAuthoritySuppressed") is True),
            "retiredV15_10Eligible": bool(model.get("retiredV15_10Eligible") is True),
            "r7DeploymentIdentity": model.get("r7DeploymentIdentity"),
            "apiRuntimeVersion": model.get("apiRuntimeVersion"),
        },
        "mlbAuto": {
            "httpStatus": auto_http,
            "slateDateEt": auto.get("slateDateEt"),
            "scheduledGames": _integer(auto.get("scheduledGames")) or 0,
            "cardPublished": bool(auto.get("cardPublished") is True),
            "pickCount": _integer(pick_count) or 0,
            "cardDecisionAuthority": card_authority or None,
            "bedrockPickCount": authority_counts["BEDROCK_LLM"],
            "r7AuthorityPickCount": authority_counts["AWS_ML_PROSPECTIVE_R7"],
            "unknownAuthorityPickCount": authority_counts["UNKNOWN"],
            "gradingCohort": primary_grading.get("name"),
            "gradingCohortKey": primary_grading.get("key"),
            "gradingValid": primary_grading.get("valid"),
            "gradingErrors": list(primary_grading.get("errors") or []),
            "gradedPicks": primary_grading.get("gradedPicks"),
            "correctPicks": primary_grading.get("correctPicks"),
            "accuracy": primary_grading.get("accuracy"),
            "currentSlateGrading": current_slate_grading,
            "trailing14DayGrading": trailing_grading,
            "targetAccuracy": _normalise_accuracy(
                _number(
                    _first_not_none(
                        auto.get("targetDailyAccuracy"),
                        autonomy.get("targetDailyAccuracy"),
                    )
                )
            ),
            "invocations35m": invocations,
            "errors35m": errors,
            "errorRate35m": error_rate,
            "functionName": auto_invocation.get("functionName"),
            "serviceVersion": auto.get("version"),
        },
        "r7": {
            "statusReadOk": bool(r7.get("ok") is True),
            "reportedExperimentId": r7.get("experimentId") or latest.get("experimentId"),
            "trainingStatus": training_status,
            "processedThroughSlateDate": continuity.get("processedThroughSlateDate"),
            "processedSlateCount": len(processed_dates) if isinstance(processed_dates, list) else 0,
            "finalizedSlateCount": len(finalized_dates) if isinstance(finalized_dates, list) else 0,
            "blockedSlateDate": continuity.get("blockedSlateDate"),
            "continuityBlocker": continuity.get("blocker"),
            "acceptedRowCount": _integer(latest.get("acceptedRowCount")) or 0,
            "rejectedRowCount": _integer(latest.get("rejectedRowCount")) or 0,
            "trainCount": counts["train"],
            "validationCount": counts["validation"],
            "prospectiveTestCount": counts["prospectiveTest"],
            "trainTarget": _integer(_nested(milestones, "targets.train")) or 300,
            "validationTarget": _integer(_nested(milestones, "targets.validation")) or 100,
            "prospectiveTestTarget": _integer(_nested(milestones, "targets.prospectiveTest")) or 100,
            "totalTarget": _integer(_nested(milestones, "targets.totalClean")) or 500,
            "modelTrained": model_trained,
            "candidateArtifactId": candidate.get("artifactDigest") or latest.get("artifactDigest"),
            "championArtifactId": champion.get("artifactDigest") or champion.get("modelId"),
            "validationAccuracy": _normalise_accuracy(_metric(validation, ("accuracy", "selectedAccuracy", "overallAccuracy"))),
            "validationBrier": _metric(validation, ("brierScore", "brier", "selectedBrierScore")),
            "validationEce": _metric(validation, ("ece", "expectedCalibrationError", "calibrationError")),
            "prospectiveAccuracy": _normalise_accuracy(_metric(prospective, ("accuracy", "selectedAccuracy", "overallAccuracy"))),
            "prospectiveBrier": _metric(prospective, ("brierScore", "brier", "selectedBrierScore")),
            "promotionDecision": promotion_decision,
            "promotionGatePassed": bool(gate_passed),
            "liveInferenceAuthority": live_authority,
            "productionAuthorityChanged": production_changed,
            "selectionCapturedCount": _integer(selection_capture.get("capturedCount")) or 0,
            "selectionSelectedCount": _integer(selection_capture.get("selectedCount")) or 0,
            "workflowRun": dict(continuity_run),
            "functionName": r7_invocation.get("functionName"),
        },
        "blockers": sorted(set(item for item in blockers if item)),
    }

    reported_experiment = state["r7"].get("reportedExperimentId")
    if reported_experiment and reported_experiment != R7_EXPERIMENT_ID:
        state["blockers"].append(
            f"R7_EXPERIMENT_ID_MISMATCH:{reported_experiment}"
        )
    active_model = str(state["mlb"].get("activeModelId") or "")
    if "v15.10" in active_model.lower():
        state["blockers"].append("UNSAFE_RETIRED_V15_10_AUTHORITY_LEAK")
    state["blockers"] = sorted(set(state["blockers"]))
    return state


def _issue_comments() -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for page in range(1, 51):
        try:
            result = _run(
                [
                    "gh",
                    "api",
                    f"repos/{REPO}/issues/{ISSUE_NUMBER}/comments?per_page=100&page={page}",
                ],
                timeout=60,
            )
            rows = _json_loads(result.stdout or "[]", []) or []
        except Exception:
            break
        if not isinstance(rows, list):
            break
        comments.extend(row for row in rows if isinstance(row, dict))
        if len(rows) < 100:
            break
    return comments


def _decode_comment_state(body: str) -> Optional[dict[str, Any]]:
    pattern = re.compile(
        rf"<!--\s*{re.escape(STATE_MARKER)}:([A-Za-z0-9_\-+/=]+)\s*-->"
    )
    match = pattern.search(body)
    if not match:
        return None
    try:
        raw = base64.b64decode(match.group(1).encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _latest_visible_pulse(
    comments: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    rows = list(comments) if comments is not None else _issue_comments()
    for comment in reversed(rows):
        state = _decode_comment_state(str(comment.get("body") or ""))
        if state is None:
            continue
        return {
            "state": state,
            "commentId": comment.get("id"),
            "createdAtUtc": comment.get("created_at"),
            "updatedAtUtc": comment.get("updated_at"),
            "url": comment.get("html_url"),
        }
    return None


def _previous_state(
    comments: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    pulse = _latest_visible_pulse(comments)
    return pulse.get("state") if pulse else None


def _reporting_continuity(
    previous_pulse: Optional[Mapping[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    observed_now = now or datetime.now(timezone.utc)
    if observed_now.tzinfo is None:
        observed_now = observed_now.replace(tzinfo=timezone.utc)
    created_at = previous_pulse.get("createdAtUtc") if previous_pulse else None
    age_minutes: Optional[float] = None
    if created_at:
        try:
            parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            age_minutes = max(
                0.0,
                round((observed_now - parsed).total_seconds() / 60.0, 3),
            )
        except Exception:
            age_minutes = None
    return {
        "previousVisiblePulseFound": previous_pulse is not None,
        "previousVisiblePulseAtUtc": created_at,
        "previousVisiblePulseUrl": previous_pulse.get("url") if previous_pulse else None,
        "previousPulseAgeMinutes": age_minutes,
        "targetCadenceMinutes": PULSE_TARGET_CADENCE_MINUTES,
        "cadenceGraceMinutes": PULSE_CADENCE_GRACE_MINUTES,
        "staleAfterMinutes": PULSE_STALE_AFTER_MINUTES,
        "cadenceBreach": (
            age_minutes
            > PULSE_TARGET_CADENCE_MINUTES + PULSE_CADENCE_GRACE_MINUTES
            if age_minutes is not None
            else None
        ),
    }


def _path_value(state: Optional[Mapping[str, Any]], path: str) -> Any:
    if state is None:
        return None
    return _nested(state, path)


def _numeric_delta(state: Mapping[str, Any], previous: Optional[Mapping[str, Any]], path: str) -> Optional[float]:
    now = _number(_path_value(state, path))
    before = _number(_path_value(previous, path))
    if now is None or before is None:
        return None
    return round(now - before, 6)


def _grading_delta(
    state: Mapping[str, Any],
    previous: Optional[Mapping[str, Any]],
    metric: str,
) -> Optional[float]:
    """Compare grading values only when both pulses describe one cohort."""
    if previous is None:
        return None
    current_key = _path_value(state, "mlbAuto.gradingCohortKey")
    previous_key = _path_value(previous, "mlbAuto.gradingCohortKey")
    if not current_key or current_key != previous_key:
        return None
    if _path_value(state, "mlbAuto.gradingValid") is not True:
        return None
    if _path_value(previous, "mlbAuto.gradingValid") is not True:
        return None
    return _numeric_delta(state, previous, f"mlbAuto.{metric}")


def _date_delta(state: Mapping[str, Any], previous: Optional[Mapping[str, Any]], path: str) -> Optional[int]:
    current = _path_value(state, path)
    before = _path_value(previous, path)
    try:
        if not current or not before:
            return None
        return (date.fromisoformat(str(current)) - date.fromisoformat(str(before))).days
    except Exception:
        return None


def _fmt_int(value: Any) -> str:
    number = _integer(value)
    return "n/a" if number is None else f"{number:,}"


def _fmt_num(value: Any, digits: int = 3) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def _fmt_pct(value: Any, digits: int = 1) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.{digits}f}%"


def _fmt_delta(value: Optional[float], *, percent: bool = False, days: bool = False) -> str:
    if value is None:
        return "baseline"
    if days:
        return f"{value:+.0f} day" + ("s" if abs(value) != 1 else "")
    if percent:
        return f"{value * 100:+.1f} pp"
    if abs(value - round(value)) < 1e-9:
        return f"{int(value):+,}"
    return f"{value:+.3f}".rstrip("0").rstrip(".")


def _arrow(delta: Optional[float], *, lower_is_better: bool = False) -> str:
    if delta is None:
        return "⚪"
    effective = -delta if lower_is_better else delta
    if effective > 0:
        return "🟢 ↑"
    if effective < 0:
        return "🔴 ↓"
    return "🟡 →"


def _progress(current: int, target: int) -> str:
    pct = min(max(current / target, 0.0), 1.0) if target > 0 else 0.0
    return f"{current:,}/{target:,} ({pct:.1%})"


def _overall_direction(state: Mapping[str, Any], previous: Optional[Mapping[str, Any]]) -> tuple[str, int, int]:
    positive = 0
    negative = 0
    for path in (
        "r7.acceptedRowCount",
        "r7.trainCount",
        "r7.validationCount",
        "r7.prospectiveTestCount",
        "r7.processedSlateCount",
        "r7.finalizedSlateCount",
        "r7.selectionCapturedCount",
        "mlbAuto.pickCount",
    ):
        delta = _numeric_delta(state, previous, path)
        if delta is not None and delta > 0:
            positive += 1
        elif delta is not None and delta < 0:
            negative += 1
    for metric in ("gradedPicks", "correctPicks"):
        delta = _grading_delta(state, previous, metric)
        if delta is not None and delta > 0:
            positive += 1
        elif delta is not None and delta < 0:
            negative += 1
    date_move = _date_delta(state, previous, "r7.processedThroughSlateDate")
    if date_move is not None and date_move > 0:
        positive += 1
    elif date_move is not None and date_move < 0:
        negative += 1
    current_trained = bool(_path_value(state, "r7.modelTrained"))
    previous_trained = bool(_path_value(previous, "r7.modelTrained")) if previous else False
    if current_trained and not previous_trained:
        positive += 1
    current_qualified = bool(_path_value(state, "mlb.qualifiedChampionPresent"))
    previous_qualified = bool(_path_value(previous, "mlb.qualifiedChampionPresent")) if previous else False
    if current_qualified and not previous_qualified:
        positive += 2
    error_delta = _numeric_delta(state, previous, "mlbAuto.errorRate35m")
    if error_delta is not None and error_delta < 0:
        positive += 1
    elif error_delta is not None and error_delta > 0:
        negative += 1
    if previous is None:
        return "⚪ BASELINE ESTABLISHED", positive, negative
    if positive > 0 and negative == 0:
        return "🟢 MOVING FORWARD", positive, negative
    if positive > negative:
        return "🟢 NET FORWARD", positive, negative
    if negative > positive:
        return "🔴 REGRESSION DETECTED", positive, negative
    return "🟡 FLAT / BLOCKED", positive, negative


def _comment(state: Mapping[str, Any], previous: Optional[Mapping[str, Any]]) -> str:
    generated = datetime.fromisoformat(str(state["generatedAtUtc"]).replace("Z", "+00:00"))
    generated_et = generated.astimezone(ET)
    overall, positive, negative = _overall_direction(state, previous)
    r7 = state["r7"]
    mlb = state["mlb"]
    auto = state["mlbAuto"]
    reporting = state.get("reporting") or {}

    processed_delta = _date_delta(state, previous, "r7.processedThroughSlateDate")
    accepted_delta = _numeric_delta(state, previous, "r7.acceptedRowCount")
    rejected_delta = _numeric_delta(state, previous, "r7.rejectedRowCount")
    train_delta = _numeric_delta(state, previous, "r7.trainCount")
    validation_delta = _numeric_delta(state, previous, "r7.validationCount")
    prospective_delta = _numeric_delta(state, previous, "r7.prospectiveTestCount")
    inv_delta = _numeric_delta(state, previous, "mlbAuto.invocations35m")
    err_delta = _numeric_delta(state, previous, "mlbAuto.errors35m")
    error_rate_delta = _numeric_delta(state, previous, "mlbAuto.errorRate35m")
    picks_delta = _numeric_delta(state, previous, "mlbAuto.pickCount")
    graded_delta = _grading_delta(state, previous, "gradedPicks")
    pred_delta = _numeric_delta(state, previous, "mlb.winnerPredictionCount")

    workflow = r7.get("workflowRun") or {}
    workflow_status = f"{workflow.get('status') or 'unknown'}"
    if workflow.get("conclusion"):
        workflow_status += f" / {workflow.get('conclusion')}"
    workflow_link = workflow.get("url")
    if workflow_link:
        workflow_status = f"[{workflow_status}]({workflow_link})"
    workflow_kind = workflow.get("workflowKind") or "unknown"

    model_id = mlb.get("activeModelId") or "none — fail closed"
    authority_safe = bool(
        mlb.get("retiredAuthoritySuppressed")
        and not mlb.get("retiredV15_10Eligible")
        and "v15.10" not in str(model_id).lower()
    )
    authority_icon = "🟢" if authority_safe else "🔴"
    error_rate = auto.get("errorRate35m")
    current_grading = auto.get("currentSlateGrading") or {}
    trailing_grading = auto.get("trailing14DayGrading") or {}

    lines = [
        f"## MLB production pulse — {generated_et.strftime('%Y-%m-%d %I:%M %p ET')}",
        "",
        f"**Overall:** {overall}  ·  positive movements **{positive}**  ·  regressions **{negative}**",
        "",
        f"**Reporting cadence:** previous visible pulse `{reporting.get('previousVisiblePulseAtUtc') or 'none'}` · gap **{_fmt_num(reporting.get('previousPulseAgeMinutes'), 1)} minutes** · target **{_fmt_int(reporting.get('targetCadenceMinutes'))} minutes** (+{_fmt_int(reporting.get('cadenceGraceMinutes'))} grace) · fallback threshold **{_fmt_int(reporting.get('staleAfterMinutes'))} minutes** · breach **{reporting.get('cadenceBreach')}**.",
        "",
        "### R7 prospective experiment",
        "",
        "| Metric | Now | Δ30m | Direction |",
        "|---|---:|---:|:---:|",
        f"| Processed through slate | {r7.get('processedThroughSlateDate') or 'n/a'} | {_fmt_delta(processed_delta, days=True)} | {_arrow(processed_delta)} |",
        f"| Accepted rows | {_fmt_int(r7.get('acceptedRowCount'))} | {_fmt_delta(accepted_delta)} | {_arrow(accepted_delta)} |",
        f"| Rejected rows | {_fmt_int(r7.get('rejectedRowCount'))} | {_fmt_delta(rejected_delta)} | {_arrow(rejected_delta, lower_is_better=True)} |",
        f"| Train partition | {_progress(int(r7.get('trainCount') or 0), int(r7.get('trainTarget') or 300))} | {_fmt_delta(train_delta)} | {_arrow(train_delta)} |",
        f"| Validation partition | {_progress(int(r7.get('validationCount') or 0), int(r7.get('validationTarget') or 100))} | {_fmt_delta(validation_delta)} | {_arrow(validation_delta)} |",
        f"| Prospective-test partition | {_progress(int(r7.get('prospectiveTestCount') or 0), int(r7.get('prospectiveTestTarget') or 100))} | {_fmt_delta(prospective_delta)} | {_arrow(prospective_delta)} |",
        f"| Processed / finalized slates | {_fmt_int(r7.get('processedSlateCount'))} / {_fmt_int(r7.get('finalizedSlateCount'))} | — | — |",
        f"| Selection ledger captured / selected | {_fmt_int(r7.get('selectionCapturedCount'))} / {_fmt_int(r7.get('selectionSelectedCount'))} | — | — |",
        "",
        f"**Training state:** `{r7.get('trainingStatus') or 'unknown'}` · model trained **{r7.get('modelTrained')}** · candidate `{r7.get('candidateArtifactId') or 'none'}` · promotion `{r7.get('promotionDecision') or 'not evaluated'}` · gate passed **{r7.get('promotionGatePassed')}**.",
        f"**Out-of-sample metrics:** validation accuracy {_fmt_pct(r7.get('validationAccuracy'))}, Brier {_fmt_num(r7.get('validationBrier'))}, ECE {_fmt_num(r7.get('validationEce'))}; prospective accuracy {_fmt_pct(r7.get('prospectiveAccuracy'))}, Brier {_fmt_num(r7.get('prospectiveBrier'))}.",
        f"**R7 recovery workflow:** {workflow_status} · source `{workflow_kind}` · blocked slate `{r7.get('blockedSlateDate') or 'none'}` · continuity blocker `{r7.get('continuityBlocker') or 'none'}`.",
        "",
        "### MLB AUTO",
        "",
        "| Metric | Now | Δ30m | Direction |",
        "|---|---:|---:|:---:|",
        f"| Lambda invocations, last 35m | {_fmt_int(auto.get('invocations35m'))} | {_fmt_delta(inv_delta)} | {_arrow(inv_delta)} |",
        f"| Lambda errors, last 35m | {_fmt_int(auto.get('errors35m'))} | {_fmt_delta(err_delta)} | {_arrow(err_delta, lower_is_better=True)} |",
        f"| Error rate, last 35m | {_fmt_pct(error_rate)} | {_fmt_delta(error_rate_delta, percent=True)} | {_arrow(error_rate_delta, lower_is_better=True)} |",
        f"| Scheduled games / published picks | {_fmt_int(auto.get('scheduledGames'))} / {_fmt_int(auto.get('pickCount'))} | {_fmt_delta(picks_delta)} picks | {_arrow(picks_delta)} |",
        f"| Bedrock / R7 / unknown authority picks | {_fmt_int(auto.get('bedrockPickCount'))} / {_fmt_int(auto.get('r7AuthorityPickCount'))} / {_fmt_int(auto.get('unknownAuthorityPickCount'))} | — | — |",
        f"| Current-slate graded / correct / accuracy | {_fmt_int(current_grading.get('gradedPicks'))} / {_fmt_int(current_grading.get('correctPicks'))} / {_fmt_pct(current_grading.get('accuracy'))} | — | {'🟢 valid' if current_grading.get('valid') is True else '🔴 invalid' if current_grading.get('valid') is False else '⚪ unavailable'} |",
        f"| Trailing-14-day graded / correct / accuracy | {_fmt_int(trailing_grading.get('gradedPicks'))} / {_fmt_int(trailing_grading.get('correctPicks'))} / {_fmt_pct(trailing_grading.get('accuracy'))} | — | {'🟢 valid' if trailing_grading.get('valid') is True else '🔴 invalid' if trailing_grading.get('valid') is False else '⚪ unavailable'} |",
        f"| Primary grading cohort | `{auto.get('gradingCohort') or 'unavailable'}` | {_fmt_delta(graded_delta)} graded | {_arrow(graded_delta)} |",
        "",
        f"**Slate:** `{auto.get('slateDateEt') or 'n/a'}` · scheduled games **{_fmt_int(auto.get('scheduledGames'))}** · card published **{auto.get('cardPublished')}** · card authority `{auto.get('cardDecisionAuthority') or 'not exposed'}` · target accuracy **{_fmt_pct(auto.get('targetAccuracy'))}**.",
        "",
        "### MLB production authority",
        "",
        "| Metric | Now | Δ30m |",
        "|---|---:|---:|",
        f"| Authority status | `{mlb.get('authorityStatus') or 'unknown'}` | — |",
        f"| Active model | `{model_id}` | — |",
        f"| Winner predictions | {_fmt_int(mlb.get('winnerPredictionCount'))} | {_fmt_delta(pred_delta)} |",
        f"| Qualified R7 champion | {mlb.get('qualifiedChampionPresent')} | — |",
        f"| Publication closed | {mlb.get('publicationClosed')} | — |",
        f"| Retired V15.10 suppressed | {authority_icon} {mlb.get('retiredAuthoritySuppressed')} | — |",
        "",
    ]

    blockers = state.get("blockers") or []
    if blockers:
        lines.append("**Current blockers:** " + "; ".join(f"`{item}`" for item in blockers[:12]))
    else:
        lines.append("**Current blockers:** none reported by the read-only evidence paths.")

    pulse_url = f"https://github.com/{REPO}/actions/runs/{RUN_ID}"
    if RUN_ID:
        lines.extend(["", f"Evidence run: [GitHub Actions {RUN_ID}, attempt {RUN_ATTEMPT or '1'}]({pulse_url})."])

    encoded = base64.b64encode(
        json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).decode("ascii")
    lines.extend(
        [
            "",
            "_Read/proof operations only. No prediction, lock, label, partition, model, champion, or promotion state was mutated by this pulse._",
            f"<!-- {STATE_MARKER}:{encoded} -->",
        ]
    )
    return "\n".join(lines)


def _post_comment(body: str) -> None:
    with tempfile.TemporaryDirectory(prefix="mlb-pulse-comment-") as tmp:
        input_path = Path(tmp) / "comment.json"
        input_path.write_text(json.dumps({"body": body}), encoding="utf-8")
        _run(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{REPO}/issues/{ISSUE_NUMBER}/comments",
                "--input",
                str(input_path),
            ],
            timeout=60,
        )


def main() -> int:
    discovery_errors: list[str] = []
    trainer_fn, error = _resolve_function(ROOT_STACK, "MLBMLTrainingFunction")
    if error:
        discovery_errors.append(f"R7_FUNCTION_DISCOVERY:{error}")
    read_fn, error = _resolve_function(ROOT_STACK, "MLBV3ReadFunction")
    if error:
        discovery_errors.append(f"MLB_READ_FUNCTION_DISCOVERY:{error}")
    auto_fn, error = _resolve_function(AUTO_STACK, "MLBAutoLLMFunction")
    if error:
        discovery_errors.append(f"MLB_AUTO_FUNCTION_DISCOVERY:{error}")

    # The only trainer mode used here is the canonical read-only status mode.
    r7_invocation = _invoke(trainer_fn, {"sport": "mlb", "mode": "status"})
    model_invocation = _invoke(
        read_fn,
        {
            "version": "2.0",
            "rawPath": "/v1/mlb/model/version",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {},
        },
    )
    today_invocation = _invoke(
        read_fn,
        {
            "version": "2.0",
            "rawPath": "/v1/mlb/today",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {},
        },
    )
    auto_invocation = _invoke(
        auto_fn,
        {
            "version": "2.0",
            "rawPath": "/v1/mlb-auto-llm/status",
            "requestContext": {"http": {"method": "GET"}},
            "queryStringParameters": {},
        },
    )

    state = _extract_state(
        r7_invocation=r7_invocation,
        model_invocation=model_invocation,
        today_invocation=today_invocation,
        auto_invocation=auto_invocation,
        auto_invocations_35m=_cloudwatch_sum(auto_fn, "Invocations"),
        auto_errors_35m=_cloudwatch_sum(auto_fn, "Errors"),
        continuity_run=_latest_continuity_run(),
        discovery_errors=discovery_errors,
    )
    comments = _issue_comments()
    previous_pulse = _latest_visible_pulse(comments)
    previous = previous_pulse.get("state") if previous_pulse else None
    generated_at = datetime.fromisoformat(
        str(state["generatedAtUtc"]).replace("Z", "+00:00")
    )
    state["reporting"] = _reporting_continuity(
        previous_pulse,
        now=generated_at,
    )
    if state["reporting"].get("cadenceBreach") is True:
        age = _integer(state["reporting"].get("previousPulseAgeMinutes"))
        state["blockers"].append(
            f"PROGRESS_PULSE_CADENCE_BREACH:{age if age is not None else 'unknown'}m"
        )
        state["blockers"] = sorted(set(state["blockers"]))
    body = _comment(state, previous)
    _post_comment(body)
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
