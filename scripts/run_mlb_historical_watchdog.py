#!/usr/bin/env python3
"""Verify and gently advance the canonical MLB historical optimizer.

The configured historical end date is an authorized recovery ceiling. The durable
optimizer state may end earlier while it waits for the next MLB slate to become
provably settled. This verifier distinguishes that healthy bounded wait from a
stall, quota block, corrupt state, stale range exhaustion, or unproductive active
phase.
"""
from __future__ import annotations

import argparse
import copy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Dict, Mapping

import boto3


VERSION = "MLB-HISTORICAL-WATCHDOG-v3-current-wait-contract"
WAITING_PHASE = "WAITING_FOR_SETTLED_HORIZON"
WAITING_CONTRACT_VERSION = (
    "MLB-HISTORICAL-STATE-INTEGRITY-v2-settled-horizon-ledger-aware"
)
LEGACY_WAITING_CONTRACT_VERSION = (
    "MLB-HISTORICAL-STATE-INTEGRITY-v1-settled-horizon-idempotent"
)
WAITING_CONTRACT_VERSIONS = frozenset(
    {WAITING_CONTRACT_VERSION, LEGACY_WAITING_CONTRACT_VERSION}
)
ACTIVE_PHASES = frozenset(
    {"BACKFILLING", "OPTIMIZING", "REMATERIALIZING_FEATURES"}
)
TERMINAL_PHASES = frozenset({"PROMOTED", "CANDIDATE_REJECTED"})
PROGRESS_FIELDS = (
    "networkRequestCount",
    "eligibleGameCount",
    "completeSlateCount",
    "optimizationRound",
)
VOLATILE_STATE_FIELDS = frozenset({"revision", "updatedAtUtc"})
STABLE_STACK_STATUSES = frozenset(
    {
        "CREATE_COMPLETE",
        "UPDATE_COMPLETE",
        "UPDATE_ROLLBACK_COMPLETE",
        "IMPORT_COMPLETE",
        "IMPORT_ROLLBACK_COMPLETE",
    }
)


def _date(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise ValueError(f"{name}_invalid:{value}") from exc


def canonical_end_date(template: Path) -> str:
    text = template.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^  HistoricalEndDate:\s*$.*?^    Default: ['\"]?([^'\"\s]+)['\"]?\s*$",
        text,
    )
    if not match:
        raise ValueError("canonical_historical_end_date_missing")
    return _date(match.group(1), "canonical_historical_end_date").isoformat()


def _material(state: Mapping[str, Any]) -> Dict[str, Any]:
    value = copy.deepcopy(dict(state))
    for key in VOLATILE_STATE_FIELDS:
        value.pop(key, None)
    return value


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cursor(state: Mapping[str, Any]) -> tuple[str, int]:
    return (
        str(state.get("currentDate") or ""),
        _int(state.get("currentSlotIndex")),
    )


def _progressed(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    return bool(
        any(
            _int(after.get(key)) > _int(before.get(key))
            for key in PROGRESS_FIELDS
        )
        or _cursor(after) != _cursor(before)
    )


def _progressed_since_published(
    after: Mapping[str, Any], published: Mapping[str, Any]
) -> bool:
    return bool(
        any(
            _int(after.get(key)) > _int(published.get(key))
            for key in PROGRESS_FIELDS
        )
        or _cursor(after) > _cursor(published)
    )


def validate_common_state(
    state: Mapping[str, Any], *, expected_ceiling: str
) -> Dict[str, Any]:
    phase = str(state.get("phase") or "")
    allowed = ACTIVE_PHASES | TERMINAL_PHASES | {WAITING_PHASE, "DATA_RANGE_EXHAUSTED"}
    if phase == "PAUSED_QUOTA":
        raise ValueError("historical_ingestion_blocked_by_quota")
    if phase not in allowed:
        raise ValueError(f"unexpected_historical_phase:{phase}")

    if state.get("featureRematerializationComplete") is not True:
        raise ValueError("feature_rematerialization_incomplete")
    rematerialized = _int(state.get("featureRematerializedSlateCount"))
    rematerialization_total = _int(
        state.get("featureRematerializationTotalSlateCount")
    )
    completed = _int(state.get("completeSlateCount"))
    if rematerialized != rematerialization_total:
        raise ValueError("feature_rematerialization_counts_disagree")
    if completed and (
        rematerialized != completed or rematerialization_total != completed
    ):
        raise ValueError("feature_rematerialization_does_not_cover_completed_slates")
    if state.get("featureRematerializationErrors"):
        raise ValueError("feature_rematerialization_errors_remain")
    if state.get("lastError"):
        raise ValueError(f"optimizer_persisted_error:{state.get('lastError')}")

    ceiling = _date(expected_ceiling, "configured_ceiling")
    authorized_through = _date(
        state.get("endDate"), "authorized_through_date"
    )
    if authorized_through > ceiling:
        raise ValueError("authorized_range_exceeds_configured_ceiling")
    if phase == "DATA_RANGE_EXHAUSTED" and authorized_through < ceiling:
        raise ValueError("data_range_exhausted_before_configured_ceiling")

    quota = (state.get("lastQuota") or {}).get("x-requests-remaining")
    if isinstance(quota, int) and quota <= 100:
        raise ValueError(
            f"historical_provider_quota_at_or_below_reserve:{quota}"
        )
    return {
        "phase": phase,
        "configuredCeilingDate": ceiling.isoformat(),
        "authorizedThroughDate": authorized_through.isoformat(),
        "quotaRemaining": quota,
        "completeSlateCount": completed,
        "featureRematerializedSlateCount": rematerialized,
        "featureRematerializationTotalSlateCount": rematerialization_total,
    }


def validate_waiting_state(
    state: Mapping[str, Any], *, expected_ceiling: str
) -> Dict[str, Any]:
    common = validate_common_state(
        state, expected_ceiling=expected_ceiling
    )
    if common["phase"] != WAITING_PHASE:
        raise ValueError("state_is_not_waiting_for_settled_horizon")
    wait = state.get("settledHorizonWait")
    if not isinstance(wait, Mapping):
        raise ValueError("settled_horizon_wait_proof_missing")
    wait_version = str(wait.get("version") or "")
    if wait_version not in WAITING_CONTRACT_VERSIONS:
        raise ValueError("settled_horizon_wait_version_mismatch")
    if wait.get("blockingError") is not False:
        raise ValueError("settled_horizon_wait_is_blocking")

    ceiling = _date(expected_ceiling, "configured_ceiling")
    configured = _date(
        wait.get("configuredCeilingDate"), "wait_ceiling"
    )
    authorized = _date(
        wait.get("authorizedThroughDate"), "wait_authorized_through"
    )
    settled = _date(wait.get("settledHorizonDate"), "settled_horizon")
    next_eligible = _date(
        wait.get("nextEligibleSlateDate"), "next_eligible_slate"
    )
    state_end = _date(state.get("endDate"), "state_end")
    current = _date(state.get("currentDate"), "current_date")
    retry = _date(
        state.get("rangeExtensionNextRetryDate"),
        "range_extension_next_retry",
    )

    if configured != ceiling:
        raise ValueError("wait_ceiling_does_not_match_deployment_ceiling")
    if authorized != state_end:
        raise ValueError(
            "wait_authorized_through_does_not_match_state_end"
        )
    if settled > authorized:
        raise ValueError("settled_horizon_exceeds_authorized_through")
    if next_eligible != authorized + timedelta(days=1):
        raise ValueError("next_eligible_slate_is_not_contiguous")
    if retry != next_eligible:
        raise ValueError(
            "retry_date_does_not_match_next_eligible_slate"
        )
    if current < next_eligible:
        raise ValueError("cursor_precedes_next_eligible_slate")
    if authorized >= ceiling:
        raise ValueError("waiting_phase_invalid_at_configured_ceiling")

    return {
        **common,
        "waitingHealthy": True,
        "waitContractVersion": wait_version,
        "settledHorizonDate": settled.isoformat(),
        "nextEligibleSlateDate": next_eligible.isoformat(),
        "blockingError": False,
        "eligibleGameCount": _int(state.get("eligibleGameCount")),
        "targetSettledGames": _int(state.get("targetSettledGames")),
        "remainingEvidenceGames": max(
            0,
            _int(state.get("targetSettledGames"))
            - _int(state.get("eligibleGameCount")),
        ),
    }


def validate_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    expected_ceiling: str,
    published: Mapping[str, Any] | None = None,
    lease_held: bool = False,
) -> Dict[str, Any]:
    common = validate_common_state(
        after, expected_ceiling=expected_ceiling
    )
    phase = common["phase"]
    if phase == WAITING_PHASE:
        return {
            **validate_waiting_state(
                after, expected_ceiling=expected_ceiling
            ),
            "advancedInRun": _progressed(before, after),
            "advancedSincePublished": _progressed_since_published(
                after, published or {}
            ),
            "leaseHeld": lease_held,
        }

    advanced_in_run = _progressed(before, after)
    advanced_since_published = _progressed_since_published(
        after, published or {}
    )
    if phase in ACTIVE_PHASES and not (
        advanced_in_run or advanced_since_published or lease_held
    ):
        raise ValueError(
            "active_optimizer_did_not_make_substantive_progress"
        )
    return {
        **common,
        "waitingHealthy": False,
        "advancedInRun": advanced_in_run,
        "advancedSincePublished": advanced_since_published,
        "leaseHeld": lease_held,
    }


def validate_repeated_wait_is_idempotent(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    expected_ceiling: str,
) -> Dict[str, Any]:
    first_proof = validate_waiting_state(
        first, expected_ceiling=expected_ceiling
    )
    second_proof = validate_waiting_state(
        second, expected_ceiling=expected_ceiling
    )
    if _material(first) != _material(second):
        raise ValueError("repeated_wait_changed_material_state")
    if _int(second.get("revision")) != _int(first.get("revision")):
        raise ValueError("repeated_wait_created_duplicate_revision")
    return {
        "idempotent": True,
        "firstRevision": _int(first.get("revision")),
        "secondRevision": _int(second.get("revision")),
        "first": first_proof,
        "second": second_proof,
    }


def _decode_lambda_response(
    response: Mapping[str, Any]
) -> Dict[str, Any]:
    if response.get("FunctionError"):
        raise RuntimeError(
            f"lambda_function_error:{response.get('FunctionError')}"
        )
    stream = response.get("Payload")
    body = stream.read() if hasattr(stream, "read") else stream
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    value = json.loads(body or "{}")
    if (
        isinstance(value, Mapping)
        and "body" in value
        and "statusCode" in value
    ):
        value = json.loads(value.get("body") or "{}")
    if not isinstance(value, dict):
        raise RuntimeError("lambda_payload_is_not_an_object")
    return value


def _invoke(
    lam: Any, function_name: str, payload: Mapping[str, Any]
) -> Dict[str, Any]:
    response = lam.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            dict(payload), separators=(",", ":")
        ).encode("utf-8"),
    )
    return _decode_lambda_response(response)


def _status(
    lam: Any, function_name: str, run_name: str
) -> Dict[str, Any]:
    for attempt in range(1, 25):
        value = _invoke(
            lam,
            function_name,
            {"mode": "status", "run": f"{run_name}_{attempt}"},
        )
        if value.get("status") == "LEASE_HELD" and not isinstance(
            value.get("state"), Mapping
        ):
            time.sleep(15)
            continue
        if value.get("ok") is True and isinstance(
            value.get("state"), Mapping
        ):
            return value
        raise RuntimeError(f"historical_status_unhealthy:{value}")
    raise RuntimeError("historical_status_remained_lease_held")


def _stack_outputs(stack: Mapping[str, Any]) -> Dict[str, str]:
    return {
        str(row.get("OutputKey")): str(row.get("OutputValue"))
        for row in stack.get("Outputs") or []
        if row.get("OutputKey") and row.get("OutputValue")
    }


def run(
    *,
    region: str,
    stack_name: str,
    expected_handler: str,
    expected_max_credits: str,
    template: Path,
    published_status: Path,
) -> Dict[str, Any]:
    expected_ceiling = canonical_end_date(template)
    cf = boto3.client("cloudformation", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    stack = (
        cf.describe_stacks(StackName=stack_name).get("Stacks") or []
    )[0]
    stack_status = str(stack.get("StackStatus") or "")
    if stack_status not in STABLE_STACK_STATUSES:
        raise RuntimeError(
            f"historical_stack_not_stable:{stack_status}"
        )
    function_name = _stack_outputs(stack).get(
        "HistoricalOptimizerFunctionName"
    )
    if not function_name:
        raise RuntimeError(
            "historical_optimizer_function_output_missing"
        )

    config = lam.get_function_configuration(
        FunctionName=function_name
    )
    environment = (
        (config.get("Environment") or {}).get("Variables") or {}
    )
    runtime_checks = {
        "handler": config.get("Handler") == expected_handler,
        "configuredCeiling": environment.get(
            "MLB_HISTORICAL_END_DATE"
        )
        == expected_ceiling,
        "maxCredits": environment.get(
            "MLB_HISTORICAL_MAX_CREDITS"
        )
        == str(expected_max_credits),
        "rangeExtensionAuthorized": environment.get(
            "MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED"
        )
        == "true",
    }
    if not all(runtime_checks.values()):
        raise RuntimeError(
            f"historical_runtime_configuration_mismatch:{runtime_checks}"
        )

    before_response = _status(
        lam, function_name, "watchdog_before"
    )
    before = dict(before_response["state"])
    resume = _invoke(
        lam,
        function_name,
        {
            "mode": "orchestrate",
            "run": "github_watchdog_resume_canonical_v7",
        },
    )
    if resume.get("ok") is not True:
        raise RuntimeError(
            f"historical_optimizer_resume_failed:{resume}"
        )
    after_response = _status(
        lam, function_name, "watchdog_after"
    )
    after = dict(after_response["state"])
    published = (
        json.loads(published_status.read_text(encoding="utf-8"))
        if published_status.exists()
        else {}
    )
    transition = validate_transition(
        before,
        after,
        expected_ceiling=expected_ceiling,
        published=published,
        lease_held=resume.get("status") == "LEASE_HELD",
    )

    repeated_wait = None
    final_state = after
    if str(after.get("phase") or "") == WAITING_PHASE:
        second_resume = _invoke(
            lam,
            function_name,
            {
                "mode": "orchestrate",
                "run": "github_watchdog_wait_idempotency_v3",
            },
        )
        if second_resume.get("ok") is not True:
            raise RuntimeError(
                "historical_wait_idempotency_resume_failed:"
                f"{second_resume}"
            )
        second_response = _status(
            lam,
            function_name,
            "watchdog_wait_after_second",
        )
        second = dict(second_response["state"])
        if str(second.get("phase") or "") == WAITING_PHASE:
            repeated_wait = validate_repeated_wait_is_idempotent(
                after,
                second,
                expected_ceiling=expected_ceiling,
            )
        else:
            repeated_wait = {
                "idempotent": None,
                "horizonAdvancedDuringVerification": True,
                "transition": validate_transition(
                    after,
                    second,
                    expected_ceiling=expected_ceiling,
                    published=published,
                    lease_held=(
                        second_resume.get("status") == "LEASE_HELD"
                    ),
                ),
            }
        final_state = second

    return {
        "proofType": "MLB_HISTORICAL_WATCHDOG_PROGRESS",
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "ok": True,
        "stackName": stack_name,
        "stackStatus": stack_status,
        "functionName": function_name,
        "runtimeConfiguration": {
            "handler": config.get("Handler"),
            "deployGitSha": environment.get("INQSI_DEPLOY_GIT_SHA"),
            "configuredCeilingDate": expected_ceiling,
            "maximumCredits": environment.get(
                "MLB_HISTORICAL_MAX_CREDITS"
            ),
            "rangeExtensionAuthorized": environment.get(
                "MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED"
            ),
            "checks": runtime_checks,
        },
        "before": {
            key: before.get(key)
            for key in (
                "phase",
                "endDate",
                "currentDate",
                "currentSlotIndex",
                *PROGRESS_FIELDS,
                "revision",
                "lastError",
            )
        },
        "after": {
            key: final_state.get(key)
            for key in (
                "phase",
                "endDate",
                "currentDate",
                "currentSlotIndex",
                *PROGRESS_FIELDS,
                "revision",
                "lastError",
            )
        },
        "transition": transition,
        "repeatedWaitProof": repeated_wait,
        "configuredCeilingIsNotSettledAuthority": True,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
        "blockers": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--expected-handler", required=True)
    parser.add_argument("--expected-max-credits", required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument(
        "--published-status", type=Path, required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(
            region=args.region,
            stack_name=args.stack_name,
            expected_handler=args.expected_handler,
            expected_max_credits=args.expected_max_credits,
            template=args.template,
            published_status=args.published_status,
        )
    except Exception as exc:
        result = {
            "proofType": "MLB_HISTORICAL_WATCHDOG_PROGRESS",
            "version": VERSION,
            "createdAtUtc": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
            "automaticWagerAllowed": False,
            "productionAuthorityChanged": False,
            "blockers": [str(exc)],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
