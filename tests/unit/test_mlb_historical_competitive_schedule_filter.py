from __future__ import annotations

import copy

from hello_world import mlb_historical_optimizer_entrypoint as entrypoint


def _game(*, game_pk: int, game_type: str, away: int, home: int):
    return {
        "gamePk": game_pk,
        "gameType": game_type,
        "officialDate": "2026-03-25",
        "gameDate": "2026-03-25T20:00:00Z",
        "status": {
            "abstractGameState": "Final",
            "codedGameState": "F",
            "statusCode": "F",
            "detailedState": "Final",
        },
        "teams": {
            "away": {"score": away, "team": {"name": "Away"}},
            "home": {"score": home, "team": {"name": "Home"}},
        },
    }


def test_spring_training_ties_are_evidenced_but_not_labeled():
    payload = {
        "totalGames": 2,
        "dates": [
            {
                "date": "2026-03-25",
                "totalGames": 2,
                "games": [
                    _game(game_pk=1, game_type="S", away=3, home=3),
                    _game(game_pk=2, game_type="R", away=0, home=7),
                ],
            }
        ],
    }

    value = entrypoint.fetch_official_schedule_cross_date_safe(
        "2026-03-25", http_get=lambda _url, _timeout: payload
    )

    assert value["providerReportedGameCount"] == 2
    assert value["officialGameCount"] == 1
    assert value["games"][0]["officialGamePk"] == "2"
    assert value["nonCompetitiveExcludedCount"] == 1
    assert value["nonCompetitiveExclusions"][0]["officialGamePk"] == "1"
    assert value["nonCompetitiveExclusions"][0]["gameType"] == "S"
    assert value["nonCompetitiveExclusions"][0]["exclusionReason"] == (
        "provider_non_competitive_game_type"
    )


def test_blocked_extension_can_retry_and_clear_rejected_dates(monkeypatch):
    state = {
        "phase": "RANGE_EXTENSION_BLOCKED_INCOMPLETE_LEDGER",
        "endDate": "2026-03-24",
        "paidBackfillAuthorized": True,
        "creditsConsumed": 1000,
        "maximumCredits": 300000,
        "rangeExtensionRejectedDates": [
            {"slateDateEt": "2026-03-25", "details": "old failure"}
        ],
        "plan": {
            "slates": [],
            "fingerprint": "old",
            "completeDateRangeLedger": True,
            "planningErrorCount": 0,
            "rejectedDates": [],
        },
    }
    saved = {}

    monkeypatch.setenv("MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED", "true")
    monkeypatch.setattr(entrypoint.optimizer_handler, "END_DATE", "2026-03-25")
    monkeypatch.setattr(entrypoint.optimizer_handler, "MAX_CREDITS", 300000)
    monkeypatch.setattr(entrypoint.optimizer_handler, "QUOTA_RESERVE", 100)
    monkeypatch.setattr(
        entrypoint.optimizer_handler, "_load_state", lambda: copy.deepcopy(state)
    )
    monkeypatch.setattr(
        entrypoint.optimizer_handler,
        "_load_or_fetch_finals",
        lambda _day: (
            {
                "officialGameCount": 1,
                "games": [{"gameDate": "2026-03-25T20:00:00Z"}],
            },
            {},
        ),
    )
    monkeypatch.setattr(
        entrypoint.optimizer_handler,
        "_quota_status",
        lambda: {"x-requests-remaining": 1_000_000},
    )
    monkeypatch.setattr(
        entrypoint.optimizer_handler,
        "_save_state",
        lambda value: saved.update(copy.deepcopy(value)) or value,
    )

    entrypoint._append_authorized_range_extension()

    assert saved["phase"] == "BACKFILLING"
    assert saved["endDate"] == "2026-03-25"
    assert saved["lastError"] is None
    assert "rangeExtensionRejectedDates" not in saved
    assert saved["rangeExtension"]["competitiveGameTypes"] == [
        "D",
        "F",
        "L",
        "R",
        "W",
    ]
    assert saved["plan"]["slates"][0]["slateDateEt"] == "2026-03-25"
