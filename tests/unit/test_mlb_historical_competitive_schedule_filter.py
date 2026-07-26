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
    monkeypatch.setenv(
        "MLB_HISTORICAL_COMPETITIVE_EXTENSION_START_DATE", "2026-03-25"
    )
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
    assert saved["rangeExtension"]["competitiveStartDate"] == "2026-03-25"
    assert saved["rangeExtension"]["competitiveGameTypes"] == [
        "D",
        "F",
        "L",
        "R",
        "W",
    ]
    assert saved["plan"]["slates"][0]["slateDateEt"] == "2026-03-25"


def test_cached_spring_extension_is_removed_without_erasing_usage(monkeypatch):
    state = {
        "phase": "BACKFILLING",
        "endDate": "2026-07-24",
        "paidBackfillAuthorized": True,
        "creditsConsumed": 159030,
        "networkRequestCount": 15903,
        "currentDate": "2026-02-20",
        "currentSlotIndex": 26,
        "lastError": None,
        "completedSlates": [
            {
                "slateDateEt": "2025-10-31",
                "eligibleGameCount": 1,
                "artifact": {"key": "datasets/2025-10-31.json"},
            },
            {
                "slateDateEt": "2026-02-20",
                "eligibleGameCount": 0,
                "artifact": {"key": "datasets/2026-02-20.json"},
            },
        ],
        "completeSlateCount": 2,
        "eligibleGameCount": 1,
        "rejectedSlates": [
            {"slateDateEt": "2026-02-20", "reason": "incomplete_full_slate_dataset"}
        ],
        "skippedHistoricalSlots": [
            {"slateDateEt": "2026-02-20", "status": "QUARANTINED_STALE"}
        ],
        "lastCompletedFinalsArtifact": {
            "key": "mlb/historical-daily-v1/official-finals/2026-02-20.json"
        },
        "lastCompletedQuarantineCount": 66,
        "plan": {
            "slates": [
                {
                    "slateDateEt": "2025-10-31",
                    "officialGameCount": 1,
                    "historicalRequestCount": 74,
                    "estimatedCredits": 740,
                },
                {
                    "slateDateEt": "2026-02-20",
                    "officialGameCount": 7,
                    "historicalRequestCount": 66,
                    "estimatedCredits": 660,
                },
                {
                    "slateDateEt": "2026-03-25",
                    "officialGameCount": 1,
                    "historicalRequestCount": 74,
                    "estimatedCredits": 740,
                },
            ],
            "rangeExtension": {
                "version": "MLB-HISTORICAL-RANGE-EXTENSION-v2-competitive-only",
                "previousEndDate": "2025-10-31",
                "newEndDate": "2026-07-24",
                "competitiveGameTypes": ["D", "F", "L", "R", "W"],
            },
            "completeDateRangeLedger": True,
            "planningErrorCount": 0,
            "rejectedDates": [],
        },
    }
    saved = {}

    monkeypatch.setenv("MLB_HISTORICAL_RANGE_EXTENSION_AUTHORIZED", "true")
    monkeypatch.setenv(
        "MLB_HISTORICAL_COMPETITIVE_EXTENSION_START_DATE", "2026-03-25"
    )
    monkeypatch.setattr(
        entrypoint.optimizer_handler, "_load_state", lambda: copy.deepcopy(state)
    )
    monkeypatch.setattr(
        entrypoint.optimizer_handler,
        "_save_state",
        lambda value: saved.update(copy.deepcopy(value)) or value,
    )

    entrypoint._repair_precompetitive_extension_state()

    assert [row["slateDateEt"] for row in saved["plan"]["slates"]] == [
        "2025-10-31",
        "2026-03-25",
    ]
    assert saved["currentDate"] == "2026-03-25"
    assert saved["currentSlotIndex"] == 0
    assert saved["phase"] == "BACKFILLING"
    assert saved["lastError"] is None
    assert saved["completeSlateCount"] == 1
    assert saved["eligibleGameCount"] == 1
    assert saved["rejectedSlates"] == []
    assert saved["skippedHistoricalSlots"] == []
    assert saved["creditsConsumed"] == 159030
    assert saved["networkRequestCount"] == 15903
    assert saved["lastCompletedFinalsArtifact"] is None
    assert saved["lastCompletedQuarantineCount"] == 0
    repair = saved["competitiveRangeRepair"]
    assert repair["competitiveStartDate"] == "2026-03-25"
    assert repair["removedPlanSlateCount"] == 1
    assert repair["removedCompletedSlateCount"] == 1
    assert repair["providerCreditsRetained"] is True
    assert repair["immutableS3EvidenceRetained"] is True
    assert saved["authorizedPlanFingerprint"] == saved["plan"]["fingerprint"]
