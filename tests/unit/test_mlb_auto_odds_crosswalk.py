from __future__ import annotations

from datetime import datetime, timezone
import urllib.error
from urllib.parse import parse_qs, urlparse

import pytest

import handler as base
import orchestrator_v2 as strict
import orchestrator_v3 as runtime


SLATE = "2026-09-05"
AWAY = "Chicago Cubs"
HOME = "Miami Marlins"


def _game(game_pk: str, start: str, *, away: str = AWAY, home: str = HOME) -> dict:
    return {
        "gamePk": game_pk,
        "officialDate": SLATE,
        "gameDate": start,
        "away": {"name": away},
        "home": {"name": home},
    }


def _odds_event(
    event_id: str,
    start: str,
    *,
    away: str = AWAY,
    home: str = HOME,
    h2h: bool = True,
) -> dict:
    event = {
        "id": event_id,
        "commence_time": start,
        "away_team": away,
        "home_team": home,
    }
    if h2h:
        event["bookmakers"] = [
            {
                "key": "book",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": away, "price": 2.05},
                            {"name": home, "price": 1.80},
                        ],
                    }
                ],
            }
        ]
    return event


def _bbs_event(event_id: str, game: dict) -> dict:
    return {
        "id": event_id,
        "kickoff_utc": game["gameDate"],
        "away": {"name": game["away"]["name"]},
        "home": {"name": game["home"]["name"]},
    }


def _assembled_packet(
    monkeypatch,
    games: list[dict],
    *,
    priced: list[dict],
    catalog: list[dict],
    odds_request_ok: bool = True,
    catalog_request_ok: bool = True,
) -> dict:
    bbs_events = [
        _bbs_event(f"bbs-{game['gamePk']}", game) for game in games
    ]
    bbs_assignments = {
        str(game["gamePk"]): event
        for game, event in zip(games, bbs_events)
    }
    monkeypatch.setattr(
        base,
        "_official_schedule",
        lambda _slate: {"totalGames": len(games), "games": games},
    )
    monkeypatch.setattr(
        base,
        "_odds_core",
        lambda _slate: {
            "events": priced,
            "catalogEvents": catalog,
            "oddsRequestOk": odds_request_ok,
            "catalogRequestOk": catalog_request_ok,
            "quota": {},
        },
    )
    monkeypatch.setattr(
        base,
        "_bbs_matches",
        lambda _slate, _official: {
            "events": bbs_events,
            "assignments": bbs_assignments,
        },
    )
    return runtime.production._apply_source_coverage(
        base._assemble(SLATE, expanded=False)
    )


@pytest.mark.parametrize(
    ("slate", "expected_start", "expected_end"),
    [
        (
            "2026-09-05",
            "2026-09-05T04:00:00Z",
            "2026-09-06T03:59:59Z",
        ),
        # New York changes from EDT to EST during this slate.  Defining the
        # window as two local midnights (instead of adding 24 UTC hours) keeps
        # every game on the 25-hour Eastern calendar day in scope.
        (
            "2026-11-01",
            "2026-11-01T04:00:00Z",
            "2026-11-02T04:59:59Z",
        ),
        # The spring transition is a 23-hour Eastern slate.
        (
            "2026-03-08",
            "2026-03-08T05:00:00Z",
            "2026-03-09T03:59:59Z",
        ),
    ],
)
def test_odds_slate_bounds_are_exact_eastern_midnights(
    slate: str,
    expected_start: str,
    expected_end: str,
) -> None:
    assert base._odds_slate_bounds(slate) == (expected_start, expected_end)


def test_odds_and_catalog_requests_use_the_same_exact_slate_bounds(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def http_json(url: str, **_kwargs):
        calls.append(url)
        if urlparse(url).path.endswith("/events"):
            return ([_odds_event("catalog-1", "2026-09-05T20:10:00Z", h2h=False)], {})
        return (
            [_odds_event("priced-1", "2026-09-05T20:10:00Z")],
            {
                "x-requests-remaining": "100",
                "x-requests-used": "10",
                "x-requests-last": "3",
            },
        )

    monkeypatch.setattr(base, "ODDS_API_KEY", "secret")
    monkeypatch.setattr(base, "_http_json", http_json)

    result = base._odds_core(SLATE)

    assert len(calls) == 2
    assert {urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] for url in calls} == {
        "odds",
        "events",
    }
    for url in calls:
        query = parse_qs(urlparse(url).query)
        assert query["commenceTimeFrom"] == ["2026-09-05T04:00:00Z"]
        assert query["commenceTimeTo"] == ["2026-09-06T03:59:59Z"]
        assert query["dateFormat"] == ["iso"]
    assert [row["id"] for row in result["events"]] == ["priced-1"]
    assert [row["id"] for row in result["catalogEvents"]] == ["catalog-1"]


def test_next_eastern_midnight_is_excluded_from_inclusive_slate_bounds() -> None:
    rows = [
        _odds_event("start", "2026-09-05T04:00:00Z", h2h=False),
        _odds_event("last-second", "2026-09-06T03:59:59Z", h2h=False),
        _odds_event("next-midnight", "2026-09-06T04:00:00Z", h2h=False),
    ]

    bounded = base._bounded_odds_rows(
        rows,
        "2026-09-05T04:00:00Z",
        "2026-09-06T03:59:59Z",
    )

    assert [row["id"] for row in bounded] == ["start", "last-second"]


@pytest.mark.parametrize("catalog_failure", ["http", "schema"])
def test_catalog_failure_does_not_discard_complete_priced_odds(
    monkeypatch,
    catalog_failure: str,
) -> None:
    priced = [_odds_event("priced-1", "2026-09-05T20:10:00Z")]

    def http_json(url: str, **_kwargs):
        if urlparse(url).path.rstrip("/").endswith("/events"):
            if catalog_failure == "http":
                raise urllib.error.HTTPError(url, 503, "unavailable", {}, None)
            return {"unexpected": "schema"}, {}
        return priced, {
            "x-requests-remaining": "100",
            "x-requests-used": "10",
            "x-requests-last": "3",
        }

    monkeypatch.setattr(base, "ODDS_API_KEY", "secret")
    monkeypatch.setattr(base, "_http_json", http_json)

    result = base._odds_core(SLATE)

    assert result["oddsRequestOk"] is True
    assert result["catalogRequestOk"] is False
    assert [row["id"] for row in result["events"]] == ["priced-1"]
    assert result["catalogEvents"] == []


def test_odds_crosswalk_requires_exact_ordered_home_and_away() -> None:
    official = _game("game-1", "2026-09-05T20:10:00Z")
    reversed_event = _odds_event(
        "reversed",
        "2026-09-05T20:10:00Z",
        away=HOME,
        home=AWAY,
        h2h=False,
    )

    assigned = base._assign_odds_events(
        [official],
        [reversed_event],
        require_h2h=False,
    )

    assert not assigned.get("game-1")


def test_persisted_odds_matcher_also_rejects_reversed_team_order() -> None:
    official = _game("game-1", "2026-09-05T20:10:00Z")
    reversed_event = _odds_event(
        "reversed",
        "2026-09-05T20:10:00Z",
        away=HOME,
        home=AWAY,
    )

    assert (
        runtime._match_event_v2(official, [reversed_event], provider="odds")
        is None
    )


def test_one_provider_event_can_never_cover_two_official_games() -> None:
    official = [
        _game("game-1", "2026-09-05T17:10:00Z"),
        _game("game-2", "2026-09-05T23:40:00Z"),
    ]
    single_event = _odds_event(
        "only-event",
        "2026-09-05T17:12:00Z",
        h2h=False,
    )

    assigned = base._assign_odds_events(
        official,
        [single_event],
        require_h2h=False,
    )
    assigned_ids = [
        event["id"] for event in assigned.values() if isinstance(event, dict)
    ]

    assert assigned.get("game-1", {}).get("id") == "only-event"
    assert not assigned.get("game-2")
    assert assigned_ids == ["only-event"]


def test_assignment_maximizes_matches_before_minimizing_total_drift() -> None:
    official = [
        _game("game-1", "2026-09-05T16:00:00Z"),
        _game("game-2", "2026-09-06T03:54:00Z"),
    ]
    events = [
        # This is nearest to game 1 and is also the only event within the
        # allowed drift of game 2.  A per-game greedy matcher strands game 2.
        _odds_event("flexible", "2026-09-05T16:01:00Z", h2h=False),
        # Same Eastern slate, but more than 12 hours from game 2.
        _odds_event("game-1-only", "2026-09-05T04:01:00Z", h2h=False),
    ]

    assigned = base._assign_odds_events(
        official,
        events,
        require_h2h=False,
    )

    assert assigned["game-1"]["id"] == "game-1-only"
    assert assigned["game-2"]["id"] == "flexible"
    assert len({event["id"] for event in assigned.values()}) == 2


def test_adjacent_eastern_day_same_matchup_cannot_fill_current_slate() -> None:
    # Both starts are less than the legacy 18-hour tolerance apart, but only
    # the first belongs to the requested 2026-09-05 Eastern slate.
    official = _game("game-1", "2026-09-06T01:40:00Z")
    next_day_event = _odds_event(
        "next-day",
        "2026-09-06T17:00:00Z",
        h2h=False,
    )

    assigned = base._assign_odds_events(
        [official],
        [next_day_event],
        require_h2h=False,
    )

    assert not assigned.get("game-1")


def test_doubleheader_is_globally_paired_to_nearest_unique_events() -> None:
    official = [
        _game("game-1", "2026-09-05T17:10:00Z"),
        _game("game-2", "2026-09-05T23:40:00Z"),
    ]
    # Deliberately reverse provider order: list position cannot decide which
    # event fills which half of a doubleheader.
    events = [
        _odds_event("late", "2026-09-05T23:42:00Z", h2h=False),
        _odds_event("early", "2026-09-05T17:12:00Z", h2h=False),
    ]

    assigned = base._assign_odds_events(
        official,
        events,
        require_h2h=False,
    )

    assert assigned["game-1"]["id"] == "early"
    assert assigned["game-2"]["id"] == "late"
    assert len({event["id"] for event in assigned.values()}) == 2


@pytest.mark.parametrize(
    "event",
    [
        _odds_event("identity-only", "2026-09-05T20:10:00Z", h2h=False),
        {
            **_odds_event("spread-only", "2026-09-05T20:10:00Z", h2h=False),
            "bookmakers": [
                {
                    "key": "book",
                    "markets": [
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": AWAY, "price": 1.91},
                                {"name": HOME, "price": 1.91},
                            ],
                        }
                    ],
                }
            ],
        },
        {
            **_odds_event("one-price", "2026-09-05T20:10:00Z", h2h=False),
            "bookmakers": [
                {
                    "key": "book",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [{"name": AWAY, "price": 2.05}],
                        }
                    ],
                }
            ],
        },
        {
            **_odds_event("invalid-price", "2026-09-05T20:10:00Z", h2h=False),
            "bookmakers": [
                {
                    "key": "book",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": AWAY, "price": "NaN"},
                                {"name": HOME, "price": 1.80},
                            ],
                        }
                    ],
                }
            ],
        },
        {
            **_odds_event("split-books", "2026-09-05T20:10:00Z", h2h=False),
            "bookmakers": [
                {
                    "key": "home-only-book",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [{"name": HOME, "price": 1.80}],
                        }
                    ],
                },
                {
                    "key": "away-only-book",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [{"name": AWAY, "price": 2.05}],
                        }
                    ],
                },
            ],
        },
    ],
    ids=lambda event: event["id"],
)
def test_odds_coverage_requires_real_h2h_prices_for_both_teams(event: dict) -> None:
    assert base._odds_has_exact_h2h(event, HOME, AWAY) is False


def test_real_h2h_prices_for_both_exact_teams_are_eligible() -> None:
    event = _odds_event("real-h2h", "2026-09-05T20:10:00Z")

    assert base._odds_has_exact_h2h(event, HOME, AWAY) is True


def test_market_consensus_counts_only_valid_two_sided_h2h_books() -> None:
    event = _odds_event("mixed-books", "2026-09-05T20:10:00Z")
    event["bookmakers"].extend(
        [
            {
                "key": "nan",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": HOME, "price": "NaN"},
                            {"name": AWAY, "price": 2.05},
                        ],
                    }
                ],
            },
            {
                "key": "infinite",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": HOME, "price": "Infinity"},
                            {"name": AWAY, "price": 2.05},
                        ],
                    }
                ],
            },
            {
                "key": "boolean",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": HOME, "price": True},
                            {"name": AWAY, "price": 2.05},
                        ],
                    }
                ],
            },
            {
                "key": "one-sided",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [{"name": HOME, "price": 1.80}],
                    }
                ],
            },
        ]
    )
    game = {
        "home": {"name": HOME},
        "away": {"name": AWAY},
        "oddsCore": event,
    }

    consensus = base._market_consensus(game)

    assert consensus["available"] is True
    assert consensus["bookCount"] == 1
    assert consensus["homeProbability"] == pytest.approx(0.532468, abs=1e-6)
    assert consensus["awayProbability"] == pytest.approx(0.467532, abs=1e-6)
    assert consensus["marketFavorite"] == HOME


def test_wrong_next_eastern_slate_h2h_fails_source_presence() -> None:
    game = _game("game-1", "2026-09-06T01:40:00Z")
    game.update(
        {
            "official": {"gamePk": "game-1"},
            "oddsCore": _odds_event(
                "next-day-lines",
                "2026-09-06T17:00:00Z",
            ),
            "bbs": {"match": {"id": "bbs-1"}},
        }
    )

    assert runtime._source_presence_v2(game)["theOddsApi"] is False


@pytest.mark.parametrize("priced_count", [0, 1], ids=("empty", "partial"))
def test_valid_odds_response_is_integration_healthy_while_lines_are_incomplete(
    monkeypatch,
    priced_count: int,
) -> None:
    games = [
        _game("game-1", "2026-09-05T17:10:00Z", away="Away 1", home="Home 1"),
        _game("game-2", "2026-09-05T23:40:00Z", away="Away 2", home="Home 2"),
    ]
    priced = [
        _odds_event(
            "priced-1",
            games[0]["gameDate"],
            away="Away 1",
            home="Home 1",
        )
    ][:priced_count]
    catalog = [
        _odds_event(
            f"catalog-{index}",
            game["gameDate"],
            away=game["away"]["name"],
            home=game["home"]["name"],
            h2h=False,
        )
        for index, game in enumerate(games, start=1)
    ]

    packet = _assembled_packet(
        monkeypatch,
        games,
        priced=priced,
        catalog=catalog,
    )

    odds = packet["sourceStatus"]["theOddsApi"]
    assert odds["integrationOk"] is True
    assert odds["lineReadinessComplete"] is False
    assert odds["ok"] is False
    assert odds["lineReadyGames"] == priced_count
    assert packet["threeSourceCoverageComplete"] is False


@pytest.mark.parametrize(
    ("catalog_request_ok", "catalog"),
    [
        (False, []),
        (True, []),
    ],
    ids=("catalog-request-failed", "catalog-game-missing"),
)
def test_complete_priced_odds_remain_production_ready_without_catalog_coverage(
    monkeypatch,
    catalog_request_ok: bool,
    catalog: list[dict],
) -> None:
    game = _game("game-1", "2026-09-05T20:10:00Z")
    priced = [_odds_event("priced-1", game["gameDate"])]

    packet = _assembled_packet(
        monkeypatch,
        [game],
        priced=priced,
        catalog=catalog,
        catalog_request_ok=catalog_request_ok,
    )

    odds = packet["sourceStatus"]["theOddsApi"]
    assert odds["lineReadinessComplete"] is True
    assert odds["catalogCoverageComplete"] is False
    assert odds["integrationOk"] is True
    assert odds["ok"] is True
    assert packet["threeSourceCoverageComplete"] is True


def test_catalog_identity_is_diagnostic_and_never_counts_as_odds_coverage(
    monkeypatch,
) -> None:
    official = _game("game-1", "2026-09-05T20:10:00Z")
    catalog = _odds_event(
        "catalog-only",
        "2026-09-05T20:10:00Z",
        h2h=False,
    )
    bbs = {
        "id": "bbs-1",
        "kickoff_utc": "2026-09-05T20:10:00Z",
        "away": {"name": AWAY},
        "home": {"name": HOME},
    }

    monkeypatch.setattr(
        base,
        "_official_schedule",
        lambda _slate: {"totalGames": 1, "games": [official]},
    )
    monkeypatch.setattr(
        base,
        "_odds_core",
        lambda _slate: {
            "events": [],
            "catalogEvents": [catalog],
            "oddsRequestOk": True,
            "catalogRequestOk": True,
            "quota": {},
        },
    )
    monkeypatch.setattr(
        base,
        "_bbs_matches",
        lambda _slate, _official: {"events": [bbs]},
    )

    packet = base._assemble(SLATE, expanded=False)
    packet = runtime.production._apply_source_coverage(packet)

    game = packet["games"][0]
    assert game["oddsCore"] is None
    assert game["oddsCatalogEvent"]["id"] == "catalog-only"
    assert packet["sourcePresenceByGamePk"]["game-1"]["theOddsApi"] is False
    assert packet["sourceStatus"]["theOddsApi"]["catalogCoverageComplete"] is True
    assert packet["sourceStatus"]["theOddsApi"]["lineReadinessComplete"] is False
    assert packet["sourceStatus"]["theOddsApi"]["ok"] is False
    assert packet["threeSourceCoverageComplete"] is False


def test_future_slate_partial_lines_are_reported_without_publication_readiness(
    monkeypatch,
) -> None:
    now = datetime(2026, 9, 5, 23, 0, tzinfo=timezone.utc)
    current_slate = now.astimezone(base.ET).date().isoformat()
    future_slate = "2026-09-06"
    current_schedule = {
        "games": [_game("current", "2026-09-05T18:00:00Z")]
    }
    partial_game = {
        **_game("future", "2026-09-06T17:00:00Z"),
        "official": {
            **_game("future", "2026-09-06T17:00:00Z"),
        },
        "oddsCore": None,
        "oddsCatalogEvent": _odds_event(
            "future-catalog",
            "2026-09-06T17:00:00Z",
            h2h=False,
        ),
        "bbs": {"match": {"id": "bbs-future"}},
    }
    partial_packet = {
        "slateDateEt": future_slate,
        "games": [partial_game],
        "sourceStatus": {
            "mlbStatsApi": {"ok": True, "integrationOk": True},
            "theOddsApi": {
                "ok": False,
                "integrationOk": True,
                "catalogCoverageComplete": True,
                "lineReadinessComplete": False,
                "missingGamePks": ["future"],
            },
            "bigBallsDataPro": {"ok": True, "integrationOk": True},
        },
        "threeSourceCoverageComplete": False,
    }

    monkeypatch.setattr(base, "_now", lambda: now)
    monkeypatch.setattr(base, "_official_schedule", lambda _slate: current_schedule)
    monkeypatch.setattr(
        base,
        "_deadline",
        lambda _schedule: {"publishDeadlineUtc": "2026-09-05T17:00:00Z"},
    )
    monkeypatch.setattr(
        strict,
        "_next_future_provider_probe",
        lambda _slate, _now: {
            "slate": future_slate,
            "schedule": {"games": [partial_game["official"]]},
            "deadline": {"publishDeadlineUtc": "2026-09-06T16:00:00Z"},
            "deadlineDt": datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc),
        },
    )
    monkeypatch.setattr(
        strict.production,
        "_assemble_with_full_bbd",
        lambda _slate, *, expanded: partial_packet,
    )
    monkeypatch.setattr(
        strict.production,
        "_validate_deployment_smoke",
        runtime._validate_deployment_smoke_v3,
    )

    result = runtime.lambda_handler(
        {"mode": strict.DEPLOYMENT_PROVIDER_SMOKE_MODE, "force_publish": False},
        None,
    )

    assert result["status"] == "COLLECTING"
    assert result["providerProbeUsedFutureSlate"] is True
    assert result["providerIntegrationComplete"] is True
    assert result["lineReadinessComplete"] is False
    assert result["publicationReady"] is False
    assert result["threeSourceCoverageComplete"] is False
    assert result["sourceStatus"]["theOddsApi"]["missingGamePks"] == ["future"]
    assert result["readOnlyProviderProbe"] is True
    assert result["persistenceAttempted"] is False
    assert result["cardMutationAttempted"] is False

    with pytest.raises(RuntimeError, match="THREE_SOURCE_GAME_COVERAGE_INCOMPLETE"):
        runtime._build_card_three_source_model(partial_packet)
