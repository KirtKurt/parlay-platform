#!/usr/bin/env python3
"""Build the canonical, evidence-honest V7 historical status document."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


VERSION = "MLB-HISTORICAL-LIVE-STATUS-v2-evidence-honest"
WAITING_PHASE = "WAITING_FOR_SETTLED_HORIZON"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _nested(value: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _first_number(
    value: Mapping[str, Any], paths: Sequence[Sequence[str]]
) -> float | int | None:
    for path in paths:
        number = _number(_nested(value, path))
        if number is not None:
            return number
    return None


def _absolute_metrics(latest: Mapping[str, Any]) -> dict[str, Any]:
    walk_forward_brier = _first_number(
        latest,
        (
            ("walkForwardMetrics", "brierScore"),
            ("metrics", "walkForward", "brierScore"),
            ("candidate", "walkForwardMetrics", "brierScore"),
            ("promotionGate", "walkForwardBrierScore"),
        ),
    )
    walk_forward_log_loss = _first_number(
        latest,
        (
            ("walkForwardMetrics", "logLoss"),
            ("metrics", "walkForward", "logLoss"),
            ("candidate", "walkForwardMetrics", "logLoss"),
            ("promotionGate", "walkForwardLogLoss"),
        ),
    )
    holdout_brier = _first_number(
        latest,
        (
            ("untouchedHoldoutMetrics", "brierScore"),
            ("metrics", "untouchedHoldout", "brierScore"),
            ("candidate", "untouchedHoldoutMetrics", "brierScore"),
            ("promotionGate", "untouchedHoldoutBrierScore"),
        ),
    )
    holdout_log_loss = _first_number(
        latest,
        (
            ("untouchedHoldoutMetrics", "logLoss"),
            ("metrics", "untouchedHoldout", "logLoss"),
            ("candidate", "untouchedHoldoutMetrics", "logLoss"),
            ("promotionGate", "untouchedHoldoutLogLoss"),
        ),
    )
    available = all(
        value is not None
        for value in (
            walk_forward_brier,
            walk_forward_log_loss,
            holdout_brier,
            holdout_log_loss,
        )
    )
    return {
        "walkForwardBrierScore": walk_forward_brier,
        "walkForwardLogLoss": walk_forward_log_loss,
        "untouchedHoldoutBrierScore": holdout_brier,
        "untouchedHoldoutLogLoss": holdout_log_loss,
        "absoluteScoresPublished": available,
        "unavailableReason": (
            None
            if available
            else "latest canonical status/experiment pointer does not expose absolute calibration scores"
        ),
    }


def _runtime_blockers(state: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if state.get("lastError"):
        blockers.append(f"optimizer_persisted_error:{state.get('lastError')}")
    errors = state.get("featureRematerializationErrors") or []
    if errors:
        blockers.append("feature_rematerialization_errors_present")
    complete = _int(state.get("completeSlateCount"))
    rematerialized = _int(state.get("featureRematerializedSlateCount"))
    rematerialization_total = _int(
        state.get("featureRematerializationTotalSlateCount")
    )
    if state.get("featureRematerializationComplete") is not True:
        blockers.append("feature_rematerialization_incomplete")
    if complete and (
        rematerialized != complete or rematerialization_total != complete
    ):
        blockers.append("feature_rematerialization_does_not_cover_completed_slates")
    if str(state.get("phase") or "") == "PAUSED_QUOTA":
        blockers.append("historical_provider_quota_paused")
    if str(state.get("phase") or "") == WAITING_PHASE:
        wait = state.get("settledHorizonWait")
        if not isinstance(wait, Mapping):
            blockers.append("settled_horizon_wait_proof_missing")
        elif wait.get("blockingError") is not False:
            blockers.append("settled_horizon_wait_is_blocking")
    return blockers


def build_summary(
    status_response: Mapping[str, Any],
    *,
    function_configuration: Mapping[str, Any] | None = None,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    if status_response.get("ok") is not True:
        raise ValueError("optimizer status is not OK")
    state = status_response.get("state")
    if not isinstance(state, Mapping):
        raise ValueError("optimizer status omitted state")

    required = (
        "phase",
        "currentDate",
        "currentSlotIndex",
        "eligibleGameCount",
        "completeSlateCount",
        "optimizationRound",
        "targetSettledGames",
        "updatedAtUtc",
    )
    missing = [name for name in required if state.get(name) is None]
    if missing:
        raise ValueError(
            "optimizer status missing required fields:" + ",".join(missing)
        )

    checked = (checked_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source_updated = _dt(state.get("updatedAtUtc"))
    source_age_seconds = (
        max(0.0, (checked - source_updated).total_seconds())
        if source_updated
        else None
    )

    eligible = _int(state.get("eligibleGameCount"))
    target = _int(state.get("targetSettledGames"))
    complete = _int(state.get("completeSlateCount"))
    latest = state.get("latestExperiment") or {}
    if not isinstance(latest, Mapping):
        latest = {}
    gate = latest.get("promotionGate") or {}
    if not isinstance(gate, Mapping):
        gate = {}
    latest_settled = _int(gate.get("settledGameCount"))
    training_games = _int(gate.get("trainingGameCount"))
    absolute = _absolute_metrics(latest)

    start = str(state.get("freshAuditStartDate") or "")
    completed = state.get("completedSlates") or []
    provisional = [
        row
        for row in completed
        if isinstance(row, Mapping)
        and start
        and str(row.get("slateDateEt") or "") >= start
    ]

    config = function_configuration or {}
    environment = (
        (config.get("Environment") or {}).get("Variables") or {}
        if isinstance(config, Mapping)
        else {}
    )
    wait = state.get("settledHorizonWait") or {}
    if not isinstance(wait, Mapping):
        wait = {}

    runtime_blockers = _runtime_blockers(state)
    waiting_healthy = bool(
        str(state.get("phase") or "") == WAITING_PHASE
        and not runtime_blockers
        and wait.get("blockingError") is False
    )
    next_ready = bool(
        state.get("nextOptimizationReady") is True
        or (target > 0 and eligible >= target)
    )
    if runtime_blockers:
        stalled_stage = "RUNTIME_BLOCKED"
    elif waiting_healthy and not next_ready:
        stalled_stage = "SETTLED_CORPUS_ACCUMULATION"
    elif next_ready:
        stalled_stage = "NEXT_OPTIMIZATION_READY"
    elif latest.get("status") == "CANDIDATE_REJECTED":
        stalled_stage = "MODEL_PROMOTION_GATE"
    else:
        stalled_stage = str(state.get("phase") or "UNKNOWN")

    overfit = gate.get("overfitChecks") or {}
    if not isinstance(overfit, Mapping):
        overfit = {}

    challenger_metrics = {
        "experimentId": latest.get("experimentId"),
        "candidateStatus": latest.get("status"),
        "trainingGameCount": training_games,
        "settledGameCount": latest_settled,
        "corpusCurrent": latest_settled == eligible,
        "walkForwardGameCount": _int(gate.get("walkForwardGameCount")),
        "walkForwardDayCount": _int(gate.get("walkForwardDayCount")),
        "walkForwardMeanDailyAccuracy": gate.get(
            "walkForwardMeanDailyAccuracy"
        ),
        "walkForwardMinimumDailyAccuracy": gate.get(
            "walkForwardMinimumDailyAccuracy"
        ),
        "untouchedHoldoutGameCount": _int(
            gate.get("untouchedHoldoutGameCount")
        ),
        "untouchedHoldoutDayCount": _int(
            gate.get("untouchedHoldoutDayCount")
        ),
        "untouchedHoldoutMeanDailyAccuracy": gate.get(
            "untouchedHoldoutMeanDailyAccuracy"
        ),
        "untouchedHoldoutMinimumDailyAccuracy": gate.get(
            "untouchedHoldoutMinimumDailyAccuracy"
        ),
        **absolute,
        "brierDeltaVsBaseline": overfit.get("brierDeltaVsBaseline"),
        "logLossDeltaVsBaseline": overfit.get("logLossDeltaVsBaseline"),
        "promotionPassed": gate.get("passed") is True,
        "promotionErrors": list(gate.get("errors") or []),
    }

    champion_validation = status_response.get("championValidation")
    champion_ok = bool(
        isinstance(champion_validation, Mapping)
        and champion_validation.get("ok") is True
    )

    return {
        "statusVersion": VERSION,
        "checkedAt": checked.isoformat(),
        "ok": not runtime_blockers,
        "phase": state.get("phase"),
        "stalledStage": stalled_stage,
        "waitingHealthy": waiting_healthy,
        "safeSettledHorizonDate": wait.get("settledHorizonDate"),
        "nextEligibleSlateDate": wait.get("nextEligibleSlateDate"),
        "authorizedThroughDate": state.get("endDate"),
        "configuredCeilingDate": environment.get(
            "MLB_HISTORICAL_END_DATE"
        ),
        "deploymentGitSha": environment.get("INQSI_DEPLOY_GIT_SHA"),
        "currentDate": state.get("currentDate"),
        "currentSlotIndex": state.get("currentSlotIndex"),
        "networkRequestCount": state.get("networkRequestCount"),
        "creditsConsumed": state.get("creditsConsumed"),
        "eligibleGameCount": eligible,
        "completeSlateCount": complete,
        "targetSettledGames": target,
        "gamesUntilNextOptimization": max(0, target - eligible),
        "nextOptimizationReady": next_ready,
        "optimizationRound": state.get("optimizationRound"),
        "optimizationCompletedAtUtc": state.get(
            "optimizationCompletedAtUtc"
        ),
        "currentTrainingGameCount": training_games,
        "latestExperiment": dict(latest),
        "latestChallengerMetrics": challenger_metrics,
        "latestAccuracy": {
            "settledGameCount": latest_settled,
            "walkForwardMeanDailyAccuracy": gate.get(
                "walkForwardMeanDailyAccuracy"
            ),
            "walkForwardMinimumDailyAccuracy": gate.get(
                "walkForwardMinimumDailyAccuracy"
            ),
            "untouchedHoldoutMeanDailyAccuracy": gate.get(
                "untouchedHoldoutMeanDailyAccuracy"
            ),
            "untouchedHoldoutMinimumDailyAccuracy": gate.get(
                "untouchedHoldoutMinimumDailyAccuracy"
            ),
            "walkForwardBrierScore": absolute[
                "walkForwardBrierScore"
            ],
            "walkForwardLogLoss": absolute["walkForwardLogLoss"],
            "untouchedHoldoutBrierScore": absolute[
                "untouchedHoldoutBrierScore"
            ],
            "untouchedHoldoutLogLoss": absolute[
                "untouchedHoldoutLogLoss"
            ],
            "brierDeltaVsBaseline": overfit.get(
                "brierDeltaVsBaseline"
            ),
            "logLossDeltaVsBaseline": overfit.get(
                "logLossDeltaVsBaseline"
            ),
            "absoluteCalibrationScoresPublished": absolute[
                "absoluteScoresPublished"
            ],
            "calibrationScoreUnavailableReason": absolute[
                "unavailableReason"
            ],
            "promotionPassed": gate.get("passed") is True,
        },
        "accuracyTargetReached": gate.get("passed") is True,
        "candidateStatus": latest.get("status"),
        "championStatus": (
            "ACTIVE_CHAMPION" if champion_ok else "NO_ACTIVE_CHAMPION"
        ),
        "runtimeBlockers": runtime_blockers,
        "modelPromotionBlockers": list(gate.get("errors") or []),
        "lastError": state.get("lastError"),
        "lastErrorAtUtc": state.get("lastErrorAtUtc"),
        "stateUpdatedAtUtc": state.get("updatedAtUtc"),
        "sourceStateAgeSeconds": source_age_seconds,
        "sourceStateStaleButHealthyWait": bool(
            waiting_healthy
            and source_age_seconds is not None
            and source_age_seconds > 3600
        ),
        "revision": state.get("revision"),
        "featureDatasetVersion": state.get("featureDatasetVersion"),
        "featureRematerializationComplete": state.get(
            "featureRematerializationComplete"
        ),
        "featureRematerializedSlateCount": state.get(
            "featureRematerializedSlateCount"
        ),
        "featureRematerializationTotalSlateCount": state.get(
            "featureRematerializationTotalSlateCount"
        ),
        "featureRematerializationErrors": state.get(
            "featureRematerializationErrors"
        ),
        "freshAuditExpansionRequired": state.get(
            "freshAuditExpansionRequired"
        ),
        "freshAuditCollectedDayCount": state.get(
            "freshAuditCollectedDayCount"
        ),
        "freshAuditCollectedGameCount": state.get(
            "freshAuditCollectedGameCount"
        ),
        "provisionalFreshAuditCollectedDayCount": len(provisional),
        "provisionalFreshAuditCollectedGameCount": sum(
            _int(row.get("eligibleGameCount")) for row in provisional
        ),
        "freshAuditStartDate": state.get("freshAuditStartDate"),
        "endDate": state.get("endDate"),
        "lastQuota": state.get("lastQuota"),
        "championValidation": champion_validation,
        "cutoverValidation": status_response.get("cutoverValidation"),
        "productionAuthority": status_response.get(
            "productionAuthority"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument(
        "--function-configuration", type=Path, required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    status = json.loads(args.status.read_text(encoding="utf-8"))
    function_configuration = json.loads(
        args.function_configuration.read_text(encoding="utf-8")
    )
    summary = build_summary(
        status,
        function_configuration=function_configuration,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
