from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from archive import InMemoryTennisArchive
from config import TennisConfig
from contracts import floor_to_slot, parse_utc, sorted_event_ids, utc_iso
from schedule_gate import evaluate_window, group_events_by_slate, upcoming_events
from signal_engine import build_feature_vector


PIPELINE_VERSION = "INQSI-TENNIS-COLLECTION-PIPELINE-v2-hardened"


def _latest_time(*values: Any, fallback: datetime) -> datetime:
    parsed = [
        value for value in (parse_utc(item) for item in values) if value is not None
    ]
    return max(parsed) if parsed else fallback


def _failed_tournament_keys(meta: Dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for row in meta.get("failedTournaments") or []:
        if isinstance(row, dict) and row.get("tournamentKey"):
            keys.add(str(row["tournamentKey"]))
        elif isinstance(row, str):
            keys.add(row)
    return keys


class TennisPipeline:
    def __init__(
        self,
        config: TennisConfig,
        provider: Any,
        store: Any,
        archive: Any = None,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.config = config
        self.provider = provider
        self.store = store
        # Production always injects S3ParquetTennisArchive. This deterministic
        # sink keeps local/unit construction lightweight without weakening the
        # Lambda wiring contract.
        self.archive = archive or InMemoryTennisArchive()
        self.clock = clock

    def _wall_received_at(self, current: datetime, explicit_now: bool) -> datetime:
        if self.clock is not None:
            return self.clock().astimezone(timezone.utc)
        if explicit_now:
            return current
        return datetime.now(timezone.utc)

    def run(
        self,
        now_utc: Optional[datetime] = None,
        *,
        slot_anchor_utc: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        explicit_now = now_utc is not None
        current = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        slot_anchor = (slot_anchor_utc or current).astimezone(timezone.utc)
        gate_evaluation_time = slot_anchor if slot_anchor_utc is not None else current
        slot = floor_to_slot(slot_anchor, self.config.pull_interval_minutes)
        slot_utc = utc_iso(slot)
        observed_at_utc = utc_iso(current)

        schedule, discovery = self.provider.discover_schedule(current)
        grouped = group_events_by_slate(schedule, self.config.slate_timezone)
        decisions: List[Dict[str, Any]] = []
        active: Dict[str, List[Dict[str, Any]]] = {}
        pending: Dict[str, List[Dict[str, Any]]] = {}
        completed_before: Dict[str, set[str]] = {}
        already_completed_slates: List[str] = []

        for slate_date, events in sorted(grouped.items()):
            state = self.store.get_window_state(slate_date)
            decision = evaluate_window(
                slate_date,
                events,
                gate_evaluation_time,
                lead_hours=self.config.lead_hours,
                interval_minutes=self.config.pull_interval_minutes,
                latched_state=state,
            )
            decisions.append(decision.as_dict())
            if decision.state == "COMPLETE":
                self.store.complete_window(slate_date, observed_at_utc)
                continue
            if decision.state != "ACTIVE":
                continue

            upcoming = upcoming_events(events, current)
            if not upcoming:
                self.store.complete_window(slate_date, observed_at_utc)
                continue
            starts = sorted(
                start
                for start in (parse_utc(event.get("commence_time")) for event in events)
                if start is not None
            )
            latest_first = utc_iso(starts[0]) if starts else decision.first_match_at_utc
            self.store.open_window(
                slate_date,
                first_match_at_utc=str(decision.first_match_at_utc),
                gate_open_at_utc=str(decision.gate_open_at_utc),
                opened_at_utc=observed_at_utc,
                latest_first_match_at_utc=str(latest_first),
            )

            if self.store.has_run_manifest(slate_date, slot_utc=slot_utc):
                already_completed_slates.append(slate_date)
                continue

            prior_keys = set(
                self.store.completed_tournament_keys(slate_date, slot_utc=slot_utc)
            )
            completed_before[slate_date] = prior_keys
            active[slate_date] = upcoming
            pending[slate_date] = [
                event
                for event in upcoming
                if str(event.get("tournament_key") or "") not in prior_keys
            ]
            for event in pending[slate_date]:
                commence = parse_utc(event.get("commence_time"))
                if commence is not None:
                    self.store.latch_event_cutoff(
                        str(event["event_id"]), utc_iso(commence), observed_at_utc
                    )

        # A previous attempt may have archived and checkpointed every paid
        # tournament, then failed while writing only the aggregate manifest.
        # Repair that marker from durable checkpoints without buying odds again.
        for slate_date, events in sorted(active.items()):
            if pending.get(slate_date):
                continue
            if self.store.has_run_manifest(slate_date, slot_utc=slot_utc):
                continue
            tournament_keys = {
                str(event.get("tournament_key") or "") for event in events
            } - {""}
            completed_keys = completed_before.get(slate_date, set())
            if not tournament_keys or not tournament_keys <= completed_keys:
                continue
            prior = self.store.get_run_manifest(slate_date, slot_utc=slot_utc) or {}
            decision = next(
                row for row in decisions if row["slate_date_et"] == slate_date
            )
            recovered = {
                **prior,
                "pipeline_version": PIPELINE_VERSION,
                "sport": "tennis",
                "slate_date_et": slate_date,
                "slot_utc": slot_utc,
                "observed_at_utc": observed_at_utc,
                "window": decision,
                "event_ids": sorted_event_ids(events),
                "event_count": len(events),
                "attempt_count": int(prior.get("attempt_count") or 1),
                "completed_tournament_keys": sorted(completed_keys),
                "pending_tournament_keys": [],
                "tournament_keys": sorted(tournament_keys),
                "complete": True,
                "completion_recovered_from_checkpoints": True,
                "model_state": self.config.model_state,
                "predictions_published": 0,
            }
            self.store.upsert_run_manifest(recovered, slot_utc=slot_utc)
            already_completed_slates.append(slate_date)

        pending = {date: rows for date, rows in pending.items() if rows}
        if not pending:
            return {
                "ok": True,
                "pipeline_version": PIPELINE_VERSION,
                "sport": "tennis",
                "mode": self.config.model_state,
                "run_status": (
                    "SLOT_ALREADY_COMPLETE"
                    if already_completed_slates
                    else "NO_ACTIVE_TENNIS_WINDOW"
                ),
                "slot_utc": slot_utc,
                "observed_at_utc": observed_at_utc,
                "schedule_discovery": discovery,
                "window_decisions": decisions,
                "already_completed_slates": already_completed_slates,
                "odds_endpoint_calls": 0,
                "retry_required": False,
                "slate_runs": [],
            }

        active_events: Dict[str, Dict[str, Any]] = {}
        for events in pending.values():
            for event in events:
                active_events[str(event["event_id"])] = dict(event)

        odds_by_event, odds_meta = self.provider.fetch_odds(active_events.values())
        wall_received = self._wall_received_at(current, explicit_now)
        safe_received_at = _latest_time(
            wall_received,
            odds_meta.get("fetchCompletedAtUtc"),
            fallback=wall_received,
        )
        run_observed_at_utc = utc_iso(safe_received_at)
        requested_keys = {
            str(event.get("tournament_key") or "") for event in active_events.values()
        } - {""}
        failed_keys = _failed_tournament_keys(odds_meta)
        explicit_success = {
            str(key) for key in (odds_meta.get("successfulTournamentKeys") or [])
        }
        successful_keys = (
            explicit_success
            if "successfulTournamentKeys" in odds_meta
            else requested_keys - failed_keys
        )
        empty_keys = {str(key) for key in (odds_meta.get("emptyTournamentKeys") or [])}

        slate_runs: List[Dict[str, Any]] = []
        retry_required = False
        for slate_date, events in sorted(active.items()):
            decision = next(
                row for row in decisions if row["slate_date_et"] == slate_date
            )
            prior_manifest = (
                self.store.get_run_manifest(slate_date, slot_utc=slot_utc) or {}
            )
            attempt = int(prior_manifest.get("attempt_count") or 0) + 1
            prior_completed = completed_before.get(slate_date, set())
            pending_events = pending.get(slate_date, [])
            by_tournament: Dict[str, List[Dict[str, Any]]] = {}
            for event in pending_events:
                key = str(event.get("tournament_key") or "")
                if key:
                    by_tournament.setdefault(key, []).append(event)

            stored_count = 0
            deduped_count = 0
            no_odds_count = 0
            started_during_fetch_count = 0
            feature_stored_count = 0
            feature_deduped_count = 0
            archive_failure_count = 0
            archive_receipts: List[Dict[str, Any]] = []
            features: List[Dict[str, Any]] = []
            completed_this_attempt: set[str] = set()
            covered_event_ids = {
                str(event_id)
                for event_id in (prior_manifest.get("covered_event_ids") or [])
                if str(event_id)
            }

            for tournament_key, tournament_events in sorted(by_tournament.items()):
                if tournament_key in failed_keys or (
                    tournament_key not in successful_keys
                    and tournament_key not in empty_keys
                ):
                    retry_required = True
                    features.extend(
                        {
                            "event_id": str(event["event_id"]),
                            "research_status": "PROVIDER_RETRY_REQUIRED",
                            "market_signal_score": None,
                            "grade": "PROVIDER_FAILURE",
                            "tags": ["PROVIDER_FAILURE"],
                            "selected_player": None,
                        }
                        for event in tournament_events
                    )
                    continue

                archive_rows: List[Dict[str, Any]] = []
                tournament_accepted = 0
                tournament_started = 0
                for scheduled in tournament_events:
                    event_id = str(scheduled["event_id"])
                    event = dict(odds_by_event.get(event_id) or scheduled)
                    event_received_at = _latest_time(
                        safe_received_at,
                        event.get("fetched_at_utc"),
                        fallback=safe_received_at,
                    )
                    schedule_commence = parse_utc(scheduled.get("commence_time"))
                    response_commence = parse_utc(event.get("commence_time"))
                    candidates = [
                        value
                        for value in (schedule_commence, response_commence)
                        if value is not None
                    ]
                    if not candidates:
                        cutoff = None
                    else:
                        candidate = min(candidates)
                        latched = self.store.latch_event_cutoff(
                            event_id, utc_iso(candidate), utc_iso(event_received_at)
                        )
                        cutoff = parse_utc(latched)

                    archive_status = "EMPTY_ODDS"
                    if cutoff is None or event_received_at >= cutoff:
                        tournament_started += 1
                        started_during_fetch_count += 1
                        archive_status = "PREMATCH_CUTOFF"
                        features.append(
                            {
                                "event_id": event_id,
                                "research_status": "EXCLUDED_STARTED_DURING_FETCH",
                                "market_signal_score": None,
                                "grade": "PREMATCH_CUTOFF",
                                "tags": ["PREMATCH_CUTOFF"],
                                "selected_player": None,
                            }
                        )
                    elif not (event.get("books") or {}):
                        no_odds_count += 1
                        reason_counts = (event.get("book_quality") or {}).get(
                            "rejection_counts"
                        ) or {}
                        archive_status = (
                            "ALL_BOOKS_REJECTED" if reason_counts else "EMPTY_ODDS"
                        )
                        features.append(
                            {
                                "event_id": event_id,
                                "research_status": "EMPTY_ODDS_RETRY_REQUIRED",
                                "market_signal_score": None,
                                "grade": "NO_USABLE_BOOKS",
                                "tags": [archive_status],
                                "selected_player": None,
                            }
                        )
                    else:
                        tournament_accepted += 1
                        archive_status = "ACCEPTED_PREMATCH"
                        event_observed_at_utc = utc_iso(event_received_at)
                        event["slate_date_et"] = slate_date
                        event["observed_at_utc"] = event_observed_at_utc
                        event["slot_utc"] = slot_utc
                        event["earliest_commence_at_utc"] = utc_iso(cutoff)
                        event["collection_policy"] = {
                            "interval_minutes": self.config.pull_interval_minutes,
                            "lead_hours": self.config.lead_hours,
                            "pregame_only": True,
                            "provider_event_id_is_identity": True,
                            "earliest_commence_is_latched": True,
                            "stale_books_are_rejected": True,
                        }
                        created = self.store.store_event_snapshot(
                            event,
                            slot_utc=slot_utc,
                            observed_at_utc=event_observed_at_utc,
                            slate_date_et=slate_date,
                        )
                        if created:
                            stored_count += 1
                        else:
                            deduped_count += 1
                        covered_event_ids.add(event_id)

                        rows = self.store.query_match_snapshots(event_id)
                        feature = build_feature_vector(
                            rows, event, event_received_at, self.config
                        )
                        feature_created = self.store.store_signal(
                            feature, slot_utc=slot_utc
                        )
                        if feature_created:
                            feature_stored_count += 1
                        else:
                            feature_deduped_count += 1
                        features.append(
                            {
                                "event_id": event_id,
                                "research_status": feature.get("research_status"),
                                "market_signal_score": feature.get(
                                    "market_signal_score"
                                ),
                                "grade": feature.get("grade"),
                                "tags": feature.get("tags"),
                                "selected_player": feature.get("selected_player"),
                            }
                        )

                    archive_rows.append(
                        {
                            "slate_date_et": slate_date,
                            "slot_utc": slot_utc,
                            "tournament_key": tournament_key,
                            "event_id": event_id,
                            "attempt": attempt,
                            "record_status": archive_status,
                            "observed_at_utc": utc_iso(event_received_at),
                            "payload": event,
                        }
                    )

                try:
                    receipt = self.archive.archive_tournament(
                        archive_rows,
                        slate_date_et=slate_date,
                        slot_utc=slot_utc,
                        tournament_key=tournament_key,
                        attempt=attempt,
                    )
                    archive_receipts.append(receipt)
                except Exception:
                    archive_failure_count += 1
                    retry_required = True
                    continue

                terminal_started = tournament_started == len(tournament_events)
                has_usable_market = tournament_accepted > 0
                if terminal_started or (
                    has_usable_market and tournament_key not in empty_keys
                ):
                    self.store.checkpoint_tournament(
                        slate_date,
                        slot_utc=slot_utc,
                        tournament_key=tournament_key,
                        observed_at_utc=run_observed_at_utc,
                        archive_receipt=receipt,
                    )
                    completed_this_attempt.add(tournament_key)
                else:
                    retry_required = True

            all_tournament_keys = {
                str(event.get("tournament_key") or "") for event in events
            } - {""}
            completed_keys = prior_completed | completed_this_attempt
            complete = (
                bool(all_tournament_keys) and all_tournament_keys <= completed_keys
            )
            if not complete:
                retry_required = True

            manifest = {
                "pipeline_version": PIPELINE_VERSION,
                "sport": "tennis",
                "slate_date_et": slate_date,
                "slot_utc": slot_utc,
                "observed_at_utc": run_observed_at_utc,
                "window": decision,
                "event_ids": sorted_event_ids(events),
                "event_count": len(events),
                "covered_event_ids": sorted(covered_event_ids),
                "covered_event_count": len(covered_event_ids),
                "snapshot_created_count": stored_count,
                "snapshot_deduped_count": deduped_count,
                "events_without_odds_count": no_odds_count,
                "events_started_during_fetch_count": started_during_fetch_count,
                "feature_created_count": feature_stored_count,
                "feature_deduped_count": feature_deduped_count,
                "archive_failure_count": archive_failure_count,
                "archive_receipts": archive_receipts,
                "attempt_count": attempt,
                "completed_tournament_keys": sorted(completed_keys),
                "pending_tournament_keys": sorted(all_tournament_keys - completed_keys),
                "failed_tournament_keys": sorted(failed_keys & all_tournament_keys),
                "tournament_keys": sorted(all_tournament_keys),
                "complete": complete,
                "model_state": self.config.model_state,
                "predictions_published": 0,
            }
            self.store.upsert_run_manifest(manifest, slot_utc=slot_utc)
            slate_runs.append({**manifest, "feature_summaries": features})

        return {
            "ok": not retry_required,
            "pipeline_version": PIPELINE_VERSION,
            "sport": "tennis",
            "mode": self.config.model_state,
            "run_status": "PARTIAL_RETRY_REQUIRED" if retry_required else "PULL_STORED",
            "slot_utc": slot_utc,
            "observed_at_utc": run_observed_at_utc,
            "schedule_discovery": discovery,
            "window_decisions": decisions,
            "already_completed_slates": already_completed_slates,
            "odds_meta": odds_meta,
            "odds_endpoint_calls": odds_meta.get(
                "oddsCalls", odds_meta.get("tournamentOddsCalls", 0)
            ),
            "retry_required": retry_required,
            "slate_runs": slate_runs,
        }
