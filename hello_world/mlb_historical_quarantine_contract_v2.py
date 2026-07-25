"""Quarantine-aware request-ledger contract for MLB historical training.

The Odds API can return a nearest historical snapshot whose provider timestamp is
outside the strict 15-minute grid tolerance.  The request was still made and its
immutable response was still archived, so it must remain represented in the
request ledger even though it is not eligible to enter model features.

This module installs three runtime overrides on the isolated historical Lambda:

* archive provider responses before freshness validation;
* represent rejected observations as explicit quarantine ledger rows; and
* build slate datasets from valid observations while retaining exact request
  accounting and full-slate eligibility checks.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Sequence

import mlb_historical_daily_optimizer_v1 as optimizer
import mlb_historical_optimizer_handler as handler

VERSION = "MLB-HISTORICAL-QUARANTINE-CONTRACT-v2"
HANDLER_VERSION = "MLB-HISTORICAL-OPTIMIZER-AWS-v1.9-quarantine-ledger-complete"
QUARANTINE_PREFIX = "QUARANTINED_"


def _quarantine_status(exc: Exception) -> str | None:
    message = str(exc).lower()
    if "too stale" in message:
        return "QUARANTINED_STALE"
    if "timestamp is after the request" in message:
        return "QUARANTINED_FUTURE_TIMESTAMP"
    if "timestamp is invalid" in message:
        return "QUARANTINED_INVALID_TIMESTAMP"
    if "payload data is not a list" in message or "non-object event" in message:
        return "QUARANTINED_MALFORMED_PAYLOAD"
    return None


def _fetch_historical(requested_at_utc: str):
    """Fetch and return the paid response before freshness policy is applied."""

    payload, headers = handler._http_json(handler._historical_url(requested_at_utc))
    if not isinstance(payload, Mapping):
        raise handler.OrchestrationError("historical response is not a JSON object")
    return dict(payload), headers


def _is_quarantined(row: Mapping[str, Any]) -> bool:
    status = str(row.get("status") or "").upper()
    return row.get("usableForFeatures") is False or status.startswith(QUARANTINE_PREFIX)


def build_slate_dataset(
    slate_date_et: str,
    official_final_rows: Sequence[Mapping[str, Any]],
    historical_snapshots: Sequence[Mapping[str, Any]],
    grid: optimizer.SnapshotGrid,
) -> Dict[str, Any]:
    """Build a no-leakage slate while distinguishing missing from quarantined.

    A missing planned timestamp remains fatal.  A timestamp represented by an
    explicit quarantine row completes the immutable request ledger but is never
    normalized, crosswalked, or used in any feature calculation.
    """

    official = [optimizer._official_game(row) for row in official_final_rows]
    if not official:
        raise optimizer.HistoricalOptimizerError("official slate has no settled games")
    if grid.slate_date_et != slate_date_et:
        raise optimizer.HistoricalOptimizerError("snapshot grid slate date mismatch")

    lock_by_game = {
        game["officialGamePk"]: optimizer._parse_dt(game["commenceTime"])
        - timedelta(minutes=optimizer.FULL_SLATE_LOCK_MINUTES)
        for game in official
    }
    final_grid_lock = optimizer._parse_dt(grid.lock_at_utc)
    if final_grid_lock is None or abs(
        (final_grid_lock - max(lock_by_game.values())).total_seconds()
    ) > 1.0:
        raise optimizer.HistoricalOptimizerError(
            "snapshot grid does not end at the final game T-minus-45"
        )

    expected_requests = list(grid.timestamps_utc)
    expected_set = set(expected_requests)
    by_requested: Dict[str, Mapping[str, Any]] = {}
    for row in historical_snapshots:
        requested = str(row.get("requestedAtUtc") or "")
        if not requested or requested in by_requested:
            raise optimizer.HistoricalOptimizerError(
                "historical request ledger contains duplicate or blank timestamps"
            )
        by_requested[requested] = row

    extra_requests = sorted(set(by_requested) - expected_set)
    if extra_requests:
        raise optimizer.HistoricalOptimizerError(
            "historical request ledger contains unplanned timestamps"
        )
    missing_requests = [value for value in expected_requests if value not in by_requested]
    if missing_requests:
        raise optimizer.HistoricalOptimizerError("historical request ledger is incomplete")

    normalized: List[Dict[str, Any]] = []
    quarantined_requests: List[str] = []
    series: Dict[str, List[Dict[str, Any]]] = {
        game["officialGamePk"]: [] for game in official
    }

    for requested in expected_requests:
        ledger_row = by_requested[requested]
        if _is_quarantined(ledger_row):
            status = str(ledger_row.get("status") or "QUARANTINED_INVALID").upper()
            if not status.startswith(QUARANTINE_PREFIX):
                raise optimizer.HistoricalOptimizerError(
                    "unusable historical request lacks a quarantine status"
                )
            quarantined_requests.append(requested)
            normalized.append(
                {
                    "requestedAtUtc": requested,
                    "providerTimestampUtc": ledger_row.get("providerTimestampUtc"),
                    "matchedOfficialGames": 0,
                    "acceptedBeforePerGameLock": 0,
                    "providerEventCount": 0,
                    "status": status,
                    "usableForFeatures": False,
                    "reason": str(ledger_row.get("reason") or "")[:500],
                }
            )
            continue

        snapshot = optimizer.normalize_historical_snapshot(
            ledger_row.get("payload", ledger_row), requested
        )
        provider_at = optimizer._parse_dt(snapshot["providerTimestampUtc"])
        if provider_at is None or provider_at > final_grid_lock + timedelta(seconds=1):
            raise optimizer.HistoricalOptimizerError(
                "post-final-lock historical observation detected"
            )
        matches = optimizer.crosswalk_snapshot(official, snapshot["events"])
        accepted_for_games = 0
        for game in official:
            game_pk = game["officialGamePk"]
            event = matches.get(game_pk)
            if event and provider_at <= lock_by_game[game_pk] + timedelta(seconds=1):
                series[game_pk].append(
                    {
                        **event,
                        "requestedAtUtc": snapshot["requestedAtUtc"],
                        "providerTimestampUtc": snapshot["providerTimestampUtc"],
                        "providerLagMinutes": snapshot["providerLagMinutes"],
                    }
                )
                accepted_for_games += 1
        normalized.append(
            {
                "requestedAtUtc": snapshot["requestedAtUtc"],
                "providerTimestampUtc": snapshot["providerTimestampUtc"],
                "matchedOfficialGames": len(matches),
                "acceptedBeforePerGameLock": accepted_for_games,
                "providerEventCount": len(snapshot["events"]),
                "status": "VALID",
                "usableForFeatures": True,
            }
        )

    records: List[Dict[str, Any]] = []
    exclusions: List[Dict[str, Any]] = []
    parsed_requested = [optimizer._parse_dt(value) for value in expected_requests]
    for game in official:
        game_pk = game["officialGamePk"]
        game_lock = lock_by_game[game_pk]
        observations = sorted(
            series[game_pk], key=lambda row: str(row.get("providerTimestampUtc") or "")
        )
        if any(
            (
                optimizer._parse_dt(row.get("providerTimestampUtc"))
                or datetime.max.replace(tzinfo=timezone.utc)
            )
            > game_lock + timedelta(seconds=1)
            for row in observations
        ):
            raise optimizer.HistoricalOptimizerError(
                "post-game-lock historical observation detected"
            )
        expected_game_slots = sum(
            value is not None and value <= game_lock + timedelta(seconds=1)
            for value in parsed_requested
        )
        if len(observations) < optimizer.MIN_PULLS_PER_GAME:
            exclusions.append(
                {
                    "officialGamePk": game_pk,
                    "reason": "insufficient_game_lock_bounded_pull_depth",
                    "pullCount": len(observations),
                    "predictionLockAtUtc": optimizer._iso_z(game_lock),
                }
            )
            continue
        home = optimizer._signal(game, observations, "home", expected_game_slots)
        away = optimizer._signal(game, observations, "away", expected_game_slots)
        records.append(
            {
                "version": optimizer.DATASET_VERSION,
                "slateDateEt": slate_date_et,
                "officialGamePk": game_pk,
                "homeTeam": game["homeTeam"],
                "awayTeam": game["awayTeam"],
                "commenceTime": game["commenceTime"],
                "winner": game["winner"],
                "homeWon": game["homeWon"],
                "homeSignal": home,
                "awaySignal": away,
                "requestedSlotCount": expected_game_slots,
                "observedHomePullCount": home["pullCountForGame"],
                "observedAwayPullCount": away["pullCountForGame"],
                "predictionLockAtUtc": optimizer._iso_z(game_lock),
                "postLockDataExcluded": True,
                "gameSpecificLockClipping": True,
            }
        )

    official_count = len(official)
    coverage = len(records) / official_count if official_count else 0.0
    return {
        "version": optimizer.DATASET_VERSION,
        "quarantineContractVersion": VERSION,
        "slateDateEt": slate_date_et,
        "grid": grid.to_dict(),
        "officialGameCount": official_count,
        "eligibleGameCount": len(records),
        "exactSlateCoverage": round(coverage, 8),
        "completeSlate": len(records) == official_count and not exclusions,
        "requestLedgerComplete": True,
        "plannedSnapshotCount": len(expected_requests),
        "validSnapshotCount": len(expected_requests) - len(quarantined_requests),
        "quarantinedSnapshotCount": len(quarantined_requests),
        "quarantinedRequests": quarantined_requests,
        "records": records,
        "exclusions": exclusions,
        "snapshotAudit": normalized,
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
        "fingerprint": optimizer.dataset_fingerprint(records),
    }


def _record_quarantine(
    state: Dict[str, Any], day: str, requested: str, status: str, reason: str
) -> None:
    skipped = state.setdefault("skippedHistoricalSlots", [])
    marker = {
        "slateDateEt": day,
        "requestedAtUtc": requested,
        "status": status,
        "reason": reason[:500],
        "usableForFeatures": False,
        "recordedAtUtc": handler._now_iso(),
    }
    if not any(
        isinstance(row, Mapping)
        and row.get("slateDateEt") == day
        and row.get("requestedAtUtc") == requested
        for row in skipped
    ):
        skipped.append(marker)
    state["quarantinedHistoricalSlotCount"] = len(skipped)


def _complete_slate(
    state: Dict[str, Any],
    day: str,
    finals: Mapping[str, Any],
    grid: optimizer.SnapshotGrid,
) -> None:
    historical: List[Dict[str, Any]] = []
    for requested in grid.timestamps_utc:
        raw, pointer = handler._get_s3_json(handler._raw_key(day, requested))
        payload = raw.get("payload") if isinstance(raw, Mapping) and "payload" in raw else raw
        try:
            optimizer.normalize_historical_snapshot(payload, requested)
        except optimizer.HistoricalOptimizerError as exc:
            status = _quarantine_status(exc)
            if status is None:
                raise
            _record_quarantine(state, day, requested, status, str(exc))
            historical.append(
                {
                    "requestedAtUtc": requested,
                    "status": status,
                    "usableForFeatures": False,
                    "reason": str(exc),
                    "sourceArtifact": pointer,
                }
            )
            continue
        historical.append(
            {
                "requestedAtUtc": requested,
                "status": "VALID",
                "usableForFeatures": True,
                "payload": payload,
            }
        )

    try:
        dataset = build_slate_dataset(
            day,
            finals.get("games") or [],
            historical,
            grid,
        )
    except Exception as exc:
        handler._advance_after_rejection(
            state,
            day,
            "slate_dataset_build_failed",
            {
                "error": f"{type(exc).__name__}:{exc}"[:500],
                "quarantinedSnapshotCount": sum(
                    1 for row in historical if _is_quarantined(row)
                ),
            },
        )
        state["lastRejectedSlateError"] = f"{type(exc).__name__}:{exc}"[:500]
        state["lastError"] = None
        return

    pointer = handler._put_immutable_json(
        handler._slate_key(day),
        dataset,
        record_type="mlb_historical_complete_slate",
    )
    if dataset.get("completeSlate") is not True or float(
        dataset.get("exactSlateCoverage") or 0.0
    ) < 1.0:
        handler._advance_after_rejection(
            state,
            day,
            "incomplete_full_slate_dataset",
            {
                "officialGameCount": dataset.get("officialGameCount"),
                "eligibleGameCount": dataset.get("eligibleGameCount"),
                "exactSlateCoverage": dataset.get("exactSlateCoverage"),
                "validSnapshotCount": dataset.get("validSnapshotCount"),
                "quarantinedSnapshotCount": dataset.get("quarantinedSnapshotCount"),
                "artifact": pointer,
            },
        )
        state["lastRejectedSlateError"] = "incomplete_full_slate_dataset"
        state["lastError"] = None
        return

    state.setdefault("completedSlates", []).append(
        {
            "slateDateEt": day,
            "officialGameCount": dataset["officialGameCount"],
            "eligibleGameCount": dataset["eligibleGameCount"],
            "fingerprint": dataset["fingerprint"],
            "artifact": pointer,
            "quarantinedSnapshotCount": dataset.get("quarantinedSnapshotCount", 0),
        }
    )
    state["eligibleGameCount"] = int(state.get("eligibleGameCount") or 0) + int(
        dataset["eligibleGameCount"]
    )
    state["completeSlateCount"] = int(state.get("completeSlateCount") or 0) + 1
    state["currentDate"] = handler._increment_day(day)
    state["currentSlotIndex"] = 0
    state["lastError"] = None
    state["lastCompletedQuarantineCount"] = int(
        dataset.get("quarantinedSnapshotCount") or 0
    )
    if int(state["eligibleGameCount"]) >= int(
        state.get("targetSettledGames") or handler.TARGET_GAMES
    ):
        state["phase"] = "OPTIMIZING"
        state["dataCollectionCompletedAtUtc"] = handler._now_iso()


def install() -> None:
    """Install the quarantine contract exactly once in the historical runtime."""

    if getattr(handler, "_quarantine_ledger_contract_installed", False):
        return
    handler.VERSION = HANDLER_VERSION
    handler._fetch_historical = _fetch_historical
    handler._complete_slate = _complete_slate
    optimizer.build_slate_dataset = build_slate_dataset
    handler.optimizer.build_slate_dataset = build_slate_dataset
    handler._quarantine_ledger_contract_installed = True
