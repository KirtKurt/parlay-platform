from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_advanced_context as context
import mlb_fundamentals_snapshot_v2 as snapshot_v2


RETRIEVED_AT = "2026-07-21T20:00:10+00:00"


def test_advanced_context_required_groups_match_fundamentals_v2_contract():
    expected_context_groups = {context_name for _, context_name, _ in snapshot_v2.GROUP_SPECS}

    assert set(context._REQUIRED_CONTEXT_KEYS) == expected_context_groups
    assert "travel_rest" in context._REQUIRED_CONTEXT_KEYS
    assert "public_betting_handle" not in context._REQUIRED_CONTEXT_KEYS


def _team(team_id: int, name: str):
    return {"team": {"id": team_id, "name": name}}


def _game(
    game_pk: int,
    start: str,
    *,
    home_id: int,
    home_name: str,
    away_id: int,
    away_name: str,
    abstract_state: str = "Final",
    detailed_state: str = "Final",
    home_pitcher=None,
    away_pitcher=None,
):
    home = _team(home_id, home_name)
    away = _team(away_id, away_name)
    if home_pitcher is not None:
        home["probablePitcher"] = home_pitcher
    if away_pitcher is not None:
        away["probablePitcher"] = away_pitcher
    return {
        "gamePk": game_pk,
        "gameDate": start,
        "teams": {"home": home, "away": away},
        "status": {
            "abstractGameState": abstract_state,
            "detailedState": detailed_state,
        },
        "venue": {"id": 10, "name": "Official Park"},
    }


def _schedule(*games, fingerprint="history-fingerprint", endpoint="https://statsapi.test/history"):
    return {
        "ok": True,
        "source_status": "CONNECTED",
        "payload": {"dates": [{"games": list(games)}]},
        "error": None,
        "endpoint": endpoint,
        "retrievedAtUtc": RETRIEVED_AT,
        "payloadFingerprint": fingerprint,
        "historyStartDateEt": "2026-07-07",
        "historyEndDateEt": "2026-07-21",
    }


def _current(game_pk=300, start="2026-07-21T23:05:00Z"):
    return _game(
        game_pk,
        start,
        home_id=1,
        home_name="Home Club",
        away_id=2,
        away_name="Away Club",
        abstract_state="Preview",
        detailed_state="Scheduled",
    )


def _input(game_pk=300):
    return {
        "official_game_pk": game_pk,
        "home_team": "Home Club",
        "away_team": "Away Club",
    }


def test_travel_rest_uses_exact_team_ids_and_official_calendar_gap(monkeypatch):
    home_previous = _game(
        101,
        "2026-07-18T23:05:00Z",
        home_id=9,
        home_name="Other Club",
        away_id=1,
        away_name="Home Club",
    )
    away_previous = _game(
        202,
        "2026-07-20T20:10:00Z",
        home_id=2,
        home_name="Away Club",
        away_id=8,
        away_name="Another Club",
    )
    verified = _schedule(home_previous, away_previous, _current())
    monkeypatch.setattr(context, "_statsapi_schedule_history", lambda _: verified)

    result = context._travel_rest_payload("2026-07-21", _input())

    assert result["source_status"] == "CONNECTED"
    assert result["home_rest_days"] == 2
    assert result["away_rest_days"] == 0
    assert result["home_previous_game_pk"] == 101
    assert result["away_previous_game_pk"] == 202
    assert result["home_travel_miles"] is None
    assert result["away_travel_miles"] is None
    assert result["algorithmVersion"] == context.TRAVEL_REST_ALGORITHM_VERSION
    assert result["sourceProvenance"] == {
        "provider": "MLB Stats API",
        "endpoint": "https://statsapi.test/history",
        "dataset": (
            "schedule history 2026-07-07..2026-07-21; "
            f"derivation={context.TRAVEL_REST_ALGORITHM_VERSION}"
        ),
        "retrievedAtUtc": RETRIEVED_AT,
        "sourceEffectiveAtUtc": None,
        "payloadFingerprint": "history-fingerprint",
    }

    row = {
        "gameId": "mlb_statsapi:300",
        "officialGamePk": 300,
        "slateDateEt": "2026-07-21",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictionSourcePullAt": "2026-07-21T20:00:00+00:00",
        "advanced_context": {"travel_rest": result},
    }
    snapshot = snapshot_v2.build(row)
    travel = snapshot["groups"]["travel_rest"]
    assert travel["complete"] is True
    assert travel["values"]["homeRestDays"] == 2
    assert travel["values"]["awayRestDays"] == 0
    assert travel["payloadFingerprint"] == "history-fingerprint"


def test_same_team_doubleheader_requires_official_game_pk_and_game_two_uses_game_one(monkeypatch):
    game_one = _game(
        300,
        "2026-07-21T17:05:00Z",
        home_id=1,
        home_name="Home Club",
        away_id=2,
        away_name="Away Club",
        abstract_state="Live",
        detailed_state="In Progress",
    )
    game_two = _current(game_pk=301, start="2026-07-21T23:05:00Z")
    verified = _schedule(game_one, game_two)
    monkeypatch.setattr(context, "_statsapi_schedule_history", lambda _: verified)

    unresolved = context._travel_rest_payload(
        "2026-07-21",
        {"home_team": "Home Club", "away_team": "Away Club"},
    )
    assert unresolved["source_status"] == "MISSING_OFFICIAL_GAME_IDENTITY"
    assert unresolved["home_rest_days"] is None
    assert unresolved["away_rest_days"] is None

    exact = context._travel_rest_payload("2026-07-21", _input(game_pk=301))
    assert exact["source_status"] == "CONNECTED"
    assert exact["current_game_pk"] == "301"
    assert exact["home_previous_game_pk"] == 300
    assert exact["away_previous_game_pk"] == 300
    assert exact["home_rest_days"] == 0
    assert exact["away_rest_days"] == 0


def test_source_failure_and_unproved_history_remain_missing_not_zero(monkeypatch):
    failure = {
        "ok": False,
        "source_status": "ERROR",
        "payload": {},
        "error": "timeout",
        "endpoint": "https://statsapi.test/history",
        "retrievedAtUtc": RETRIEVED_AT,
        "payloadFingerprint": None,
        "historyStartDateEt": "2026-07-07",
        "historyEndDateEt": "2026-07-21",
    }
    monkeypatch.setattr(context, "_statsapi_schedule_history", lambda _: failure)

    failed = context._travel_rest_payload("2026-07-21", _input())
    assert failed["source_status"] == "ERROR"
    assert failed["home_rest_days"] is None
    assert failed["away_rest_days"] is None
    assert failed["sourceProvenance"]["payloadFingerprint"] is None

    postponed_home = _game(
        101,
        "2026-07-20T23:05:00Z",
        home_id=9,
        home_name="Other Club",
        away_id=1,
        away_name="Home Club",
        abstract_state="Final",
        detailed_state="Postponed",
    )
    preview_away = _game(
        202,
        "2026-07-20T20:10:00Z",
        home_id=2,
        home_name="Away Club",
        away_id=8,
        away_name="Another Club",
        abstract_state="Preview",
        detailed_state="Scheduled",
    )
    verified = _schedule(postponed_home, preview_away, _current())
    monkeypatch.setattr(context, "_statsapi_schedule_history", lambda _: verified)

    unproved = context._travel_rest_payload("2026-07-21", _input())
    assert unproved["source_status"] == "PARTIAL"
    assert unproved["home_rest_days"] is None
    assert unproved["away_rest_days"] is None
    assert "NO_STARTED_PRIOR_GAME_IN_VERIFIED_HISTORY_WINDOW" in unproved["reason"]


def test_wrong_official_identity_fails_closed(monkeypatch):
    verified = _schedule(_current())
    monkeypatch.setattr(context, "_statsapi_schedule_history", lambda _: verified)

    missing_game_pk = context._travel_rest_payload(
        "2026-07-21",
        {"home_team": "Home Club", "away_team": "Away Club"},
    )
    wrong_game = context._travel_rest_payload("2026-07-21", _input(game_pk=999))
    wrong_teams = context._travel_rest_payload(
        "2026-07-21",
        {
            "official_game_pk": 300,
            "home_team": "Different Club",
            "away_team": "Away Club",
        },
    )

    assert missing_game_pk["source_status"] == "MISSING_OFFICIAL_GAME_IDENTITY"
    for result in (missing_game_pk, wrong_game, wrong_teams):
        assert result["home_rest_days"] is None
        assert result["away_rest_days"] is None
    for result in (wrong_game, wrong_teams):
        assert result["source_status"] == "MISSING_FROM_PROVIDER"


def test_history_fetch_has_genuine_retrieval_time_payload_proof_and_cache(monkeypatch):
    payload = {"dates": [{"games": [_current()]}]}
    calls = []

    def get_json(url):
        calls.append(url)
        return payload

    context._STATSAPI_HISTORY_CACHE.clear()
    monkeypatch.setattr(context, "_http_get_json", get_json)

    first = context._statsapi_schedule_history("2026-07-21")
    second = context._statsapi_schedule_history("2026-07-21")

    assert first is second
    assert len(calls) == 1
    assert "startDate=2026-07-07" in first["endpoint"]
    assert "endDate=2026-07-21" in first["endpoint"]
    assert datetime.fromisoformat(first["retrievedAtUtc"]).tzinfo is not None
    assert first["payloadFingerprint"] == context._payload_fingerprint(payload)


def test_probable_pitcher_and_travel_provenance_are_not_conflated(monkeypatch):
    current = _game(
        300,
        "2026-07-21T23:05:00Z",
        home_id=1,
        home_name="Home Club",
        away_id=2,
        away_name="Away Club",
        abstract_state="Preview",
        detailed_state="Scheduled",
        home_pitcher={"id": 11, "fullName": "Home Starter"},
        away_pitcher={"id": 22, "fullName": "Away Starter"},
    )
    current_schedule = _schedule(
        current,
        fingerprint="probable-fingerprint",
        endpoint="https://statsapi.test/current?hydrate=probablePitcher",
    )
    history = _schedule(
        _game(
            101,
            "2026-07-20T23:05:00Z",
            home_id=9,
            home_name="Other Club",
            away_id=1,
            away_name="Home Club",
        ),
        _game(
            202,
            "2026-07-20T20:10:00Z",
            home_id=2,
            home_name="Away Club",
            away_id=8,
            away_name="Another Club",
        ),
        current,
        fingerprint="history-fingerprint",
        endpoint="https://statsapi.test/history",
    )
    monkeypatch.setattr(context, "_statsapi_schedule", lambda _: current_schedule)
    monkeypatch.setattr(context, "_statsapi_schedule_history", lambda _: history)

    result = context.build_advanced_context("2026-07-21", _input())

    probable = result["confirmed_probable_pitchers"]
    travel = result["travel_rest"]
    assert probable["source_status"] == "CONNECTED"
    assert probable["home_probable_pitcher"] == "Home Starter"
    assert probable["away_probable_pitcher"] == "Away Starter"
    assert probable["sourceProvenance"]["endpoint"] == current_schedule["endpoint"]
    assert probable["sourceProvenance"]["retrievedAtUtc"] == RETRIEVED_AT
    assert probable["sourceProvenance"]["payloadFingerprint"] == "probable-fingerprint"
    assert travel["sourceProvenance"]["endpoint"] == history["endpoint"]
    assert travel["sourceProvenance"]["payloadFingerprint"] == "history-fingerprint"


def test_current_schedule_outage_is_cached_across_slate_rows_and_snapshots_fail_closed(monkeypatch):
    calls = []

    def timeout(url):
        calls.append(url)
        raise TimeoutError("upstream timeout")

    history_failure = {
        "ok": False,
        "source_status": "ERROR",
        "payload": {},
        "error": "upstream timeout",
        "endpoint": "https://statsapi.test/history",
        "retrievedAtUtc": RETRIEVED_AT,
        "payloadFingerprint": None,
        "historyStartDateEt": "2026-07-07",
        "historyEndDateEt": "2026-07-21",
    }
    monkeypatch.setattr(context, "_STATSAPI_CACHE", {})
    monkeypatch.setattr(context, "_http_get_json", timeout)
    monkeypatch.setattr(context, "_statsapi_schedule_history", lambda _: history_failure)

    games = [
        _input(game_pk=300),
        {
            "official_game_pk": 301,
            "home_team": "Second Home Club",
            "away_team": "Second Away Club",
        },
    ]
    advanced_contexts = [
        context.build_advanced_context("2026-07-21", game)
        for game in games
    ]

    assert len(calls) == 1
    for game, advanced in zip(games, advanced_contexts):
        probable = advanced["confirmed_probable_pitchers"]
        assert probable["source_status"] == "ERROR"
        assert probable["home_probable_pitcher"] is None
        assert probable["away_probable_pitcher"] is None
        assert probable["sourceProvenance"]["payloadFingerprint"] is None

        snapshot = snapshot_v2.build(
            {
                "gameId": f"mlb_statsapi:{game['official_game_pk']}",
                "officialGamePk": game["official_game_pk"],
                "slateDateEt": "2026-07-21",
                "homeTeam": game["home_team"],
                "awayTeam": game["away_team"],
                "predictionSourcePullAt": "2026-07-21T20:00:00+00:00",
                "advanced_context": advanced,
            }
        )
        assert snapshot["trainingEligibleAtCapture"] is False
        assert "confirmed_probable_pitchers" in snapshot["missingGroups"]
        assert snapshot["groups"]["confirmed_probable_pitchers"]["values"]["homeName"] is None
        assert snapshot["groups"]["confirmed_probable_pitchers"]["values"]["awayName"] is None


def test_current_schedule_outage_cache_expires_and_retries(monkeypatch):
    calls = []

    def timeout(url):
        calls.append(url)
        raise TimeoutError("upstream timeout")

    monkeypatch.setattr(context, "_STATSAPI_CACHE", {})
    monkeypatch.setattr(context, "_http_get_json", timeout)

    first = context._statsapi_schedule("2026-07-21")
    cached_at = datetime.fromisoformat(first["retrievedAtUtc"])
    first["retrievedAtUtc"] = (
        cached_at - timedelta(seconds=context._STATSAPI_CACHE_SECONDS + 1)
    ).astimezone(timezone.utc).isoformat()
    second = context._statsapi_schedule("2026-07-21")

    assert len(calls) == 2
    assert first["ok"] is False
    assert second["ok"] is False
    assert second is not first
