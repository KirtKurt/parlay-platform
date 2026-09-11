from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "mlb_bullpen_roster_diagnostic",
    ROOT / "scripts" / "mlb_bullpen_roster_diagnostic.py",
)
assert SPEC and SPEC.loader
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)

NOW = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=3)


def _schedule_game() -> dict:
    return {
        "gamePk": 1001,
        "gameDate": START.isoformat().replace("+00:00", "Z"),
        "status": {"abstractGameState": "Preview"},
        "teams": {
            "home": {"team": {"id": 10}},
            "away": {"team": {"id": 20}},
        },
    }


def _team(team_id: int, base: int) -> dict:
    starter = base + 1
    relievers = [base + 2, base + 3, base + 4]
    ids = [starter, *relievers]
    return {
        "team": {"id": team_id},
        "pitchers": ids,
        "bullpen": relievers,
        "players": {
            f"ID{identity}": {"person": {"id": identity, "fullName": f"P{identity}"}}
            for identity in ids
        },
    }


def _feed() -> dict:
    game = _schedule_game()
    return {
        "gameData": {
            "game": {"pk": game["gamePk"]},
            "datetime": {"dateTime": game["gameDate"]},
            "status": {"abstractGameState": "Preview"},
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "home": _team(10, 100),
                    "away": _team(20, 200),
                }
            }
        },
    }


def _provider(*, game=None, feed=None):
    schedule_game = game or _schedule_game()
    live_feed = feed or _feed()

    def fetch(url: str, timeout: int):
        assert timeout == 8
        if "/schedule?" in url:
            return {"dates": [{"date": "2026-09-11", "games": [schedule_game]}]}
        assert url.endswith("/game/1001/feed/live")
        return live_feed

    return fetch


def test_valid_preview_bullpen_arrays_are_passive_identity_evidence_only() -> None:
    report = SUBJECT.build_report(observed_at=NOW, fetch_json=_provider())
    assert report["previewGameCount"] == 1
    assert report["bothBullpenIdentityValidCount"] == 1
    assert report["preT45BothBullpenIdentityValidCount"] == 1
    assert report["availableRelieverSemanticClaimCount"] == 0
    assert report["fundamentalsCompletenessChanged"] is False
    assert report["productionAuthorityChanged"] is False
    game = report["games"][0]
    assert game["feedObservedAtUtc"] == NOW.isoformat().replace("+00:00", "Z")
    assert game["home"]["bullpenPlayerIds"] == [102, 103, 104]
    assert game["away"]["bullpenPlayerIds"] == [202, 203, 204]
    assert game["home"]["subsetOfPitchers"] is True
    assert game["canSatisfyAvailableRelievers"] is False
    assert game["canChangeProductionScoring"] is False


def test_duplicate_or_malformed_bullpen_ids_fail_identity_validation() -> None:
    feed = _feed()
    feed["liveData"]["boxscore"]["teams"]["home"]["bullpen"] = [102, 102, None]
    report = SUBJECT.build_report(observed_at=NOW, fetch_json=_provider(feed=feed))
    assert report["bothBullpenIdentityValidCount"] == 0
    errors = report["games"][0]["home"]["errors"]
    assert "bullpen_contains_invalid_player_id" in errors
    assert "bullpen_player_ids_not_unique" in errors
    assert report["availableRelieverSemanticClaimCount"] == 0


def test_player_identity_mismatch_fails_closed() -> None:
    feed = _feed()
    feed["liveData"]["boxscore"]["teams"]["away"]["players"]["ID202"]["person"]["id"] = 999
    report = SUBJECT.build_report(observed_at=NOW, fetch_json=_provider(feed=feed))
    assert report["bothBullpenIdentityValidCount"] == 0
    assert "bullpen_player_identity_mismatch:202" in report["games"][0]["away"]["errors"]


def test_feed_game_team_status_and_start_identity_must_match_schedule() -> None:
    for mutation in ("game", "team", "status", "start"):
        feed = _feed()
        if mutation == "game":
            feed["gameData"]["game"]["pk"] = 999
        elif mutation == "team":
            feed["liveData"]["boxscore"]["teams"]["home"]["team"]["id"] = 999
        elif mutation == "status":
            feed["gameData"]["status"]["abstractGameState"] = "Live"
        else:
            feed["gameData"]["datetime"]["dateTime"] = (START + timedelta(minutes=5)).isoformat()
        report = SUBJECT.build_report(observed_at=NOW, fetch_json=_provider(feed=feed))
        game = report["games"][0]
        assert game["feedIdentityValid"] is False
        assert game["bothBullpenIdentityValid"] is False
        assert game["canSatisfyAvailableRelievers"] is False


def test_valid_roster_after_cutoff_is_not_pret45_evidence() -> None:
    observed = START - timedelta(minutes=30)
    report = SUBJECT.build_report(observed_at=observed, fetch_json=_provider())
    assert report["bothBullpenIdentityValidCount"] == 1
    assert report["preT45BothBullpenIdentityValidCount"] == 0
    assert report["games"][0]["preT45"] is False
    assert report["availableRelieverSemanticClaimCount"] == 0


def test_slow_feed_crossing_cutoff_uses_receipt_time_not_run_start() -> None:
    state = {"now": START - timedelta(minutes=46)}
    schedule_game = _schedule_game()
    live_feed = _feed()

    def clock():
        return state["now"]

    def fetch(url: str, timeout: int):
        assert timeout == 8
        if "/schedule?" in url:
            return {"dates": [{"date": "2026-09-11", "games": [schedule_game]}]}
        state["now"] = START - timedelta(minutes=44)
        return live_feed

    report = SUBJECT.build_report(fetch_json=fetch, clock=clock)
    game = report["games"][0]
    assert report["createdAtUtc"] == (START - timedelta(minutes=46)).isoformat().replace("+00:00", "Z")
    assert game["feedObservedAtUtc"] == (START - timedelta(minutes=44)).isoformat().replace("+00:00", "Z")
    assert game["bothBullpenIdentityValid"] is True
    assert game["preT45"] is False
    assert report["preT45BothBullpenIdentityValidCount"] == 0
    assert report["availableRelieverSemanticClaimCount"] == 0


def test_missing_bullpen_list_never_becomes_empty_available_list() -> None:
    feed = _feed()
    del feed["liveData"]["boxscore"]["teams"]["home"]["bullpen"]
    report = SUBJECT.build_report(observed_at=NOW, fetch_json=_provider(feed=feed))
    home = report["games"][0]["home"]
    assert home["identityValid"] is False
    assert home["bullpenPlayerIds"] == []
    assert "bullpen_list_missing" in home["errors"]
    assert report["availableRelieverSemanticClaimCount"] == 0
