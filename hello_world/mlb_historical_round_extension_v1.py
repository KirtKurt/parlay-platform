"""Safely resume rejected MLB historical optimization with a fresh audit window.

This patch never changes a candidate, promotion gate, historical evidence, or prior
untouched-audit assignment. It only reopens a terminal candidate-rejected state
when a deployment has explicitly raised the optimization-round ceiling and the
ledger still contains strictly later authorized dates.
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any, Dict, Mapping

VERSION = "MLB-HISTORICAL-ROUND-EXTENSION-v1-strictly-later-audit-only"


def _latest_evaluated_date(state: Mapping[str, Any]) -> str | None:
    dates = []
    for window in state.get("evaluatedAuditWindows") or []:
        if not isinstance(window, Mapping):
            continue
        dates.extend(str(value) for value in window.get("dates") or [] if str(value))
    return max(dates) if dates else None


def install(handler: Any) -> None:
    if getattr(handler, "_INQSI_HISTORICAL_ROUND_EXTENSION_V1_INSTALLED", False):
        return

    original = handler._migrate_state

    def patched(state: Mapping[str, Any]) -> Dict[str, Any]:
        value = original(state)
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
        recovered["freshAuditStartDate"] = current_raw
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
            "freshAuditStartDate": current_raw,
            "latestPreviouslyEvaluatedAuditDate": latest_evaluated,
            "strictlyLaterAuditRequired": True,
            "priorCandidateAuthorityGranted": False,
        }
        return recovered

    handler._migrate_state = patched
    handler._INQSI_HISTORICAL_ROUND_EXTENSION_V1_INSTALLED = True
