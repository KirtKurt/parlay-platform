from __future__ import annotations

import urllib.error
from io import BytesIO
from datetime import datetime, timezone

import pytest

import provider as provider_module
from config import TennisConfig
from provider import OddsApiTennisProvider, TennisProviderError


def config(*, include_doubles: bool = False) -> TennisConfig:
    return TennisConfig(
        odds_api_key="test-key",
        snapshots_table="snapshots",
        signals_table="signals",
        include_doubles=include_doubles,
    )


def test_tournament_discovery_is_exact_active_non_outright_tennis_only():
    rows = [
        {
            "key": "tennis_atp_test",
            "title": "ATP Test",
            "group": "Tennis",
            "active": True,
            "has_outrights": False,
        },
        {
            "key": "tennis_wta_test_doubles",
            "title": "WTA Test Doubles",
            "group": "Tennis",
            "active": True,
            "has_outrights": False,
        },
        {
            "key": "tennis_atp_winner",
            "title": "ATP Winner",
            "group": "Tennis",
            "active": True,
            "has_outrights": True,
        },
        {
            "key": "basketball_nba",
            "title": "NBA",
            "group": "Basketball",
            "active": True,
            "has_outrights": False,
        },
    ]
    provider = OddsApiTennisProvider(config())
    provider._get_json = lambda *args, **kwargs: (rows, {})

    tournaments = provider.discover_tournaments()

    assert [row["key"] for row in tournaments] == ["tennis_atp_test"]
    assert tournaments[0]["tour"] == "ATP"
    assert tournaments[0]["discipline"] == "singles"


def test_doubles_can_be_enabled_without_mixing_the_discipline_label():
    rows = [
        {
            "key": "tennis_wta_test_doubles",
            "title": "WTA Test Doubles",
            "group": "Tennis",
            "active": True,
            "has_outrights": False,
        }
    ]
    provider = OddsApiTennisProvider(config(include_doubles=True))
    provider._get_json = lambda *args, **kwargs: (rows, {})

    tournaments = provider.discover_tournaments()

    assert tournaments[0]["tour"] == "WTA"
    assert tournaments[0]["discipline"] == "doubles"


def test_schedule_discovery_fails_closed_if_one_tournament_fails():
    provider = OddsApiTennisProvider(config())
    tournaments = [
        {
            "key": "tennis_atp_good",
            "title": "ATP Good",
            "tour": "ATP",
            "discipline": "singles",
        },
        {
            "key": "tennis_wta_bad",
            "title": "WTA Bad",
            "tour": "WTA",
            "discipline": "singles",
        },
    ]
    provider.discover_tournaments = lambda: tournaments

    def fake_get(path, params, *, request_kind):
        if "wta_bad" in path:
            raise TennisProviderError(
                "provider_request_failed:https://secret.invalid?apiKey=test-key:body"
            )
        return [], {"fetchedAtUtc": "2026-07-22T10:00:00+00:00"}

    provider._get_json = fake_get

    with pytest.raises(TennisProviderError) as raised:
        provider.discover_schedule(
            datetime.fromisoformat("2026-07-22T10:00:00+00:00").astimezone(timezone.utc)
        )

    assert str(raised.value) == "schedule_discovery_incomplete"
    assert "test-key" not in str(raised.value)
    assert "secret.invalid" not in str(raised.value)


def _event(event_id: str, tournament_key: str) -> dict:
    return {
        "event_id": event_id,
        "tournament_key": tournament_key,
        "player_a": "A",
        "player_b": "B",
        "commence_time": "2026-07-22T18:00:00+00:00",
    }


def _book(book_key: str, last_update: object) -> dict:
    market = {
        "key": "h2h",
        "outcomes": [
            {"name": "A", "price": -120},
            {"name": "B", "price": 110},
        ],
    }
    if last_update is not None:
        market["last_update"] = last_update
    return {"key": book_key, "markets": [market]}


def _raw_odds(event_id: str, *books: dict) -> dict:
    return {
        "id": event_id,
        "commence_time": "2026-07-22T18:00:00+00:00",
        "bookmakers": list(books),
    }


def _usage(*, remaining: int = 1_000, cost: int = 1) -> dict:
    return {
        "requestsRemaining": remaining,
        "requestsUsed": 10,
        "lastRequestCost": cost,
        "fetchedAtUtc": "2026-07-22T10:00:00+00:00",
    }


def test_odds_pull_preserves_successes_and_classifies_failure_and_empty():
    provider = OddsApiTennisProvider(config())
    events = [
        _event("event-good", "a_good"),
        _event("event-empty", "b_empty"),
        _event("event-bad", "c_bad"),
    ]

    def fake_get(path, params, *, request_kind):
        if "c_bad" in path:
            raise TennisProviderError(
                "provider_request_failed:https://secret.invalid?apiKey=test-key:body"
            )
        if "b_empty" in path:
            return [], _usage()
        return [
            _raw_odds("event-good", _book("valid", "2026-07-22T09:59:00Z"))
        ], _usage()

    provider._get_json = fake_get

    rows, meta = provider.fetch_odds(events)

    assert set(rows) == {"event-good", "event-empty", "event-bad"}
    assert set(rows["event-good"]["books"]) == {"valid"}
    assert rows["event-empty"]["books"] == {}
    assert rows["event-bad"]["books"] == {}
    assert meta["successfulTournamentKeys"] == ["a_good"]
    assert meta["emptyTournamentKeys"] == ["b_empty"]
    assert meta["failedTournaments"] == [
        {
            "tournamentKey": "c_bad",
            "errorCode": "provider_request_failed",
            "attempted": True,
        }
    ]
    assert meta["tournamentOddsCalls"] == 3
    assert meta["oddsCalls"] == 3
    assert "test-key" not in repr(meta)
    assert "secret.invalid" not in repr(meta)
    assert ":body" not in repr(meta)


def test_book_last_update_gate_reports_exact_per_event_rejection_counts():
    cfg = config()
    object.__setattr__(cfg, "max_book_price_age_seconds", 900)
    object.__setattr__(cfg, "max_book_future_skew_seconds", 60)
    provider = OddsApiTennisProvider(cfg)
    event = _event("event-1", "a_tournament")
    raw = _raw_odds(
        "event-1",
        _book("valid", "2026-07-22T09:59:00Z"),
        _book("missing", None),
        _book("invalid", "not-a-time"),
        _book("stale", "2026-07-22T09:44:59Z"),
        _book("future", "2026-07-22T10:01:01Z"),
        _book("at_start", "2026-07-22T18:00:00Z"),
    )
    provider._get_json = lambda *args, **kwargs: ([raw], _usage())

    rows, meta = provider.fetch_odds([event])

    normalized = rows["event-1"]
    assert set(normalized["books"]) == {"valid"}
    assert normalized["book_rejections"] == {
        "at_start": "last_update_at_or_after_start",
        "future": "future_skewed_last_update",
        "invalid": "invalid_last_update",
        "missing": "missing_last_update",
        "stale": "stale_last_update",
    }
    expected_counts = {
        "future_skewed_last_update": 1,
        "invalid_last_update": 1,
        "last_update_at_or_after_start": 1,
        "missing_last_update": 1,
        "stale_last_update": 1,
    }
    assert normalized["book_rejection_reason_counts"] == expected_counts
    assert normalized["book_quality"] == {
        "accepted_book_count": 1,
        "rejected_book_count": 5,
        "rejection_counts": expected_counts,
        "rejections_by_book": normalized["book_rejections"],
    }
    assert meta["bookRejectionCountsByEvent"] == {"event-1": expected_counts}
    assert meta["bookRejectionReasonCounts"] == expected_counts


def test_book_timestamp_age_and_future_skew_boundaries_are_inclusive():
    scheduled = _event("event-boundary", "a_tournament")
    raw = _raw_odds(
        "event-boundary",
        _book("age_boundary", "2026-07-22T09:45:00Z"),
        _book("future_boundary", "2026-07-22T10:01:00Z"),
    )

    normalized = OddsApiTennisProvider._normalize_odds_event(
        raw,
        scheduled,
        fetched_at_utc="2026-07-22T10:00:00Z",
        max_book_price_age_seconds=900,
        max_book_future_skew_seconds=60,
    )

    assert set(normalized["books"]) == {"age_boundary", "future_boundary"}
    assert normalized["book_rejections"] == {}


@pytest.mark.parametrize("bad_price", ["N/A", 0, 99, -99, float("inf"), True])
def test_invalid_american_prices_cannot_make_a_tournament_successful(bad_price):
    provider = OddsApiTennisProvider(config())
    event = _event("event-invalid-price", "a_tournament")
    book = _book("invalid_price", "2026-07-22T09:59:00Z")
    book["markets"][0]["outcomes"][0]["price"] = bad_price
    provider._get_json = lambda *args, **kwargs: (
        [_raw_odds("event-invalid-price", book)],
        _usage(),
    )

    rows, meta = provider.fetch_odds([event])

    assert rows["event-invalid-price"]["books"] == {}
    assert rows["event-invalid-price"]["book_rejections"] == {
        "invalid_price": "invalid_american_price"
    }
    assert meta["successfulTournamentKeys"] == []
    assert meta["emptyTournamentKeys"] == ["a_tournament"]


def test_quota_reserve_stops_new_calls_without_losing_prior_success():
    cfg = config()
    object.__setattr__(cfg, "quota_protected_reserve", 250)
    object.__setattr__(cfg, "quota_daily_budget", 20)
    provider = OddsApiTennisProvider(cfg)
    calls = []
    events = [
        _event("event-a", "a_probe"),
        _event("event-b", "b_skipped"),
        _event("event-c", "c_skipped"),
    ]

    def fake_get(path, params, *, request_kind):
        calls.append(path)
        return [_raw_odds("event-a", _book("valid", "2026-07-22T09:59:00Z"))], _usage(
            remaining=250
        )

    provider._get_json = fake_get

    rows, meta = provider.fetch_odds(events)

    assert len(calls) == 1
    assert set(rows["event-a"]["books"]) == {"valid"}
    assert rows["event-b"]["books"] == {}
    assert rows["event-c"]["books"] == {}
    assert meta["successfulTournamentKeys"] == ["a_probe"]
    assert meta["emptyTournamentKeys"] == []
    assert meta["failedTournaments"] == [
        {
            "tournamentKey": "b_skipped",
            "errorCode": "quota_protected_reserve",
            "attempted": False,
        },
        {
            "tournamentKey": "c_skipped",
            "errorCode": "quota_protected_reserve",
            "attempted": False,
        },
    ]
    assert meta["oddsCalls"] == 1
    assert meta["quotaStatus"] == {
        "attemptedOddsCalls": 1,
        "estimatedAttemptCost": 1,
        "requestsRemaining": 250,
        "remainingAfterAttempt": 250,
        "reportedRequestsRemaining": 250,
        "protectedReserve": 250,
        "protectedReserveReached": True,
        "projectedReserveBreach": True,
        "dailyBudget": 20,
        "forecastPullsPerDay": 96,
        "projectedDailyOddsCalls": 288,
        "projectedDailyRequestCost": 288,
        "dailyBudgetExceeded": True,
        "quotaHeadersRequired": True,
        "quotaHeaderMissingTournamentKeys": [],
        "quotaSkippedTournamentKeys": ["b_skipped", "c_skipped"],
    }


def test_required_missing_quota_header_fails_closed_after_one_attempt():
    cfg = config()
    object.__setattr__(cfg, "require_quota_headers", True)
    provider = OddsApiTennisProvider(cfg)
    calls = []
    events = [
        _event("event-a", "a_missing_header"),
        _event("event-b", "b_not_attempted"),
    ]

    def fake_get(path, params, *, request_kind):
        calls.append(path)
        usage = _usage()
        usage["requestsRemaining"] = None
        return [_raw_odds("event-a", _book("valid", "2026-07-22T09:59:00Z"))], usage

    provider._get_json = fake_get

    rows, meta = provider.fetch_odds(events)

    assert len(calls) == 1
    assert rows["event-a"]["books"] == {}
    assert rows["event-b"]["books"] == {}
    assert meta["successfulTournamentKeys"] == []
    assert meta["emptyTournamentKeys"] == []
    assert meta["failedTournaments"] == [
        {
            "tournamentKey": "a_missing_header",
            "errorCode": "quota_headers_missing",
            "attempted": True,
        },
        {
            "tournamentKey": "b_not_attempted",
            "errorCode": "quota_status_unavailable",
            "attempted": False,
        },
    ]
    assert meta["quotaStatus"]["quotaHeaderMissingTournamentKeys"] == [
        "a_missing_header"
    ]
    assert meta["quotaStatus"]["quotaSkippedTournamentKeys"] == ["b_not_attempted"]


def test_http_error_is_sanitized_and_failed_odds_attempt_is_counted(monkeypatch):
    provider = OddsApiTennisProvider(config())
    leaked_url = "https://secret.invalid/path?apiKey=test-key"

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            leaked_url,
            401,
            "secret-message",
            {},
            BytesIO(b"secret-response-body"),
        )

    monkeypatch.setattr(provider_module.urllib.request, "urlopen", fail)

    with pytest.raises(TennisProviderError) as raised:
        provider._get_json(
            "/sports/tennis/odds/",
            {"apiKey": "test-key"},
            request_kind="odds",
        )

    assert str(raised.value) == "provider_http_401"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert "test-key" not in str(raised.value)
    assert "secret.invalid" not in str(raised.value)
    assert "secret-response-body" not in str(raised.value)
    assert provider.odds_call_count == 1


def test_transport_error_never_reflects_exception_text(monkeypatch):
    provider = OddsApiTennisProvider(config())

    def fail(*args, **kwargs):
        raise RuntimeError(
            "https://secret.invalid?apiKey=test-key secret-response-body"
        )

    monkeypatch.setattr(provider_module.urllib.request, "urlopen", fail)

    with pytest.raises(TennisProviderError) as raised:
        provider._get_json(
            "/sports/tennis/odds/",
            {"apiKey": "test-key"},
            request_kind="odds",
        )

    assert str(raised.value) == "provider_request_failed"
    assert provider.odds_call_count == 1


def test_odds_response_earlier_start_becomes_the_effective_prematch_cutoff():
    scheduled = {
        "event_id": "event-1",
        "player_a": "Player A",
        "player_b": "Player B",
        "commence_time": "2026-07-22T18:10:00+00:00",
        "tournament_key": "tennis_atp_test",
    }
    raw = {
        "id": "event-1",
        "commence_time": "2026-07-22T18:00:00+00:00",
        "bookmakers": [],
    }

    event = OddsApiTennisProvider._normalize_odds_event(raw, scheduled)

    assert event["schedule_commence_time"] == "2026-07-22T18:10:00+00:00"
    assert event["odds_commence_time"] == "2026-07-22T18:00:00+00:00"
    assert event["commence_time"] == "2026-07-22T18:00:00+00:00"
    assert event["commence_time_cutoff_policy"] == "earliest_schedule_or_odds"
