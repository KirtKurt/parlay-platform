"""Incrementally extend the authorized MLB historical ledger through settled slates.

The deployment end date is a recovery ceiling, not proof that future games are final.
This patch appends only the contiguous date range that can currently be proven settled,
retains immutable prior evidence, and retries an unresolved boundary date later.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Mapping
from zoneinfo import ZoneInfo

VERSION = "MLB-HISTORICAL-INCREMENTAL-RANGE-EXTENSION-v2-waiting-resume"
EASTERN = ZoneInfo("America/New_York")
WAITING_PHASE = "WAITING_FOR_SETTLED_HORIZON"


def settled_horizon(now_utc: datetime | None = None) -> date:
    """Return the latest slate date safe to attempt without assuming today's games ended."""
    value = now_utc or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(EASTERN).date() - timedelta(days=1)


def _extension_row(handler: Any, day: str, finals: Mapping[str, Any]) -> Dict[str, Any] | None:
    official_count = int(finals.get("officialGameCount") or 0)
    if not official_count:
        return None
    starts = [
        handler.optimizer._parse_dt(row.get("gameDate"))
        for row in finals.get("games") or []
    ]
    starts = [value for value in starts if value is not None]
    if len(starts) != official_count:
        raise handler.OrchestrationError("official_start_time_missing")
    grid = handler.optimizer.build_snapshot_grid(day, starts)
    return {
        "slateDateEt": day,
        "officialGameCount": official_count,
        "historicalRequestCount": len(grid.timestamps_utc),
        "estimatedCredits": len(grid.timestamps_utc)
        * handler.ESTIMATED_CREDITS_PER_HISTORICAL_REQUEST,
        "firstGameStartUtc": grid.first_game_start_utc,
        "lastGameStartUtc": grid.last_game_start_utc,
        "firstRequestUtc": grid.timestamps_utc[0],
        "lastRequestUtc": grid.timestamps_utc[-1],
    }


def install(base: Any) -> None:
    """Replace the future-range all-or-nothing extension with settled incremental repair."""
    if getattr(base, "_INQSI_INCREMENTAL_RANGE_EXTENSION_V1_INSTALLED", False):
        return

    handler = base.optimizer_handler

    def append_incrementally() -> None:
        if not base._truthy("MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED"):
            return
        state = handler._load_state()
        if not isinstance(state, dict):
            return
        if state.get("phase") not in {
            "DATA_RANGE_EXHAUSTED",
            "CANDIDATE_REJECTED",
            "RANGE_EXTENSION_BLOCKED_INCOMPLETE_LEDGER",
            "PAUSED_QUOTA",
            WAITING_PHASE,
        }:
            return

        previous_end = date.fromisoformat(str(state.get("endDate") or handler.END_DATE))
        configured_end = date.fromisoformat(str(handler.END_DATE))
        horizon = min(configured_end, settled_horizon())
        if horizon <= previous_end:
            return

        plan = copy.deepcopy(state.get("plan") or {})
        if not plan or state.get("paidBackfillAuthorized") is not True:
            return

        existing_dates = {
            str(row.get("slateDateEt") or "")
            for row in plan.get("slates") or []
            if isinstance(row, Mapping)
        }
        start = base._competitive_extension_start()
        cursor = max(previous_end + timedelta(days=1), start)
        appended: list[Dict[str, Any]] = []
        deferred: list[Dict[str, Any]] = []
        proven_through = previous_end

        while cursor <= horizon:
            day = cursor.isoformat()
            try:
                finals, _ = handler._load_or_fetch_finals(day)
                row = _extension_row(handler, day, finals)
                if row is not None and day not in existing_dates:
                    appended.append(row)
                    existing_dates.add(day)
                proven_through = cursor
            except Exception as exc:
                deferred.append(
                    {
                        "slateDateEt": day,
                        "details": f"{type(exc).__name__}:{str(exc)[:200]}",
                        "classification": "NOT_YET_PROVABLY_SETTLED",
                    }
                )
                break
            cursor += timedelta(days=1)

        if proven_through <= previous_end:
            state["phase"] = "DATA_RANGE_EXHAUSTED"
            state["lastError"] = None
            state["rangeExtensionDeferredDates"] = deferred
            state["rangeExtensionNextRetryDate"] = (
                deferred[0]["slateDateEt"] if deferred else None
            )
            handler._save_state(state)
            return

        extension_credits = sum(int(row["estimatedCredits"]) for row in appended)
        projected = int(state.get("creditsConsumed") or 0) + extension_credits
        maximum = max(int(state.get("maximumCredits") or 0), int(handler.MAX_CREDITS))
        quota = handler._quota_status() if extension_credits else (state.get("lastQuota") or {})
        remaining = quota.get("x-requests-remaining")
        if extension_credits and (
            projected > maximum
            or (
                isinstance(remaining, int)
                and remaining < extension_credits + handler.QUOTA_RESERVE
            )
        ):
            state["phase"] = "PAUSED_QUOTA"
            state["lastError"] = (
                "incremental range extension is valid but the configured credit/quota guard blocks paid requests"
            )
            state["rangeExtensionEstimatedCredits"] = extension_credits
            state["lastQuota"] = quota
            handler._save_state(state)
            return

        merged = list(plan.get("slates") or []) + appended
        merged = sorted(merged, key=lambda row: str(row.get("slateDateEt") or ""))
        plan["slates"] = merged
        plan["endDate"] = proven_through.isoformat()
        base._recalculate_plan(plan)
        plan["maximumCredits"] = maximum
        plan["providerReportedRemainingCredits"] = remaining
        plan["completeDateRangeLedger"] = True
        plan["planningErrorCount"] = 0
        plan["rejectedDates"] = []
        plan["rangeExtension"] = {
            "version": VERSION,
            "previousEndDate": previous_end.isoformat(),
            "newEndDate": proven_through.isoformat(),
            "configuredCeilingDate": configured_end.isoformat(),
            "settledHorizonDate": horizon.isoformat(),
            "competitiveStartDate": start.isoformat(),
            "appendedSlateCount": len(appended),
            "appendedEstimatedCredits": extension_credits,
            "competitiveGameTypes": sorted(base.COMPETITIVE_GAME_TYPES),
            "deferredDateCount": len(deferred),
            "nextRetryDate": deferred[0]["slateDateEt"] if deferred else None,
            "authorizedByDeployment": True,
            "authorizedAtUtc": handler._now_iso(),
        }
        plan["fingerprint"] = handler._plan_fingerprint(plan)

        state["plan"] = plan
        state["authorizedPlanFingerprint"] = plan["fingerprint"]
        state["endDate"] = proven_through.isoformat()
        state["maximumCredits"] = maximum
        state["lastError"] = None
        state.pop("rangeExtensionRejectedDates", None)
        state.pop("rangeExtensionEstimatedCredits", None)
        state["rangeExtensionDeferredDates"] = deferred
        state["rangeExtensionNextRetryDate"] = (
            deferred[0]["slateDateEt"] if deferred else None
        )
        state["rangeExtension"] = copy.deepcopy(plan["rangeExtension"])

        if appended:
            state["phase"] = "BACKFILLING"
        else:
            state["phase"] = "DATA_RANGE_EXHAUSTED"
            current = date.fromisoformat(
                str(state.get("currentDate") or (previous_end + timedelta(days=1)).isoformat())
            )
            if current <= proven_through:
                state["currentDate"] = (proven_through + timedelta(days=1)).isoformat()
                state["currentSlotIndex"] = 0

        handler._save_state(state)

    base._append_authorized_range_extension = append_incrementally
    base._INQSI_INCREMENTAL_RANGE_EXTENSION_V1_INSTALLED = True
