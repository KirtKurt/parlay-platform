from __future__ import annotations

from datetime import datetime, timezone

from config import TennisConfig
from pipeline import TennisPipeline
from storage import InMemoryTennisStore


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def config() -> TennisConfig:
    return TennisConfig(
        odds_api_key="test-key",
        snapshots_table="snapshots",
        signals_table="signals",
    )


def scheduled_event(event_id: str, tournament: str, start: str):
    return {
        "event_id": event_id,
        "sport": "tennis",
        "player_a": f"{event_id} Player A",
        "player_b": f"{event_id} Player B",
        "commence_time": start,
        "slate_date_et": "2026-07-22",
        "tournament_key": tournament,
        "tournament_title": tournament,
        "tour": "ATP" if "atp" in tournament else "WTA",
        "discipline": "singles",
    }


class FakeProvider:
    def __init__(self, events):
        self.events = events
        self.fetch_count = 0
        self.move = 0

    def discover_schedule(self, now_utc):
        return list(self.events), {
            "eventCount": len(self.events),
            "scheduleCalls": 3,
            "oddsCalls": 0,
        }

    def fetch_odds(self, events):
        self.fetch_count += 1
        self.move += 10
        rows = {}
        for event in events:
            rows[event["event_id"]] = {
                **event,
                "books": {
                    "fanduel": {
                        "player_a": -110 - self.move,
                        "player_b": -110 + self.move,
                    },
                    "draftkings": {
                        "player_a": -108 - self.move,
                        "player_b": -112 + self.move,
                    },
                    "fanatics": {
                        "player_a": -112 - self.move,
                        "player_b": -108 + self.move,
                    },
                },
            }
        return rows, {"tournamentOddsCalls": len({e["tournament_key"] for e in events})}


class FailingOddsProvider(FakeProvider):
    def fetch_odds(self, events):
        self.fetch_count += 1
        raise RuntimeError("transient odds failure")


class StartCrossingProvider(FakeProvider):
    def fetch_odds(self, events):
        rows, meta = super().fetch_odds(events)
        for row in rows.values():
            row["fetched_at_utc"] = "2026-07-22T18:00:01+00:00"
        meta["fetchCompletedAtUtc"] = "2026-07-22T18:00:01+00:00"
        return rows, meta


class EarlierStartRaceProvider(FakeProvider):
    def fetch_odds(self, events):
        rows, meta = super().fetch_odds(events)
        for row in rows.values():
            row["schedule_commence_time"] = row["commence_time"]
            row["odds_commence_time"] = "2026-07-22T18:00:00+00:00"
            row["commence_time"] = "2026-07-22T18:00:00+00:00"
            row["fetched_at_utc"] = "2026-07-22T18:05:00+00:00"
        meta["fetchCompletedAtUtc"] = "2026-07-22T18:05:00+00:00"
        return rows, meta


class EmptyOddsProvider(FakeProvider):
    def fetch_odds(self, events):
        self.fetch_count += 1
        rows = {
            event["event_id"]: {
                **event,
                "books": {},
                "fetched_at_utc": "2026-07-22T10:00:01+00:00",
            }
            for event in events
        }
        keys = sorted({event["tournament_key"] for event in events})
        return rows, {
            "tournamentOddsCalls": len(keys),
            "oddsCalls": len(keys),
            "successfulTournamentKeys": [],
            "emptyTournamentKeys": keys,
            "failedTournaments": [],
            "fetchCompletedAtUtc": "2026-07-22T10:00:01+00:00",
        }


class PartialTournamentProvider(FakeProvider):
    def __init__(self, events):
        super().__init__(events)
        self.requested_tournaments = []

    def fetch_odds(self, events):
        rows = list(events)
        keys = sorted({event["tournament_key"] for event in rows})
        self.requested_tournaments.append(keys)
        self.fetch_count += 1
        if self.fetch_count == 1:
            successful = ["tennis_atp_good"]
            failed = [
                {
                    "tournamentKey": "tennis_wta_retry",
                    "errorCode": "provider_request_failed",
                    "attempted": True,
                }
            ]
        else:
            successful = keys
            failed = []
        result = {}
        for event in rows:
            if event["tournament_key"] not in successful:
                result[event["event_id"]] = {**event, "books": {}}
                continue
            result[event["event_id"]] = {
                **event,
                "books": {
                    "fanduel": {"player_a": -110, "player_b": -110},
                    "draftkings": {"player_a": -108, "player_b": -112},
                },
            }
        return result, {
            "tournamentOddsCalls": len(keys),
            "oddsCalls": len(keys),
            "successfulTournamentKeys": successful,
            "emptyTournamentKeys": [],
            "failedTournaments": failed,
            "fetchCompletedAtUtc": "2026-07-22T10:00:01+00:00",
        }


def test_closed_gate_makes_zero_odds_calls():
    provider = FakeProvider(
        [scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:07:00+00:00")]
    )
    pipeline = TennisPipeline(config(), provider, InMemoryTennisStore())

    report = pipeline.run(dt("2026-07-22T09:45:00+00:00"))

    assert report["run_status"] == "NO_ACTIVE_TENNIS_WINDOW"
    assert report["odds_endpoint_calls"] == 0
    assert provider.fetch_count == 0


def test_active_gate_aggregates_tournaments_and_builds_shadow_features():
    events = [
        scheduled_event("event-atp", "tennis_atp_test", "2026-07-22T18:00:00+00:00"),
        scheduled_event("event-wta", "tennis_wta_test", "2026-07-22T19:00:00+00:00"),
    ]
    provider = FakeProvider(events)
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(config(), provider, store)

    first = pipeline.run(dt("2026-07-22T10:00:00+00:00"))
    second = pipeline.run(dt("2026-07-22T10:15:00+00:00"))

    assert first["run_status"] == "PULL_STORED"
    assert first["slate_runs"][0]["event_count"] == 2
    assert set(first["slate_runs"][0]["event_ids"]) == {"event-atp", "event-wta"}
    assert len(store.snapshots) == 4
    assert len(store.runs) == 2
    assert provider.fetch_count == 2
    assert all(item["sport"] == "tennis" for item in store.snapshots.values())
    summaries = second["slate_runs"][0]["feature_summaries"]
    assert all(row["market_signal_score"] is not None for row in summaries)
    assert second["slate_runs"][0]["predictions_published"] == 0


def test_provider_event_id_survives_a_start_time_change():
    store = InMemoryTennisStore()
    original = scheduled_event(
        "stable-id", "tennis_atp_test", "2026-07-22T18:00:00+00:00"
    )
    revised = {**original, "commence_time": "2026-07-22T19:00:00+00:00"}

    store.store_event_snapshot(
        original,
        slot_utc="2026-07-22T10:00:00+00:00",
        observed_at_utc="2026-07-22T10:00:05+00:00",
        slate_date_et="2026-07-22",
    )
    store.store_event_snapshot(
        revised,
        slot_utc="2026-07-22T10:15:00+00:00",
        observed_at_utc="2026-07-22T10:15:05+00:00",
        slate_date_et="2026-07-22",
    )

    rows = store.query_match_snapshots("stable-id")
    assert len(rows) == 2
    assert {row["event_id"] for row in rows} == {"stable-id"}


def test_window_is_latched_before_a_transient_odds_failure():
    events = [
        scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:00:00+00:00")
    ]
    provider = FailingOddsProvider(events)
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(config(), provider, store)

    try:
        pipeline.run(dt("2026-07-22T10:00:00+00:00"))
    except RuntimeError as exc:
        assert "transient odds failure" in str(exc)
    else:
        raise AssertionError("expected the provider failure to be raised")

    state = store.get_window_state("2026-07-22")
    assert state["window_state"] == "ACTIVE"
    assert state["opened_at_utc"] == "2026-07-22T10:00:00+00:00"


def test_event_that_starts_during_fetch_is_not_stored_as_prematch():
    events = [
        scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:00:00+00:00")
    ]
    provider = StartCrossingProvider(events)
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(config(), provider, store)

    report = pipeline.run(dt("2026-07-22T17:59:50+00:00"))

    run = report["slate_runs"][0]
    assert run["events_started_during_fetch_count"] == 1
    assert run["snapshot_created_count"] == 0
    assert not store.snapshots
    assert not store.features
    assert run["feature_summaries"][0]["research_status"] == (
        "EXCLUDED_STARTED_DURING_FETCH"
    )


def test_earlier_start_in_odds_response_blocks_stale_schedule_leakage():
    events = [
        scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:10:00+00:00")
    ]
    provider = EarlierStartRaceProvider(events)
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(config(), provider, store)

    report = pipeline.run(dt("2026-07-22T17:59:00+00:00"))

    run = report["slate_runs"][0]
    assert run["events_started_during_fetch_count"] == 1
    assert run["snapshot_created_count"] == 0
    assert not store.snapshots


def test_same_slot_retry_is_idempotent_in_storage():
    events = [
        scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:00:00+00:00")
    ]
    provider = FakeProvider(events)
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(config(), provider, store)

    first = pipeline.run(dt("2026-07-22T10:00:00+00:00"))
    retry = pipeline.run(dt("2026-07-22T10:00:30+00:00"))

    assert len(store.snapshots) == 1
    assert len(store.features) == 1
    assert len(store.runs) == 1
    assert first["slate_runs"][0]["snapshot_created_count"] == 1
    assert retry["run_status"] == "SLOT_ALREADY_COMPLETE"
    assert retry["already_completed_slates"] == ["2026-07-22"]
    assert retry["slate_runs"] == []
    assert provider.fetch_count == 1


def test_eventbridge_slot_anchor_survives_a_delayed_invocation():
    events = [
        scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:00:00+00:00")
    ]
    pipeline = TennisPipeline(config(), FakeProvider(events), InMemoryTennisStore())

    report = pipeline.run(
        dt("2026-07-22T10:14:00+00:00"),
        slot_anchor_utc=dt("2026-07-22T10:00:00+00:00"),
    )

    assert report["slot_utc"] == "2026-07-22T10:00:00+00:00"


def test_delayed_pre_gate_retry_does_not_create_an_early_slot():
    events = [
        scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:07:00+00:00")
    ]
    provider = FakeProvider(events)
    pipeline = TennisPipeline(config(), provider, InMemoryTennisStore())

    report = pipeline.run(
        dt("2026-07-22T10:15:00+00:00"),
        slot_anchor_utc=dt("2026-07-22T10:00:00+00:00"),
    )

    assert report["run_status"] == "NO_ACTIVE_TENNIS_WINDOW"
    assert report["odds_endpoint_calls"] == 0
    assert provider.fetch_count == 0


def test_all_empty_response_remains_retryable_and_writes_no_canonical_rows():
    events = [
        scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:00:00+00:00")
    ]
    provider = EmptyOddsProvider(events)
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(config(), provider, store)

    first = pipeline.run(dt("2026-07-22T10:00:00+00:00"))
    retry = pipeline.run(dt("2026-07-22T10:00:30+00:00"))

    assert first["run_status"] == "PARTIAL_RETRY_REQUIRED"
    assert first["retry_required"] is True
    assert first["slate_runs"][0]["complete"] is False
    assert retry["run_status"] == "PARTIAL_RETRY_REQUIRED"
    assert provider.fetch_count == 2
    assert store.snapshots == {}
    assert store.features == {}
    assert len(pipeline.archive.rows) == 2
    assert store.runs[("2026-07-22", "2026-07-22T10:00:00+00:00")]["attempt_count"] == 2


def test_same_slot_retry_fetches_only_the_failed_tournament():
    events = [
        scheduled_event("event-good", "tennis_atp_good", "2026-07-22T18:00:00+00:00"),
        scheduled_event("event-retry", "tennis_wta_retry", "2026-07-22T19:00:00+00:00"),
    ]
    provider = PartialTournamentProvider(events)
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(config(), provider, store)

    first = pipeline.run(dt("2026-07-22T10:00:00+00:00"))
    second = pipeline.run(dt("2026-07-22T10:00:30+00:00"))

    assert first["run_status"] == "PARTIAL_RETRY_REQUIRED"
    assert second["run_status"] == "PULL_STORED"
    assert provider.requested_tournaments == [
        ["tennis_atp_good", "tennis_wta_retry"],
        ["tennis_wta_retry"],
    ]
    assert first["slate_runs"][0]["covered_event_count"] == 1
    assert second["slate_runs"][0]["covered_event_count"] == 2
    assert set(store.snapshots) == {
        ("event-good", "2026-07-22T10:00:00+00:00"),
        ("event-retry", "2026-07-22T10:00:00+00:00"),
    }
    assert store.has_run_manifest("2026-07-22", slot_utc="2026-07-22T10:00:00+00:00")


def test_later_reschedule_never_relaxes_the_earliest_seen_cutoff():
    original = scheduled_event(
        "event-1", "tennis_atp_test", "2026-07-22T18:00:00+00:00"
    )
    provider = FakeProvider([original])
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(config(), provider, store)

    first = pipeline.run(dt("2026-07-22T10:00:00+00:00"))
    assert first["run_status"] == "PULL_STORED"

    provider.events = [{**original, "commence_time": "2026-07-22T19:00:00+00:00"}]
    second = pipeline.run(dt("2026-07-22T18:05:00+00:00"))

    assert second["slate_runs"][0]["events_started_during_fetch_count"] == 1
    assert ("event-1", "2026-07-22T18:00:00+00:00") not in store.snapshots
    assert store.event_states["event-1"]["earliest_commence_at_utc"] == (
        "2026-07-22T18:00:00+00:00"
    )


def test_wall_clock_received_after_start_beats_forged_early_provider_time():
    event = scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:00:00+00:00")
    provider = FakeProvider([event])
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(
        config(),
        provider,
        store,
        clock=lambda: dt("2026-07-22T18:00:01+00:00"),
    )

    report = pipeline.run(dt("2026-07-22T17:59:50+00:00"))

    assert report["slate_runs"][0]["events_started_during_fetch_count"] == 1
    assert store.snapshots == {}


def test_archive_failure_prevents_completion_marker():
    class FailingArchive:
        def archive_tournament(self, *args, **kwargs):
            raise RuntimeError("archive unavailable")

    event = scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:00:00+00:00")
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(config(), FakeProvider([event]), store, FailingArchive())

    report = pipeline.run(dt("2026-07-22T10:00:00+00:00"))

    assert report["run_status"] == "PARTIAL_RETRY_REQUIRED"
    assert report["slate_runs"][0]["archive_failure_count"] == 1
    assert report["slate_runs"][0]["complete"] is False
    assert not store.has_run_manifest(
        "2026-07-22", slot_utc="2026-07-22T10:00:00+00:00"
    )


def test_archived_checkpoint_repairs_missing_manifest_without_new_paid_call():
    event = scheduled_event("event-1", "tennis_atp_test", "2026-07-22T18:00:00+00:00")
    provider = FakeProvider([event])
    store = InMemoryTennisStore()
    pipeline = TennisPipeline(config(), provider, store)
    slot = "2026-07-22T10:00:00+00:00"

    assert pipeline.run(dt(slot))["run_status"] == "PULL_STORED"
    del store.runs[("2026-07-22", slot)]

    recovered = pipeline.run(dt("2026-07-22T10:00:30+00:00"))

    assert recovered["run_status"] == "SLOT_ALREADY_COMPLETE"
    assert provider.fetch_count == 1
    assert (
        store.runs[("2026-07-22", slot)]["completion_recovered_from_checkpoints"]
        is True
    )
