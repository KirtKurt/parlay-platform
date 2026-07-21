from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_manual_pull
import inqsi_pull_history
import mlb_daily_lock_coverage_patch
import mlb_daily_pick_lock


class ConditionalCollision(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
        super().__init__("conditional collision")


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and key in self.items:
            raise ConditionalCollision()
        self.items[key] = copy.deepcopy(Item)
        return {}

    def get_item(self, Key, ConsistentRead=False):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item is not None else {}


def canonical_games():
    return [
        {
            "game_id": "provider-with-odds",
            "id": "provider-with-odds",
            "game_key": "mlb|2026-07-16|away one|home one",
            "commence_time": "2026-07-16T22:00:00+00:00",
            "home_team": "Home One",
            "away_team": "Away One",
            "provider_sport_key": "baseball_mlb",
            "books": {"fanduel": {"ml": {"home": -120, "away": 110}}},
        },
        {
            "game_id": "provider-no-odds",
            "id": "provider-no-odds",
            "game_key": "mlb|2026-07-16|away two|home two",
            "commence_time": "2026-07-16T23:00:00+00:00",
            "home_team": "Home Two",
            "away_team": "Away Two",
            "provider_sport_key": "baseball_mlb",
            "books": {},
        },
    ]


def test_bbs_shadow_capture_is_bound_to_canonical_pull_and_fails_soft(monkeypatch):
    calls = []

    class Adapter:
        @staticmethod
        def capture_shadow_slot(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("Authorization: Bearer must-never-leak")

    monkeypatch.setattr(mlb_manual_pull, "bbs_shadow", Adapter)
    compact = {
        "games": [
            {
                "game_id": "official-777001",
                "official_game_pk": 777001,
                "home_team": "Home One",
                "away_team": "Away One",
                "commence_time": "2026-07-22T23:05:00Z",
                "books": {},
            }
        ]
    }
    canonical = {
        "ok": True,
        "pull_id": "mlb_v1_slot",
        "providerManifestBound": True,
        "providerManifestFingerprint": "fingerprint",
    }

    result = mlb_manual_pull._capture_bbs_shadow_safe(
        game_date="2026-07-22",
        canonical=canonical,
        date_compact=compact,
    )

    assert len(calls) == 1
    assert calls[0]["canonical_pull"] is canonical
    assert calls[0]["official_games"][0]["official_game_pk"] == 777001
    assert result == {
        "ok": False,
        "status": "UNEXPECTED_SHADOW_CAPTURE_ERROR",
        "shadowOnly": True,
        "trainingEligible": False,
        "completenessCredit": False,
        "secretExposed": False,
    }
    assert "Bearer" not in str(result)


def test_canonical_history_returns_persisted_slot_binding_on_retry(monkeypatch):
    persisted_pull = {
        "pull_id": "first-slot-pull",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T17:01:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    }

    def fake_store(_body):
        return {
            "ok": True,
            "stored": {
                "pk": "PULLS#mlb#2026-07-16",
                "sk": "PULL#SLOT#2026-07-16T17:00:00+00:00",
                "pull_id": "first-slot-pull",
                "provider_manifest": {
                    "version": "manifest-v1",
                    "fingerprint": "manifest-fingerprint",
                    "game_count": 2,
                    "pk": "PROVIDER_MANIFEST#mlb#2026-07-16",
                    "sk": "SCHEDULE#manifest-fingerprint",
                    "immutable": True,
                    "full_provider_schedule": True,
                    "official_schedule_backed": False,
                },
            },
            "pull": copy.deepcopy(persisted_pull),
            "deduped": True,
            "canonicalSlot": {
                "slotStartUtc": "2026-07-16T17:00:00+00:00",
                "canonicalPullId": "first-slot-pull",
                "canonicalPulledAtUtc": "2026-07-16T17:01:00+00:00",
                "retryReturnedExistingCanonicalPull": True,
            },
        }

    monkeypatch.setattr(mlb_manual_pull.pull_history, "store_pull", fake_store)
    result = mlb_manual_pull._store_canonical_pull_history(
        game_date="2026-07-16",
        asof="2026-07-16T17:09:00+00:00",
        run="retry",
        compact={"games": canonical_games()},
    )

    assert result["ok"] is True
    assert result["pull_id"] == "first-slot-pull"
    assert result["canonicalPullId"] == "first-slot-pull"
    assert result["canonicalPulledAtUtc"] == "2026-07-16T17:01:00+00:00"
    assert result["canonicalSlotStartUtc"] == "2026-07-16T17:00:00+00:00"
    assert result["canonicalPullPk"] == "PULLS#mlb#2026-07-16"
    assert result["canonicalPullSk"] == (
        "PULL#SLOT#2026-07-16T17:00:00+00:00"
    )
    assert result["canonicalPullPayloadFingerprint"] == (
        inqsi_pull_history.pull_payload_fingerprint(persisted_pull)
    )
    assert result["retryReturnedExistingCanonicalPull"] is True


def official_backed_body(*, pull_id="official-full-manifest", pulled_at="2026-07-16T17:00:00+00:00"):
    schedule = mlb_manual_pull.official_schedule.validate_exact_date_schedule({
        "totalGames": 2,
        "dates": [{
            "date": "2026-07-16",
            "games": [
                {
                    "gamePk": 880001,
                    "gameDate": "2026-07-16T22:00:00Z",
                    "gameType": "R",
                    "teams": {
                        "home": {"team": {"name": "Home One"}},
                        "away": {"team": {"name": "Away One"}},
                    },
                },
                {
                    "gamePk": 880002,
                    "gameDate": "2026-07-16T23:00:00Z",
                    "gameType": "R",
                    "teams": {
                        "home": {"team": {"name": "Home Two"}},
                        "away": {"team": {"name": "Away Two"}},
                    },
                },
            ],
        }],
    }, "2026-07-16")
    reconciled, proof = mlb_manual_pull.official_schedule.reconcile_official_schedule(
        schedule,
        [
            {
                "id": "provider-with-odds",
                "commence_time": "2026-07-16T22:01:00Z",
                "home_team": "Home One",
                "away_team": "Away One",
                "bookmakers": [],
                "_provider_event_roster": True,
            },
        ],
        observed_at_utc=pulled_at,
    )
    compact = mlb_manual_pull._compact(reconciled, {"2026-07-16": proof})
    date_compact = mlb_manual_pull._compact_for_game_date(compact, "2026-07-16")
    return {
        "pull_id": pull_id,
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": pulled_at,
        "source": "the_odds_api",
        "games": mlb_manual_pull._canonical_games(date_compact),
        "meta": {
            "provider_roster": date_compact["provider_roster"],
            "official_schedule_authority": proof,
        },
    }


def test_provider_game_without_supported_odds_remains_in_slate_manifest():
    compact = mlb_manual_pull._compact([
        {
            "id": "provider-no-odds",
            "commence_time": "2026-07-16T23:00:00+00:00",
            "home_team": "Home Club",
            "away_team": "Away Club",
            "bookmakers": [],
        }
    ])

    assert compact["count"] == 1
    assert compact["game_dates_et"] == ["2026-07-16"]
    game = compact["games"][0]
    assert game["game_id"] == "provider-no-odds"
    assert game["books"] == {}
    assert game["odds_available"] is False
    assert game["moneyline_available"] is False
    assert game["markets_stored"] == []


def test_quota_free_event_roster_exact_id_merge_keeps_games_missing_from_odds():
    events = [
        {
            "id": "event-with-odds",
            "commence_time": "2026-07-16T22:00:00+00:00",
            "home_team": "Home One",
            "away_team": "Away One",
        },
        {
            "id": "event-without-odds",
            "commence_time": "2026-07-16T23:00:00+00:00",
            "home_team": "Home Two",
            "away_team": "Away Two",
        },
    ]
    odds_games = [
        {
            **events[0],
            "bookmakers": [
                {
                    "key": "fanduel",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Home One", "price": -120},
                                {"name": "Away One", "price": 110},
                            ],
                        }
                    ],
                }
            ],
        },
        {
            # Same teams are not sufficient: an odds-only id must not attach
            # to, or expand, the authoritative event roster.
            "id": "odds-only-different-id",
            "commence_time": events[1]["commence_time"],
            "home_team": events[1]["home_team"],
            "away_team": events[1]["away_team"],
            "bookmakers": [],
        },
    ]

    merged = mlb_manual_pull._merge_event_roster_with_odds(events, odds_games)
    compact = mlb_manual_pull._compact(merged)

    assert [game["id"] for game in merged] == [
        "event-with-odds",
        "event-without-odds",
    ]
    assert compact["count"] == 2
    by_id = {game["game_id"]: game for game in compact["games"]}
    assert by_id["event-with-odds"]["moneyline_available"] is True
    assert by_id["event-without-odds"]["books"] == {}
    assert by_id["event-without-odds"]["moneyline_available"] is False
    assert compact["provider_roster"] == {
        "source": "the_odds_api_events_exact_id_merge",
        "eventRosterCount": 2,
        "oddsPayloadCount": 1,
        "eventsWithoutOddsCount": 1,
        "oddsOnlyCount": 0,
        "exactProviderIdMerge": True,
        "quotaChargedForRosterRequest": False,
    }


def test_fetch_timestamps_only_after_provider_and_official_schedule_responses(monkeypatch):
    calls = []
    events = [
        {
            "id": "event-1",
            "commence_time": "2026-07-16T22:00:00+00:00",
            "home_team": "Home One",
            "away_team": "Away One",
        }
    ]

    def fake_get(url, timeout=20):
        calls.append(url)
        if "/events?" in url:
            assert timeout == 10
        return events if "/events?" in url else []

    monkeypatch.setattr(mlb_manual_pull, "ODDS_API_KEY", "test-key")
    monkeypatch.setattr(mlb_manual_pull, "_http_get_json", fake_get)
    schedule = mlb_manual_pull.official_schedule.validate_exact_date_schedule({
        "totalGames": 1,
        "dates": [{
            "date": "2026-07-16",
            "games": [{
                "gamePk": 777001,
                "gameDate": "2026-07-16T22:00:00Z",
                "gameType": "R",
                "teams": {
                    "home": {"team": {"name": "Home One"}},
                    "away": {"team": {"name": "Away One"}},
                },
            }],
        }],
    }, "2026-07-16")
    monkeypatch.setattr(
        mlb_manual_pull.official_schedule,
        "fetch_exact_date_schedule",
        lambda slate, timeout=12: calls.append("official-schedule") or schedule,
    )
    monkeypatch.setattr(mlb_manual_pull, "_now_iso", lambda: calls.append("timestamp") or "2026-07-16T20:00:00+00:00")

    merged, asof, authority_by_date = mlb_manual_pull._fetch_odds_with_completion_timestamp(
        "2026-07-16",
        0,
    )

    assert "/odds/?" in calls[0]
    assert "/events?" in calls[1]
    assert calls[2] == "official-schedule"
    assert calls[3] == "timestamp"
    assert asof == "2026-07-16T20:00:00+00:00"
    assert [row["id"] for row in merged] == ["event-1"]
    assert merged[0]["provider_event_id"] == "event-1"
    assert authority_by_date["2026-07-16"]["authoritativeStartTimes"] is True


def test_event_roster_merge_treats_provider_ids_as_opaque_and_fails_on_empty_roster():
    event = {
        "id": " event-1 ",
        "commence_time": "2026-07-16T22:00:00+00:00",
        "home_team": "Home One",
        "away_team": "Away One",
    }
    odds = [{**event, "id": "event-1", "bookmakers": [{"key": "fanduel", "markets": []}]}]

    merged = mlb_manual_pull._merge_event_roster_with_odds([event], odds)

    assert merged[0]["id"] == " event-1 "
    assert merged[0]["bookmakers"] == []
    with pytest.raises(RuntimeError, match="EVENTS_ROSTER_EMPTY_WHILE_ODDS_NONEMPTY"):
        mlb_manual_pull._merge_event_roster_with_odds([], odds)


def test_manual_pull_canonical_history_keeps_provider_game_without_odds():
    compact = {"games": canonical_games()}

    games = mlb_manual_pull._canonical_games(compact)

    assert [game["game_id"] for game in games] == [
        "provider-with-odds",
        "provider-no-odds",
    ]
    assert games[1]["books"] == {}
    assert games[1]["odds_available"] is False


def test_normalize_pull_builds_full_schedule_manifest_without_filtering_empty_books():
    normalized = inqsi_pull_history.normalize_pull({
        "pull_id": "pull-full-manifest",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T17:00:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    })

    assert normalized["ok"] is True
    pull = normalized["pull"]
    assert len(pull["games"]) == 2
    assert pull["games"][1]["books"] == {}
    manifest = pull["provider_schedule_manifest"]
    binding = pull["provider_manifest_binding"]
    assert manifest["version"] == inqsi_pull_history.PROVIDER_MANIFEST_VERSION
    assert manifest["gameCount"] == 2
    assert manifest["gameIdentities"] == [
        "provider-with-odds",
        "provider-no-odds",
    ]
    assert all("books" not in game for game in manifest["games"])
    assert manifest["fingerprint"] == inqsi_pull_history.provider_manifest_fingerprint(manifest)
    assert binding["fingerprint"] == manifest["fingerprint"]
    assert binding["immutable"] is True
    assert binding["fullProviderSchedule"] is True
    assert inqsi_pull_history.validate_provider_schedule_manifest(pull, "2026-07-16") == []


def test_official_schedule_proof_preserves_matched_provider_identity_and_falls_back_when_missing():
    normalized = inqsi_pull_history.normalize_pull(official_backed_body())

    assert normalized["ok"] is True
    pull = normalized["pull"]
    assert [game["game_id"] for game in pull["games"]] == [
        "provider-with-odds",
        "mlb_statsapi:880002",
    ]
    assert [game["commence_time"] for game in pull["games"]] == [
        "2026-07-16T22:00:00+00:00",
        "2026-07-16T23:00:00+00:00",
    ]
    manifest = pull["provider_schedule_manifest"]
    binding = pull["provider_manifest_binding"]
    assert manifest["scheduleAuthority"]["authoritativeStartTimes"] is True
    assert manifest["scheduleAuthority"]["providerStartDriftSeconds"] == [60]
    assert manifest["scheduleAuthority"]["missingProviderEventOfficialGameIds"] == ["880002"]
    assert manifest["games"][1]["official_game_pk"] == "880002"
    assert "provider_event_id" not in manifest["games"][1]
    assert binding["officialScheduleBacked"] is True
    assert binding["officialScheduleAuthorityFingerprint"] == manifest["scheduleAuthority"]["fingerprint"]
    assert inqsi_pull_history.validate_provider_schedule_manifest(pull, "2026-07-16") == []


def test_official_schedule_manifest_fingerprint_survives_dynamodb_numeric_roundtrip():
    from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

    pull = inqsi_pull_history.normalize_pull(official_backed_body())["pull"]
    wire = TypeSerializer().serialize(inqsi_pull_history.ddb_safe(pull))
    readback = TypeDeserializer().deserialize(wire)

    assert inqsi_pull_history.validate_provider_schedule_manifest(
        readback,
        "2026-07-16",
    ) == []


def test_equal_size_official_roster_outranks_legacy_during_migration(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(inqsi_pull_history, "PULLS", table)
    legacy = inqsi_pull_history.store_pull({
        "pull_id": "legacy-full-prestart",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T16:00:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    })["pull"]
    official = inqsi_pull_history.store_pull(
        official_backed_body(pulled_at="2026-07-16T17:00:00+00:00")
    )["pull"]

    resolved = inqsi_pull_history.verified_full_slate_manifest(
        [legacy, official],
        "2026-07-16",
    )

    assert resolved["fullSlateGameCount"] == 2
    assert resolved["fullAuthorityPullId"] == "official-full-manifest"
    assert resolved["rosterAuthorityMode"] == "MLB_STATS_API_EXACT_DATE"
    assert resolved["officialScheduleBacked"] is True
    assert resolved["officialScheduleAuthoritativeStartTimes"] is True


def test_store_pull_persists_independent_write_once_manifest_and_lock_reads_it(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(inqsi_pull_history, "PULLS", table)
    body = {
        "pull_id": "pull-full-manifest",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T17:00:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    }

    stored = inqsi_pull_history.store_pull(body)
    repeated = inqsi_pull_history.store_pull(body)

    assert stored["ok"] is True
    authority = stored["stored"]["provider_manifest"]
    assert authority["immutable"] is True
    assert authority["full_provider_schedule"] is True
    assert authority["game_count"] == 2
    assert authority["created"] is True
    assert repeated["stored"]["provider_manifest"]["created"] is False
    pull = stored["pull"]
    assert inqsi_pull_history.validate_provider_schedule_manifest(
        pull,
        "2026-07-16",
        verify_immutable_storage=True,
    ) == []

    lock_games = mlb_daily_pick_lock._latest_games_for_date("2026-07-16", [pull])
    assert [game["game_id"] for game in lock_games] == [
        "provider-with-odds",
        "provider-no-odds",
    ]
    # The scoreable game may be enriched from pull history, but the schedule
    # authority must still retain the provider-visible no-odds game.
    assert not (lock_games[1].get("books") or {})

    manifest_key = (authority["pk"], authority["sk"])
    table.items[manifest_key]["data"]["games"][0]["home_team"] = "Tampered Club"
    with pytest.raises(RuntimeError, match="MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID"):
        mlb_daily_pick_lock._latest_games_for_date("2026-07-16", [pull])


def test_verified_full_slate_manifest_survives_post_start_feed_contraction(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(inqsi_pull_history, "PULLS", table)
    full = inqsi_pull_history.store_pull({
        "pull_id": "full-prestart",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T20:00:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    })["pull"]
    contracted = inqsi_pull_history.store_pull({
        "pull_id": "contracted-after-game-one-start",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T22:30:00+00:00",
        "source": "the_odds_api",
        "games": [canonical_games()[1]],
    })["pull"]

    resolved = inqsi_pull_history.verified_full_slate_manifest(
        [full, contracted],
        "2026-07-16",
    )
    lock_games = mlb_daily_pick_lock._latest_games_for_date(
        "2026-07-16",
        [full, contracted],
    )
    history_calls = []

    class History:
        @staticmethod
        def verified_full_slate_manifest(pulls, slate):
            history_calls.append((pulls, slate))
            return inqsi_pull_history.verified_full_slate_manifest(pulls, slate)

    class Module:
        history = History()
        _game_date_et = staticmethod(mlb_daily_pick_lock._game_date_et)
        _parse_dt = staticmethod(mlb_daily_pick_lock._parse_dt)

    coverage_games = mlb_daily_lock_coverage_patch._latest_games(
        Module(),
        "2026-07-16",
        [full, contracted],
    )

    assert resolved["latestFeedContracted"] is True
    assert resolved["fullSlateGameCount"] == 2
    assert resolved["latestFeedGameCount"] == 1
    assert resolved["fullAuthorityPullId"] == "full-prestart"
    assert resolved["latestFeedPullId"] == "contracted-after-game-one-start"
    assert [game["game_id"] for game in lock_games] == [
        "provider-with-odds",
        "provider-no-odds",
    ]
    assert [game["game_id"] for game in coverage_games] == [
        "provider-with-odds",
        "provider-no-odds",
    ]
    assert len(history_calls) == 1


def test_verified_full_slate_manifest_quarantines_future_game_omission(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(inqsi_pull_history, "PULLS", table)
    full = inqsi_pull_history.store_pull({
        "pull_id": "full-prestart",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T20:00:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    })["pull"]
    invalid_subset = inqsi_pull_history.store_pull({
        "pull_id": "premature-subset",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T21:00:00+00:00",
        "source": "the_odds_api",
        "games": [canonical_games()[0]],
    })["pull"]

    resolved = inqsi_pull_history.verified_full_slate_manifest(
        [full, invalid_subset],
        "2026-07-16",
    )

    assert resolved["fullSlateGameCount"] == 2
    assert resolved["latestFeedGameCount"] == 1
    assert resolved["durableRosterPreservedDespiteLatestFeedAnomaly"] is True
    assert any(
        anomaly.get("type") == "LATEST_FEED_FUTURE_GAME_OMITTED"
        for anomaly in resolved["latestFeedAnomalies"]
    )


def test_same_day_migration_does_not_replace_larger_legacy_roster(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(inqsi_pull_history, "PULLS", table)
    legacy_full = inqsi_pull_history.store_pull({
        "pull_id": "legacy-full-prestart",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T20:00:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    })["pull"]
    first_events_pull = inqsi_pull_history.store_pull({
        "pull_id": "events-contracted-prestart",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T21:00:00+00:00",
        "source": "the_odds_api",
        "games": [canonical_games()[0]],
        "meta": {
            "provider_roster": {
                "source": "the_odds_api_events_exact_id_merge",
                "exactProviderIdMerge": True,
            }
        },
    })["pull"]

    resolved = inqsi_pull_history.verified_full_slate_manifest(
        [legacy_full, first_events_pull],
        "2026-07-16",
    )

    assert resolved["fullSlateGameCount"] == 2
    assert resolved["fullAuthorityPullId"] == "legacy-full-prestart"
    assert resolved["legacyMigrationFallback"] is True
    assert resolved["latestFeedEventRosterBacked"] is True
