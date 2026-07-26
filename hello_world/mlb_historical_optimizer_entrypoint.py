"""Historical optimizer entrypoint with source-honest MLB slate canonicalization.

MLB's exact-date schedule endpoint can include postponed or resumed games whose
``officialDate`` belongs to a different slate. Those provider cross-references
must not poison the requested day's canonical slate, and they must never be
silently discarded.

This entrypoint also supports an auditable, deployment-authorized extension of
an exhausted historical ledger. Existing paid evidence and evaluated audit
windows remain immutable; only strictly later dates are appended.
"""
from __future__ import annotations

import copy
import os
from datetime import date, timedelta
from typing import Any, Callable, Dict, Mapping, Optional

import inqsi_pull_history as history
import mlb_canonical_final_labels_v1 as final_labels
import mlb_historical_derived_features_v1 as derived_features
import mlb_odds_pattern_features_v1 as odds_pattern_features
import mlb_historical_optimizer_handler as optimizer_handler
import mlb_historical_quarantine_contract_v2 as quarantine_contract
import mlb_historical_versioned_dataset_key_v3 as versioned_dataset_key

VERSION = "MLB-HISTORICAL-ENTRYPOINT-v9-opening-day-range-repair"

# MLB Stats API game types that can produce authoritative championship-season
# labels. Spring Training (S), exhibition, All-Star, and other non-competitive
# games are retained as exclusion evidence but never enter training datasets.
COMPETITIVE_GAME_TYPES = frozenset({"R", "F", "D", "L", "W"})
DEFAULT_COMPETITIVE_EXTENSION_START_DATE = "2026-03-25"

optimizer_handler.MAX_NETWORK_REQUESTS = min(
    int(optimizer_handler.MAX_NETWORK_REQUESTS), 20
)
optimizer_handler.LEASE_SECONDS = max(int(optimizer_handler.LEASE_SECONDS), 960)


def _truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _competitive_extension_start() -> date:
    raw = str(
        os.environ.get(
            "MLB_HISTORICAL_COMPETITIVE_EXTENSION_START_DATE",
            DEFAULT_COMPETITIVE_EXTENSION_START_DATE,
        )
        or DEFAULT_COMPETITIVE_EXTENSION_START_DATE
    ).strip()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError(
            "MLB_HISTORICAL_COMPETITIVE_EXTENSION_START_DATE_INVALID"
        ) from exc


def _team_name(raw: Mapping[str, Any], side: str) -> Optional[str]:
    side_row = ((raw.get("teams") or {}).get(side) or {})
    value = ((side_row.get("team") or {}).get("name"))
    return str(value) if value else None


def _exclusion_evidence(
    raw: Mapping[str, Any], slate_date: str, reason: str
) -> Dict[str, Any]:
    status = raw.get("status") or {}
    return {
        "officialGamePk": str(raw.get("gamePk") or ""),
        "queriedSlateDateEt": slate_date,
        "officialDate": str(raw.get("officialDate") or ""),
        "gameDate": raw.get("gameDate"),
        "gameType": str(raw.get("gameType") or ""),
        "rescheduleDate": raw.get("rescheduleDate"),
        "resumeDate": raw.get("resumeDate"),
        "rescheduledFrom": raw.get("rescheduledFrom"),
        "resumeGameDate": raw.get("resumeGameDate"),
        "awayTeam": _team_name(raw, "away"),
        "homeTeam": _team_name(raw, "home"),
        "officialStatus": {
            "abstractGameState": status.get("abstractGameState"),
            "codedGameState": status.get("codedGameState"),
            "statusCode": status.get("statusCode"),
            "detailedState": status.get("detailedState"),
        },
        "exclusionReason": reason,
    }


def _cross_date_evidence(raw: Mapping[str, Any], slate_date: str) -> Dict[str, Any]:
    return _exclusion_evidence(
        raw, slate_date, "provider_exact_date_response_cross_date_reference"
    )


def fetch_official_schedule_cross_date_safe(
    slate_date: str,
    *,
    timeout: int = 15,
    http_get: Optional[Callable[[str, int], Any]] = None,
) -> Dict[str, Any]:
    getter = http_get or (
        lambda url, seconds: final_labels._http_get_json(url, seconds)
    )
    payload = getter(final_labels.official_finals_url(slate_date), timeout)
    if not isinstance(payload, dict):
        raise RuntimeError("MLB_OFFICIAL_FINAL_PAYLOAD_NOT_OBJECT")
    dates = payload.get("dates")
    if not isinstance(dates, list):
        raise RuntimeError("MLB_OFFICIAL_FINAL_DATES_INVALID")

    filtered_dates = []
    cross_date_exclusions = []
    non_competitive_exclusions = []
    seen = set()
    provider_game_count = 0
    for date_row in dates:
        if not isinstance(date_row, dict) or str(date_row.get("date") or "") != slate_date:
            raise RuntimeError("MLB_OFFICIAL_FINAL_NOT_EXACT_DATE")
        games = date_row.get("games")
        if not isinstance(games, list):
            raise RuntimeError("MLB_OFFICIAL_FINAL_GAMES_INVALID")
        kept = []
        for raw in games:
            if not isinstance(raw, dict):
                raise RuntimeError("MLB_OFFICIAL_FINAL_GAME_ROW_INVALID")
            provider_game_count += 1
            game_pk = str(raw.get("gamePk") or "").strip()
            if not game_pk or game_pk in seen:
                raise RuntimeError("MLB_OFFICIAL_FINAL_GAME_PK_INVALID_OR_DUPLICATE")
            seen.add(game_pk)

            game_type = str(raw.get("gameType") or "").strip().upper()
            if game_type and game_type not in COMPETITIVE_GAME_TYPES:
                non_competitive_exclusions.append(
                    _exclusion_evidence(
                        raw, slate_date, "provider_non_competitive_game_type"
                    )
                )
                continue

            official_date = str(raw.get("officialDate") or slate_date)
            if official_date != slate_date:
                evidence = _cross_date_evidence(raw, slate_date)
                if not evidence["officialDate"]:
                    raise RuntimeError(
                        f"MLB_OFFICIAL_FINAL_CROSS_DATE_IDENTITY_UNPROVEN:{game_pk}"
                    )
                cross_date_exclusions.append(evidence)
            else:
                kept.append(copy.deepcopy(raw))
        row = copy.deepcopy(date_row)
        row["games"] = kept
        row["totalGames"] = len(kept)
        filtered_dates.append(row)

    filtered = copy.deepcopy(payload)
    filtered["dates"] = filtered_dates
    filtered["totalGames"] = sum(len(row["games"]) for row in filtered_dates)
    canonical = final_labels.validate_official_schedule_payload(filtered, slate_date)
    cross_date_exclusions = sorted(
        cross_date_exclusions, key=lambda row: row["officialGamePk"]
    )
    non_competitive_exclusions = sorted(
        non_competitive_exclusions, key=lambda row: row["officialGamePk"]
    )
    all_exclusions = cross_date_exclusions + non_competitive_exclusions
    canonical.update(
        {
            "crossDateCanonicalizationVersion": VERSION,
            "providerReportedGameCount": provider_game_count,
            "crossDateExcludedCount": len(cross_date_exclusions),
            "crossDateExclusions": cross_date_exclusions,
            "crossDateExclusionFingerprint": history.canonical_payload_fingerprint(
                cross_date_exclusions
            ),
            "nonCompetitiveExcludedCount": len(non_competitive_exclusions),
            "nonCompetitiveExclusions": non_competitive_exclusions,
            "nonCompetitiveExclusionFingerprint": history.canonical_payload_fingerprint(
                non_competitive_exclusions
            ),
            "canonicalOfficialDateGameCount": canonical["officialGameCount"],
        }
    )
    if provider_game_count != canonical["officialGameCount"] + len(all_exclusions):
        raise RuntimeError("MLB_OFFICIAL_FINAL_CROSS_DATE_ACCOUNTING_MISMATCH")
    return canonical


def _date_value(value: Any) -> date:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise RuntimeError("MLB_HISTORICAL_LEDGER_DATE_INVALID") from exc


def _recalculate_plan(plan: Dict[str, Any]) -> None:
    slates = list(plan.get("slates") or [])
    if not slates:
        raise RuntimeError("MLB_HISTORICAL_COMPETITIVE_REPAIR_EMPTY_PLAN")
    plan["plannedThroughDate"] = str(slates[-1].get("slateDateEt") or "")
    plan["plannedOfficialGames"] = sum(
        int(row.get("officialGameCount") or 0) for row in slates
    )
    plan["maximumAuthorizedOfficialGames"] = plan["plannedOfficialGames"]
    plan["plannedCompleteSlateDays"] = len(slates)
    plan["historicalRequestCount"] = sum(
        int(row.get("historicalRequestCount") or 0) for row in slates
    )
    plan["estimatedCredits"] = sum(int(row.get("estimatedCredits") or 0) for row in slates)
    plan["slateLedgerDigest"] = optimizer_handler._sha256(
        optimizer_handler._json_bytes(slates)
    )


def _repair_precompetitive_extension_state() -> None:
    """Remove cached Spring Training slate rows from an already-authorized extension.

    Immutable S3 evidence and actual provider usage are retained. Only active ledger,
    cursor, completion, rejection, and quarantine indexes are re-fingerprinted so the
    optimizer resumes at the first 2026 championship-season game.
    """
    if not _truthy("MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED"):
        return
    state = optimizer_handler._load_state()
    if not isinstance(state, dict):
        return
    plan = copy.deepcopy(state.get("plan") or {})
    range_info = copy.deepcopy(plan.get("rangeExtension") or state.get("rangeExtension") or {})
    if not plan or not range_info.get("competitiveGameTypes"):
        return

    start = _competitive_extension_start()
    previous_raw = range_info.get("previousEndDate")
    if not previous_raw:
        return
    previous_end = _date_value(previous_raw)
    if start <= previous_end + timedelta(days=1):
        return

    def in_gap(value: Any) -> bool:
        day = _date_value(value)
        return previous_end < day < start

    original_slates = list(plan.get("slates") or [])
    removed_slates = [
        copy.deepcopy(row)
        for row in original_slates
        if in_gap(row.get("slateDateEt"))
    ]
    kept_slates = [
        copy.deepcopy(row)
        for row in original_slates
        if not in_gap(row.get("slateDateEt"))
    ]

    current_raw = str(state.get("currentDate") or "")
    cursor_needs_repair = bool(current_raw and in_gap(current_raw))
    if not removed_slates and not cursor_needs_repair:
        return
    if not kept_slates:
        raise RuntimeError("MLB_HISTORICAL_COMPETITIVE_REPAIR_REMOVED_ALL_SLATES")

    completed = list(state.get("completedSlates") or [])
    removed_completed = [
        copy.deepcopy(row)
        for row in completed
        if in_gap(row.get("slateDateEt"))
    ]
    kept_completed = [
        copy.deepcopy(row)
        for row in completed
        if not in_gap(row.get("slateDateEt"))
    ]
    rejected = list(state.get("rejectedSlates") or [])
    removed_rejected = [
        copy.deepcopy(row)
        for row in rejected
        if in_gap(row.get("slateDateEt"))
    ]
    kept_rejected = [
        copy.deepcopy(row)
        for row in rejected
        if not in_gap(row.get("slateDateEt"))
    ]
    skipped = list(state.get("skippedHistoricalSlots") or [])
    removed_skipped = [
        copy.deepcopy(row)
        for row in skipped
        if in_gap(row.get("slateDateEt"))
    ]
    kept_skipped = [
        copy.deepcopy(row)
        for row in skipped
        if not in_gap(row.get("slateDateEt"))
    ]

    plan["slates"] = sorted(
        kept_slates, key=lambda row: str(row.get("slateDateEt") or "")
    )
    _recalculate_plan(plan)
    range_info.update(
        {
            "version": "MLB-HISTORICAL-RANGE-EXTENSION-v3-opening-day-bounded",
            "competitiveStartDate": start.isoformat(),
            "removedPreCompetitiveSlateCount": len(removed_slates),
            "removedPreCompetitiveSlateDates": sorted(
                str(row.get("slateDateEt") or "") for row in removed_slates
            ),
            "repairedAtUtc": optimizer_handler._now_iso(),
        }
    )
    plan["rangeExtension"] = range_info
    plan["fingerprint"] = optimizer_handler._plan_fingerprint(plan)

    state["plan"] = plan
    state["authorizedPlanFingerprint"] = plan["fingerprint"]
    state["rangeExtension"] = copy.deepcopy(range_info)
    state["completedSlates"] = kept_completed
    state["completeSlateCount"] = len(kept_completed)
    state["eligibleGameCount"] = sum(
        int(row.get("eligibleGameCount") or 0) for row in kept_completed
    )
    state["rejectedSlates"] = kept_rejected
    state["skippedHistoricalSlots"] = kept_skipped
    state["lastRejectedSlateError"] = (
        str(kept_rejected[-1].get("reason") or "") if kept_rejected else None
    )
    state["currentDate"] = start.isoformat()
    state["currentSlotIndex"] = 0
    state["phase"] = "BACKFILLING"
    state["lastError"] = None
    state.pop("rangeExtensionRejectedDates", None)

    removed_dates = sorted(
        {
            str(row.get("slateDateEt") or "")
            for row in removed_slates + removed_completed + removed_rejected
            if row.get("slateDateEt")
        }
    )
    state["competitiveRangeRepair"] = {
        "version": "MLB-HISTORICAL-COMPETITIVE-RANGE-REPAIR-v1",
        "previousEndDate": previous_end.isoformat(),
        "competitiveStartDate": start.isoformat(),
        "previousCursorDate": current_raw,
        "previousCursorSlotIndex": int(state.get("currentSlotIndex") or 0),
        "removedPlanSlateCount": len(removed_slates),
        "removedCompletedSlateCount": len(removed_completed),
        "removedRejectedSlateCount": len(removed_rejected),
        "removedSkippedSlotCount": len(removed_skipped),
        "removedDateCount": len(removed_dates),
        "removedDatesFingerprint": history.canonical_payload_fingerprint(removed_dates),
        "providerCreditsRetained": True,
        "immutableS3EvidenceRetained": True,
        "repairedAtUtc": optimizer_handler._now_iso(),
    }

    last_finals = state.get("lastCompletedFinalsArtifact") or {}
    last_finals_key = str(last_finals.get("key") or "")
    if any(f"/{day}.json" in last_finals_key for day in removed_dates):
        state["lastCompletedFinalsArtifact"] = None
        state["lastCompletedQuarantineCount"] = 0

    optimizer_handler._save_state(state)


def _append_authorized_range_extension() -> None:
    """Append a strictly later, fingerprinted ledger when deployment authorizes it."""
    if not _truthy("MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED"):
        return
    state = optimizer_handler._load_state()
    if not isinstance(state, dict):
        return
    if state.get("phase") not in {
        "DATA_RANGE_EXHAUSTED",
        "CANDIDATE_REJECTED",
        "RANGE_EXTENSION_BLOCKED_INCOMPLETE_LEDGER",
        "PAUSED_QUOTA",
    }:
        return

    previous_end = date.fromisoformat(str(state.get("endDate") or optimizer_handler.END_DATE))
    configured_end = date.fromisoformat(str(optimizer_handler.END_DATE))
    if configured_end <= previous_end:
        return

    plan = copy.deepcopy(state.get("plan") or {})
    if not plan or state.get("paidBackfillAuthorized") is not True:
        return

    existing_dates = {
        str(row.get("slateDateEt") or "")
        for row in plan.get("slates") or []
        if isinstance(row, Mapping)
    }
    appended = []
    rejected = []
    start = _competitive_extension_start()
    cursor = max(previous_end + timedelta(days=1), start)
    while cursor <= configured_end:
        day = cursor.isoformat()
        try:
            finals, _ = optimizer_handler._load_or_fetch_finals(day)
            official_count = int(finals.get("officialGameCount") or 0)
            if official_count and day not in existing_dates:
                starts = [
                    optimizer_handler.optimizer._parse_dt(row.get("gameDate"))
                    for row in finals.get("games") or []
                ]
                starts = [value for value in starts if value is not None]
                if len(starts) != official_count:
                    raise optimizer_handler.OrchestrationError("official_start_time_missing")
                grid = optimizer_handler.optimizer.build_snapshot_grid(day, starts)
                appended.append(
                    {
                        "slateDateEt": day,
                        "officialGameCount": official_count,
                        "historicalRequestCount": len(grid.timestamps_utc),
                        "estimatedCredits": len(grid.timestamps_utc)
                        * optimizer_handler.ESTIMATED_CREDITS_PER_HISTORICAL_REQUEST,
                        "firstGameStartUtc": grid.first_game_start_utc,
                        "lastGameStartUtc": grid.last_game_start_utc,
                        "firstRequestUtc": grid.timestamps_utc[0],
                        "lastRequestUtc": grid.timestamps_utc[-1],
                    }
                )
        except Exception as exc:
            rejected.append(
                {
                    "slateDateEt": day,
                    "details": f"{type(exc).__name__}:{str(exc)[:200]}",
                }
            )
        cursor += timedelta(days=1)

    if rejected:
        state["phase"] = "RANGE_EXTENSION_BLOCKED_INCOMPLETE_LEDGER"
        state["lastError"] = (
            "historical range extension could not prove every later competitive schedule date"
        )
        state["rangeExtensionRejectedDates"] = rejected
        optimizer_handler._save_state(state)
        return
    if not appended:
        state["phase"] = "DATA_RANGE_EXHAUSTED"
        state["lastError"] = "extended historical range contains no additional MLB game slates"
        optimizer_handler._save_state(state)
        return

    extension_credits = sum(int(row["estimatedCredits"]) for row in appended)
    projected = int(state.get("creditsConsumed") or 0) + extension_credits
    maximum = max(int(state.get("maximumCredits") or 0), int(optimizer_handler.MAX_CREDITS))
    quota = optimizer_handler._quota_status()
    remaining = quota.get("x-requests-remaining")
    if projected > maximum or (
        isinstance(remaining, int)
        and remaining < extension_credits + optimizer_handler.QUOTA_RESERVE
    ):
        state["phase"] = "PAUSED_QUOTA"
        state["lastError"] = (
            "range extension is valid but the configured credit/quota guard blocks paid requests"
        )
        state["rangeExtensionEstimatedCredits"] = extension_credits
        state["lastQuota"] = quota
        optimizer_handler._save_state(state)
        return

    merged = list(plan.get("slates") or []) + appended
    merged = sorted(merged, key=lambda row: str(row.get("slateDateEt") or ""))
    plan["slates"] = merged
    plan["endDate"] = configured_end.isoformat()
    _recalculate_plan(plan)
    plan["maximumCredits"] = maximum
    plan["providerReportedRemainingCredits"] = remaining
    plan["completeDateRangeLedger"] = True
    plan["planningErrorCount"] = 0
    plan["rejectedDates"] = []
    plan["rangeExtension"] = {
        "version": "MLB-HISTORICAL-RANGE-EXTENSION-v3-opening-day-bounded",
        "previousEndDate": previous_end.isoformat(),
        "newEndDate": configured_end.isoformat(),
        "competitiveStartDate": start.isoformat(),
        "appendedSlateCount": len(appended),
        "appendedEstimatedCredits": extension_credits,
        "competitiveGameTypes": sorted(COMPETITIVE_GAME_TYPES),
        "authorizedByDeployment": True,
        "authorizedAtUtc": optimizer_handler._now_iso(),
    }
    plan["fingerprint"] = optimizer_handler._plan_fingerprint(plan)

    state["plan"] = plan
    state["authorizedPlanFingerprint"] = plan["fingerprint"]
    state["endDate"] = configured_end.isoformat()
    state["maximumCredits"] = maximum
    state["phase"] = "BACKFILLING"
    state["lastError"] = None
    state.pop("rangeExtensionRejectedDates", None)
    state.pop("rangeExtensionEstimatedCredits", None)
    state["rangeExtension"] = copy.deepcopy(plan["rangeExtension"])
    optimizer_handler._save_state(state)


optimizer_handler.final_labels.fetch_official_schedule = fetch_official_schedule_cross_date_safe
quarantine_contract.install()
versioned_dataset_key.install()
derived_features.install(optimizer_handler.optimizer, optimizer_handler.policy_runtime)
odds_pattern_features.install(optimizer_handler.optimizer, optimizer_handler.policy_runtime)


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    _repair_precompetitive_extension_state()
    _append_authorized_range_extension()
    return optimizer_handler.lambda_handler(event, context)
