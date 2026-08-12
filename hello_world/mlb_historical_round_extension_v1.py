"""Safely enforce strictly-later MLB historical untouched-audit rounds.

This patch never changes a candidate, promotion gate, historical evidence, or prior
untouched-audit assignment. It repairs migrated/retried non-promoted states so any
subsequent optimization must use dates strictly later than every previously
label-evaluated untouched-audit date. It also normalizes the canonical audit cadence
to the promotion policy minimum and preserves the bounded round-extension recovery
for terminal rejected states when the deployment ceiling was raised.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any, Dict, Mapping

VERSION = "MLB-HISTORICAL-ROUND-EXTENSION-v3-policy-minimum-audit-cadence"
MIN_CANONICAL_UNTOUCHED_AUDIT_GAMES = 200
_PENDING_AUDIT_PHASES = {
    "BACKFILLING",
    "PAUSED_QUOTA",
    "OPTIMIZING",
    "WAITING_FOR_SETTLED_HORIZON",
    "DATA_RANGE_EXHAUSTED",
}


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _latest_evaluated_date(state: Mapping[str, Any]) -> str | None:
    dates = []
    for window in state.get("evaluatedAuditWindows") or []:
        if not isinstance(window, Mapping):
            continue
        dates.extend(str(value) for value in window.get("dates") or [] if str(value))
    return max(dates) if dates else None


def _strictly_later_day(day: str) -> str | None:
    try:
        return (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    except (TypeError, ValueError):
        return None


def _policy_minimum_audit_games(handler: Any) -> int:
    runtime = getattr(handler, "policy_runtime", None)
    minimum = _integer(
        getattr(runtime, "MIN_UNTOUCHED_AUDIT_GAMES", None),
        MIN_CANONICAL_UNTOUCHED_AUDIT_GAMES,
    )
    if minimum < MIN_CANONICAL_UNTOUCHED_AUDIT_GAMES:
        raise RuntimeError("canonical untouched-audit policy cannot be below 200 games")
    return minimum


def _repair_persisted_audit_boundary(handler: Any, value: Dict[str, Any]) -> Dict[str, Any]:
    """Derive the fresh-audit requirement from immutable audit history.

    `freshAuditExpansionRequired` is orchestration state and can be absent/stale on
    migrated states. `evaluatedAuditWindows` is the durable evidence authority. If
    prior audit dates exist, every later optimization must be constrained to dates
    strictly after the latest one, even after retries, deployments, or migrations.
    """

    if value.get("champion") or value.get("productionCutover"):
        return value

    latest_evaluated = _latest_evaluated_date(value)
    next_audit_day = _strictly_later_day(latest_evaluated) if latest_evaluated else None
    if not next_audit_day:
        return value

    phase = str(value.get("phase") or "")
    if phase not in {"BACKFILLING", "PAUSED_QUOTA", "OPTIMIZING", "CANDIDATE_REJECTED"}:
        return value

    current_raw = str(value.get("currentDate") or "")
    configured_start = str(value.get("freshAuditStartDate") or "")
    required_start = max(next_audit_day, current_raw) if current_raw else next_audit_day

    needs_repair = (
        value.get("freshAuditExpansionRequired") is not True
        or not configured_start
        or configured_start <= latest_evaluated
    )
    if not needs_repair:
        return value

    repaired = copy.deepcopy(value)
    repaired["freshAuditExpansionRequired"] = True
    repaired["freshAuditStartDate"] = required_start
    repaired["freshAuditCollectedDayCount"] = 0
    repaired["freshAuditCollectedGameCount"] = 0

    eligible = _integer(repaired.get("eligibleGameCount"))
    prior_target = _integer(repaired.get("targetSettledGames"), eligible)
    increment = _integer(
        getattr(handler, "FRESH_AUDIT_INCREMENT_GAMES", None),
        MIN_CANONICAL_UNTOUCHED_AUDIT_GAMES,
    )
    if phase in {"BACKFILLING", "PAUSED_QUOTA", "OPTIMIZING"} and prior_target <= eligible:
        repaired["targetSettledGames"] = eligible + increment

    last_error = str(repaired.get("lastError") or "")
    if "untouched audit dates were reused after label evaluation" in last_error:
        repaired["lastError"] = None

    repaired["auditReuseRecovery"] = {
        "version": VERSION,
        "recoveredAtUtc": handler._now_iso(),
        "latestPreviouslyEvaluatedAuditDate": latest_evaluated,
        "freshAuditStartDate": required_start,
        "strictlyLaterAuditRequired": True,
        "priorFreshAuditExpansionRequired": value.get("freshAuditExpansionRequired") is True,
        "priorFreshAuditStartDate": value.get("freshAuditStartDate"),
        "priorTargetSettledGames": prior_target,
        "newTargetSettledGames": _integer(repaired.get("targetSettledGames"), prior_target),
    }
    return repaired


def _repair_overprovisioned_pending_audit_target(
    handler: Any,
    value: Dict[str, Any],
    configured_increment: int,
    policy_increment: int,
) -> Dict[str, Any]:
    """Rebase only the exact, untouched legacy target onto the policy floor.

    The repair is deliberately narrow. It applies only to a rejected candidate whose
    persisted target equals ``candidate settled games + the former configured
    increment``. Existing audit boundaries and every evaluated window are retained.
    Any collected audit evidence, active authority, non-exact target, or overlapping
    boundary makes the operation a no-op.
    """

    if configured_increment <= policy_increment:
        return value
    if value.get("champion") or value.get("productionCutover"):
        return value
    if str(value.get("phase") or "") not in _PENDING_AUDIT_PHASES:
        return value
    if value.get("freshAuditExpansionRequired") is not True:
        return value
    if _integer(value.get("freshAuditCollectedGameCount")) > 0:
        return value
    if _integer(value.get("freshAuditCollectedDayCount")) > 0:
        return value

    latest = value.get("latestExperiment") or {}
    if not isinstance(latest, Mapping):
        return value
    gate = latest.get("promotionGate") or {}
    if not isinstance(gate, Mapping):
        return value
    if latest.get("status") != "CANDIDATE_REJECTED" or gate.get("passed") is not False:
        return value

    settled_games = _integer(gate.get("settledGameCount"))
    if settled_games <= 0:
        return value
    prior_target = _integer(value.get("targetSettledGames"))
    expected_prior_target = settled_games + configured_increment
    new_target = settled_games + policy_increment
    if prior_target != expected_prior_target or new_target >= prior_target:
        return value

    latest_evaluated = _latest_evaluated_date(value)
    fresh_start = str(value.get("freshAuditStartDate") or "")
    if latest_evaluated and (not fresh_start or fresh_start <= latest_evaluated):
        return value

    repaired = copy.deepcopy(value)
    repaired["targetSettledGames"] = new_target
    repaired["canonicalAuditCadenceRepair"] = {
        "version": VERSION,
        "repairedAtUtc": handler._now_iso(),
        "policyMinimumUntouchedAuditGames": policy_increment,
        "previousConfiguredIncrementGames": configured_increment,
        "latestRejectedCandidateSettledGames": settled_games,
        "priorTargetSettledGames": prior_target,
        "newTargetSettledGames": new_target,
        "freshAuditStartDate": value.get("freshAuditStartDate"),
        "evaluatedAuditWindowsPreserved": True,
        "promotionGateWeakened": False,
    }
    return repaired


def install(handler: Any) -> None:
    if getattr(handler, "_INQSI_HISTORICAL_ROUND_EXTENSION_V1_INSTALLED", False):
        return

    policy_increment = _policy_minimum_audit_games(handler)
    configured_increment = _integer(
        getattr(handler, "FRESH_AUDIT_INCREMENT_GAMES", None),
        policy_increment,
    )
    if configured_increment < policy_increment:
        raise RuntimeError(
            "configured canonical untouched-audit increment is below the promotion policy"
        )

    # Normalize future rounds to the immutable policy floor. The migration wrapper
    # below separately handles the one persisted target created by the former 250-
    # game configuration, and only when its provenance is exact and untouched.
    handler.FRESH_AUDIT_INCREMENT_GAMES = policy_increment
    original = handler._migrate_state

    def patched(state: Mapping[str, Any]) -> Dict[str, Any]:
        value = _repair_persisted_audit_boundary(handler, original(state))
        value = _repair_overprovisioned_pending_audit_target(
            handler,
            value,
            configured_increment,
            policy_increment,
        )
        if str(value.get("phase") or "") != "CANDIDATE_REJECTED":
            return value

        round_number = _integer(value.get("optimizationRound"))
        maximum_rounds = _integer(getattr(handler, "MAX_OPTIMIZATION_ROUNDS", None))
        if round_number >= maximum_rounds:
            return value
        if value.get("paidBackfillAuthorized") is not True:
            return value
        if value.get("featureRematerializationComplete") is not True:
            return value
        if value.get("featureRematerializationErrors"):
            return value
        if value.get("champion") or value.get("productionCutover"):
            return value

        latest = value.get("latestExperiment") or {}
        gate = latest.get("promotionGate") or {}
        if latest.get("status") != "CANDIDATE_REJECTED" or gate.get("passed") is not False:
            return value

        current_raw = str(value.get("currentDate") or "")
        end_raw = str(value.get("endDate") or getattr(handler, "END_DATE", "") or "")
        try:
            current = date.fromisoformat(current_raw)
            end = date.fromisoformat(end_raw)
        except ValueError:
            return value
        if current > end:
            return value

        latest_evaluated = _latest_evaluated_date(value)
        if latest_evaluated and current_raw <= latest_evaluated:
            return value

        eligible = _integer(value.get("eligibleGameCount"))
        prior_target = _integer(value.get("targetSettledGames"), eligible)
        increment = _integer(
            getattr(handler, "FRESH_AUDIT_INCREMENT_GAMES", None),
            policy_increment,
        )
        next_target = max(prior_target + increment, eligible + increment)

        recovered = copy.deepcopy(value)
        recovered["targetSettledGames"] = next_target
        recovered["phase"] = "BACKFILLING"
        recovered["freshAuditExpansionRequired"] = True
        recovered["freshAuditStartDate"] = max(
            str(recovered.get("freshAuditStartDate") or ""), current_raw
        )
        recovered["freshAuditCollectedDayCount"] = 0
        recovered["freshAuditCollectedGameCount"] = 0
        recovered["lastError"] = None
        recovered["optimizationRoundLimitRecovery"] = {
            "version": VERSION,
            "recoveredAtUtc": handler._now_iso(),
            "optimizationRound": round_number,
            "activeMaximumOptimizationRounds": maximum_rounds,
            "priorTargetSettledGames": prior_target,
            "newTargetSettledGames": next_target,
            "freshAuditStartDate": recovered["freshAuditStartDate"],
            "latestPreviouslyEvaluatedAuditDate": latest_evaluated,
            "strictlyLaterAuditRequired": True,
            "priorCandidateAuthorityGranted": False,
            "canonicalFreshAuditIncrementGames": increment,
        }
        return recovered

    handler._migrate_state = patched
    handler._INQSI_HISTORICAL_ROUND_EXTENSION_V1_INSTALLED = True
