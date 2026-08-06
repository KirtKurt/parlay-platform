from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "mlb_scoring_guard_status.py"
SPEC = importlib.util.spec_from_file_location("mlb_scoring_guard_status", MODULE_PATH)
assert SPEC and SPEC.loader
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def game(pk: str, provider_id: str, game_key: str, start: str):
    return {
        "official_game_pk": pk,
        "game_id": provider_id,
        "game_key": game_key,
        "away_team": "Away Club",
        "home_team": "Home Club",
        "commence_time": start,
        "books": {"fanduel": {"ml": {"home": -120, "away": 110}}},
        "moneyline_available": True,
    }


def pull_item(pulled_at: str, games):
    return {
        "record_type": "pull_run",
        "data": {
            "pull_id": f"pull-{pulled_at}",
            "pulled_at": pulled_at,
            "games": games,
            "provider_schedule_manifest": {
                "pullId": f"pull-{pulled_at}",
                "observedAtUtc": pulled_at,
                "gameCount": len(games),
                "fingerprint": f"fp-{pulled_at}",
                "scheduleAuthority": {"verified": True},
                "games": games,
            },
        },
    }


def prediction(pk: str, winner: str, score: float, fundamentals: bool):
    return {
        "record_type": "mlb_single_game_moneyline_prediction",
        "data": {
            "officialGamePk": pk,
            "predictedWinner": winner,
            "score": score,
            "confidenceTier": "Solid",
            "winnerOptimizer": {
                "fundamentalsApplied": fundamentals,
                "fundamentalsMode": "TIMESTAMPED_FUNDAMENTALS_V2" if fundamentals else "NEUTRAL_NOT_ENABLED",
            },
        },
    }


def feature(game_key: str, hot_team: str | None = "Home Club", hot_delta: float = 0.01):
    return {
        "entity_type": "HOT_PULL_MOVEMENT_FEATURE",
        "game_key": game_key,
        "latest_asof": "2026-07-22T12:15:00+00:00",
        "hot_team": hot_team,
        "hot_delta": hot_delta,
        "movement_strength": "MEDIUM" if hot_delta else "FLAT",
    }


def fixture():
    # Same teams, different official IDs and starts: this proves the guard does
    # not collapse doubleheaders into one game.
    first = game("1001", "provider-a", "provider-a", "2026-07-22T17:05:00+00:00")
    second = game("1002", "provider-b", "provider-b", "2026-07-22T23:05:00+00:00")
    pulls = [
        pull_item("2026-07-22T11:00:00+00:00", [first, second]),
        pull_item("2026-07-22T11:15:00+00:00", [first, second]),
    ]
    predictions = [
        prediction("1001", "Home Club", 61.2, True),
        prediction("1002", "Away Club", 57.4, False),
    ]
    features = [feature("provider-a"), feature("provider-b")]
    return pulls, predictions, features


def evaluate(predictions=None, features=None):
    pulls, default_predictions, default_features = fixture()
    return GUARD.evaluate_slate(
        slate_date="2026-07-22",
        pull_items=pulls,
        prediction_items=default_predictions if predictions is None else predictions,
        movement_items=default_features if features is None else features,
        created_at=datetime(2026, 7, 22, 12, 30, tzinfo=timezone.utc),
    )


def test_complete_doubleheader_slate_passes():
    report = evaluate()
    assert report["guardPassed"] is True
    assert report["summary"]["officialGameCount"] == 2
    assert report["summary"]["persistedPredictionGameCount"] == 2
    assert report["summary"]["invalidPredictionTeamCount"] == 0
    assert report["summary"]["invalidMovementTeamCount"] == 0
    assert report["summary"]["movementFeatureGameCount"] == 2
    assert report["summary"]["fundamentalsAppliedCount"] == 1
    assert all(row["predictedWinnerInMatchup"] for row in report["games"])
    assert all(row["hotTeamInMatchup"] for row in report["games"])
    assert len({row["gameIdentity"] for row in report["games"]}) == 2


def test_missing_prediction_fails_closed():
    _, predictions, _ = fixture()
    report = evaluate(predictions=predictions[:1])
    assert report["guardPassed"] is False
    assert report["summary"]["missingPredictionCount"] == 1
    assert "PERSISTED_WINNER_PREDICTION_COVERAGE_INCOMPLETE" in report["blockers"]


def test_prediction_for_team_outside_matchup_fails_closed():
    _, predictions, _ = fixture()
    predictions[0] = prediction("1001", "Unrelated Club", 61.2, True)
    report = evaluate(predictions=predictions)
    assert report["guardPassed"] is False
    assert report["summary"]["invalidPredictionTeamCount"] == 1
    assert report["invalidPredictionTeamGameIdentities"] == ["official:1001"]
    assert report["games"][0]["predictedWinnerInMatchup"] is False
    assert "PREDICTED_WINNER_NOT_IN_MATCHUP" in report["blockers"]


def test_movement_hot_team_outside_matchup_fails_closed():
    _, _, features = fixture()
    features[0] = feature("provider-a", hot_team="Unrelated Club")
    report = evaluate(features=features)
    assert report["guardPassed"] is False
    assert report["summary"]["invalidMovementTeamCount"] == 1
    assert report["invalidMovementTeamGameIdentities"] == ["official:1001"]
    assert report["games"][0]["hotTeamInMatchup"] is False
    assert "MOVEMENT_TEAM_NOT_IN_MATCHUP" in report["blockers"]


def test_flat_movement_without_hot_team_is_valid():
    _, _, features = fixture()
    features[0] = feature("provider-a", hot_team=None, hot_delta=0.0)
    report = evaluate(features=features)
    assert report["guardPassed"] is True
    assert report["summary"]["invalidMovementTeamCount"] == 0
    assert report["games"][0]["hotTeamInMatchup"] is True


def test_missing_movement_feature_fails_closed():
    _, _, features = fixture()
    report = evaluate(features=features[:1])
    assert report["guardPassed"] is False
    assert report["summary"]["missingMovementCount"] == 1
    assert "MOVEMENT_FEATURE_COVERAGE_INCOMPLETE" in report["blockers"]
