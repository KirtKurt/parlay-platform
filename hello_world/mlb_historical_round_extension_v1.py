"""Safely enforce strictly-later MLB historical untouched-audit rounds.

This patch never changes a candidate, promotion gate, historical evidence, or prior
untouched-audit assignment. It repairs migrated/retried non-promoted states so any
subsequent optimization must use dates strictly later than every previously
label-evaluated untouched-audit date. It also preserves the bounded round-extension
recovery for terminal rejected states when the deployment ceiling was raised.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any, Dict, Mapping

VERSION = "MLB-HISTORICAL-ROUND-EXTENSION-v2-persisted-audit-history-guard"


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

    eligible = int(repaired.get("eligibleGameCount") or 0)
    prior_target = int(repaired.get("targetSettledGames") or eligible)
    increment = int(getattr(handler, "FRESH_AUDIT_INCREMENT_GAMES", 250) or 250)
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
        "newTargetSettledGames": int(repaired.get("targetSettledGames") or prior_target),
    }
    return repaired


def install(handler: Any) -> None:
    if getattr(handler, "_INQSI_HISTORICAL_ROUND_EXTENSION_V1_INSTALLED", False):
        return

    original = handler._migrate_state

    def patched(state: Mapping[str, Any]) -> Dict[str, Any]:
        value = _repair_persisted_audit_boundary(handler, original(state))
        if str(value.get("phase") or "") != "CANDIDATE_REJECTED":
            return value

        round_number = int(value.get("optimizationRound") or 0)
        maximum_rounds = int(getattr(handler, "MAX_OPTIMIZATION_ROUNDS", 0) or 0)
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

        eligible = int(value.get("eligibleGameCount") or 0)
        prior_target = int(value.get("targetSettledGames") or eligible)
        increment = int(getattr(handler, "FRESH_AUDIT_INCREMENT_GAMES", 250) or 250)
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
        }
        return recovered

    handler._migrate_state = patched
    handler._INQSI_HISTORICAL_ROUND_EXTENSION_V1_INSTALLED = True
