from __future__ import annotations

import mlb_historical_optimizer_entrypoint as entrypoint


def _game(game_pk: int, official_date: str, *, final: bool = True):
    return {
        "gamePk": game_pk,
        "officialDate": official_date,
        "gameDate": f"{official_date}T23:10:00Z",
        "rescheduledFrom": None,
        "resumeDate": None,
        "status": {
            "abstractGameState": "Final" if final else "Preview",
            "detailedState": "Final" if final else "Scheduled",
        },
        "teams": {
            "away": {"team": {"name": "Away"}, "score": 2},
            "home": {"team": {"name": "Home"}, "score": 4},
        },
    }


def test_cross_date_provider_reference_is_excluded_and_fingerprinted():
    payload = {
        "totalGames": 2,
        "dates": [
            {
                "date": "2025-04-05",
                "totalGames": 2,
                "games": [
                    _game(1001, "2025-04-05"),
                    _game(1002, "2025-04-06"),
                ],
            }
        ],
    }

    result = entrypoint.fetch_official_schedule_cross_date_safe(
        "2025-04-05", http_get=lambda _url, _timeout: payload
    )

    assert result["officialGameCount"] == 1
    assert result["officialFinalCount"] == 1
    assert result["providerReportedGameCount"] == 2
    assert result["crossDateExcludedCount"] == 1
    assert result["games"][0]["officialGamePk"] == "1001"
    assert result["crossDateExclusions"][0]["officialGamePk"] == "1002"
    assert result["crossDateExclusions"][0]["officialDate"] == "2025-04-06"
    assert result["crossDateExclusions"][0]["queriedSlateDateEt"] == "2025-04-05"
    assert result["crossDateExclusionFingerprint"]


def test_same_date_invalid_game_still_fails_closed():
    broken = _game(2001, "2025-04-05")
    broken["teams"]["home"]["team"]["name"] = ""
    payload = {
        "totalGames": 1,
        "dates": [{"date": "2025-04-05", "games": [broken]}],
    }

    try:
        entrypoint.fetch_official_schedule_cross_date_safe(
            "2025-04-05", http_get=lambda _url, _timeout: payload
        )
    except RuntimeError as exc:
        assert "TEAM_IDENTITY_MISSING" in str(exc)
    else:
        raise AssertionError("same-date invalid game must remain fail-closed")


def test_template_routes_to_cross_date_safe_entrypoint():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "mlb_historical_optimizer" / "template.yaml").read_text()
    assert "Handler: mlb_historical_optimizer_entrypoint.lambda_handler" in text
    assert "s3:ListBucket" in text
