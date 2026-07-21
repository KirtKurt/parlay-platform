from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_official_schedule_authority as authority


SLATE = "2026-07-21"


def official_game(
    game_pk: int,
    start: str,
    *,
    home: str = "Arizona Diamondbacks",
    away: str = "Athletics",
    game_number: int = 1,
) -> dict:
    return {
        "gamePk": game_pk,
        "gameDate": start,
        "gameType": "R",
        "gameNumber": game_number,
        "doubleHeader": "Y" if game_number > 1 else "N",
        "status": {"abstractGameState": "Preview"},
        "teams": {
            "home": {"team": {"name": home}},
            "away": {"team": {"name": away}},
        },
    }


def schedule(*games: dict) -> dict:
    return authority.validate_exact_date_schedule(
        {
            "totalGames": len(games),
            "dates": [{"date": SLATE, "games": list(games)}] if games else [],
        },
        SLATE,
    )


def provider_game(
    provider_id: str,
    start: str,
    *,
    home: str = "Arizona Diamondbacks",
    away: str = "Athletics",
) -> dict:
    return {
        "id": provider_id,
        "commence_time": start,
        "home_team": home,
        "away_team": away,
        "bookmakers": [{"key": "fanduel", "markets": []}],
        "_provider_event_roster": True,
        "_provider_odds_payload": True,
        "_odds_exact_id_match": True,
    }


def test_matched_provider_identity_survives_while_official_time_corrects_plus_60_seconds():
    official = schedule(official_game(900001, "2026-07-21T22:40:00Z"))
    games, proof = authority.reconcile_official_schedule(
        official,
        [provider_game("opaque-odds-event-id", "2026-07-21T22:41:00Z")],
        observed_at_utc="2026-07-21T10:00:00+00:00",
    )

    assert len(games) == 1
    game = games[0]
    assert game["game_id"] == "opaque-odds-event-id"
    assert game["id"] == "opaque-odds-event-id"
    assert game["official_game_pk"] == "900001"
    assert game["official_game_id"] == "mlb_statsapi:900001"
    assert game["commence_time"] == "2026-07-21T22:40:00+00:00"
    assert game["official_commence_time"] == "2026-07-21T22:40:00+00:00"
    assert game["provider_commence_time"] == "2026-07-21T22:41:00+00:00"
    assert game["provider_start_drift_seconds"] == 60
    assert game["canonical_start_time_source"] == "MLB_STATS_API_EXACT_DATE"
    assert proof["canonicalGameIds"] == ["opaque-odds-event-id"]
    assert proof["providerStartDriftSeconds"] == [60]
    assert authority.validate_authority_proof(proof, games) == []


def test_missing_provider_event_remains_in_official_roster_with_stats_api_identity():
    official = schedule(
        official_game(900001, "2026-07-21T22:40:00Z"),
        official_game(
            900002,
            "2026-07-21T23:10:00Z",
            home="Seattle Mariners",
            away="Cincinnati Reds",
        ),
    )
    games, proof = authority.reconcile_official_schedule(
        official,
        [provider_game("opaque-odds-event-id", "2026-07-21T22:41:00Z")],
        observed_at_utc="2026-07-21T10:00:00+00:00",
    )

    assert len(games) == 2
    missing = next(game for game in games if game["official_game_pk"] == "900002")
    assert missing["game_id"] == "mlb_statsapi:900002"
    assert missing["provider_event_id"] is None
    assert missing["bookmakers"] == []
    assert missing["commence_time"] == "2026-07-21T23:10:00+00:00"
    assert proof["officialGameCount"] == 2
    assert proof["providerMatchedGameCount"] == 1
    assert proof["missingProviderEventOfficialGameIds"] == ["900002"]
    assert authority.validate_authority_proof(proof, games) == []


def test_earlier_provider_time_is_audited_but_does_not_override_official_time():
    official = schedule(official_game(900001, "2026-07-21T22:40:00Z"))
    games, proof = authority.reconcile_official_schedule(
        official,
        [provider_game("opaque-odds-event-id", "2026-07-21T22:39:00Z")],
        observed_at_utc="2026-07-21T10:00:00+00:00",
    )

    assert games[0]["commence_time"] == "2026-07-21T22:40:00+00:00"
    assert games[0]["canonical_start_time_source"] == "MLB_STATS_API_EXACT_DATE"
    assert proof["providerStartDriftSeconds"] == [-60]
    assert authority.validate_authority_proof(proof, games) == []


def test_doubleheader_crosswalk_uses_nearest_start_and_unique_provider_id():
    official = schedule(
        official_game(900010, "2026-07-21T17:10:00Z", game_number=1),
        official_game(900011, "2026-07-21T23:10:00Z", game_number=2),
    )
    provider = [
        provider_game("night-market-id", "2026-07-21T23:11:00Z"),
        provider_game("day-market-id", "2026-07-21T17:11:00Z"),
    ]
    games, proof = authority.reconcile_official_schedule(
        official,
        provider,
        observed_at_utc="2026-07-21T10:00:00+00:00",
    )

    by_pk = {game["official_game_pk"]: game for game in games}
    assert by_pk["900010"]["game_id"] == "day-market-id"
    assert by_pk["900011"]["game_id"] == "night-market-id"
    assert len({game["game_id"] for game in games}) == 2
    assert proof["providerMatchedGameCount"] == 2
    assert authority.validate_authority_proof(proof, games) == []


def test_authority_validation_rejects_tampered_canonical_start():
    official = schedule(official_game(900001, "2026-07-21T22:40:00Z"))
    games, proof = authority.reconcile_official_schedule(
        official,
        [provider_game("opaque-odds-event-id", "2026-07-21T22:41:00Z")],
        observed_at_utc="2026-07-21T10:00:00+00:00",
    )
    tampered = copy.deepcopy(games)
    tampered[0]["commence_time"] = "2026-07-21T22:41:00+00:00"

    errors = authority.validate_authority_proof(proof, tampered)

    assert "official_schedule_authority_start_mismatch:900001" in errors
    assert "official_schedule_authority_not_conservative:900001" not in errors
