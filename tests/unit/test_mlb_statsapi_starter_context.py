from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import mlb_statsapi_starter_context as source
import mlb_fundamentals_snapshot_v2 as snapshot
import mlb_ml_dual_model_v2 as dual
import mlb_advanced_context as advanced

NOW = datetime(2026, 9, 9, 16, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clear_cache():
    source._CACHE.clear()


def game():
    return {"gamePk": 824950, "gameDate": "2026-09-09T20:00:00Z",
            "status": {"abstractGameState": "Preview"},
            "teams": {"home": {"probablePitcher": {"id": 101}},
                      "away": {"probablePitcher": {"id": 202}}}}


def person(identity, hand="R"):
    return {"id": identity, "pitchHand": {"code": hand}, "stats": [{
        "group": {"displayName": "pitching"}, "type": {"displayName": "season"},
        "splits": [{"season": "2026", "sport": {"id": 1}, "gameType": "R",
                    "team": {"id": 147}, "stat": {"era": "3.50", "strikeOuts": 90,
                    "baseOnBalls": 20, "battersFaced": 400}}]}]}


def call(g=None, people=None, getter=None, clock=None, day="2026-09-09"):
    g = g or game()
    payload = {"people": people if people is not None else [person(101), person(202, "L")]}
    return source.observe(day, g, {"payload": {"dates": [{"games": [g]}]}},
                          getter or (lambda *args, **kwargs: payload), now=clock or (lambda: NOW))


def test_observes_named_metrics_and_reuses_one_bounded_batch():
    requests = []
    def get(url, timeout):
        requests.append((url, timeout))
        return {"people": [person(101), person(202, "L")]}
    quality, hand = call(getter=get)
    assert call(getter=get) == (quality, hand)
    assert len(requests) == 1 and requests[0][1] == 4
    assert quality["home_starter_era"] == 3.5
    assert quality["home_starter_k_minus_bb_pct"] == 17.5
    assert hand["away_starter_hand"] == "L"
    assert quality["home_pitcher_id"] == 101
    assert len(quality["sourceProvenance"]["payloadFingerprint"]) == 64
    assert quality["sourceProvenance"]["retrievedAtUtc"] == NOW.isoformat()
    assert quality["source_status"] == "PARTIAL"
    assert not any("composite" in key or "fip" in key for key in quality)


@pytest.mark.parametrize("mode", ["historical", "wrong_day", "live", "lock", "missing_id"])
def test_never_fetches_after_lock_for_history_or_unverified_identity(mode):
    g = game()
    day, clock = "2026-09-09", lambda: NOW
    if mode == "historical": day = "2026-09-08"
    if mode == "wrong_day": g["gameDate"] = "2026-09-10T20:00:00Z"
    if mode == "live": g["status"]["abstractGameState"] = "Live"
    if mode == "lock": clock = lambda: NOW + timedelta(hours=3, minutes=15)
    if mode == "missing_id": g["teams"]["home"]["probablePitcher"] = {}
    def forbidden(*args, **kwargs):
        pytest.fail("ineligible request reached the provider")
    quality, _ = call(g=g, getter=forbidden, clock=clock, day=day)
    assert quality["source_status"] == "NOT_CONNECTED_SOURCE_REQUIRED"


def test_response_crossing_lock_is_not_attached():
    moments = iter([NOW, NOW + timedelta(hours=4), NOW + timedelta(hours=4)])
    assert call(clock=lambda: next(moments))[0]["source_status"] == "NOT_CONNECTED_SOURCE_REQUIRED"


def test_provider_failure_is_cached_and_does_not_abort_collection():
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(1)
        raise TimeoutError()
    assert call(getter=fail)[0]["source_status"] == "NOT_CONNECTED_SOURCE_REQUIRED"
    assert call(getter=fail)[0]["source_status"] == "NOT_CONNECTED_SOURCE_REQUIRED"
    assert len(attempts) == 1


def test_exact_person_join_and_ambiguous_season_totals_fail_closed():
    p = person(101)
    p["stats"][0]["splits"].append(deepcopy(p["stats"][0]["splits"][0]))
    quality, hand = call(people=[p, person(999)])
    assert quality["home_starter_era"] is None
    assert quality["away_starter_era"] is None
    assert hand["away_starter_hand"] is None


@pytest.mark.parametrize("field,value", [("battersFaced", 0), ("baseOnBalls", None),
                                        ("strikeOuts", 500), ("strikeOuts", True)])
def test_incomplete_or_invalid_counts_do_not_become_zero_rates(field, value):
    p = person(101)
    p["stats"][0]["splits"][0]["stat"][field] = value
    assert source._season_metrics(p, 2026)["kMinusBbPct"] is None


def test_snapshot_preserves_observations_without_changing_frozen_r8_features():
    quality, hand = call()
    row = {"gameId": "mlb_statsapi:824950", "officialGamePk": 824950,
           "slateDateEt": "2026-09-09", "homeTeam": "Home", "awayTeam": "Away",
           "predictionSourcePullAt": NOW.isoformat(),
           "advanced_context": {"fip_xfip": quality, "starter_handedness_splits": hand}}
    frozen = snapshot.build(row)
    group = frozen["groups"]["starter_quality"]
    assert group["values"]["homeEra"] == 3.5
    assert group["values"]["homeKMinusBbPct"] == 17.5
    assert group["identifiers"]["homeEntityId"] == 101
    assert group["complete"] is False
    assert group["values"]["homeFip"] is None
    assert group["values"]["homeComposite"] is None
    before = dual._strict_features({}, {})
    after = dual._strict_features({"fundamentalsSnapshotV2": frozen}, {})
    assert after == before


@pytest.mark.parametrize("failure", [False, True])
def test_active_context_hook_preserves_known_context_when_adapter_fails(monkeypatch, failure):
    g = game()
    for side in ("home", "away"):
        g["teams"][side]["team"] = {"name": side.title()}
        g["teams"][side]["probablePitcher"]["fullName"] = side.title() + " Pitcher"
    schedule = {"ok": True, "payload": {"dates": [{"games": [g]}]}}
    monkeypatch.setattr(advanced, "_statsapi_schedule", lambda _: schedule)
    monkeypatch.setattr(advanced, "_travel_rest_payload", lambda *args: {"source_status": "PARTIAL"})
    quality, hand = call()
    def observe(*args):
        if failure:
            raise ValueError("provider schema changed")
        return quality, hand
    monkeypatch.setattr(source, "observe", observe)
    result = advanced.build_advanced_context("2026-09-09", {
        "official_game_pk": g["gamePk"], "home_team": "Home", "away_team": "Away"})
    assert result["confirmed_probable_pitchers"]["home_pitcher_id"] == 101
    assert result["fip_xfip"]["source_status"] == ("ERROR" if failure else "PARTIAL")
    assert result["fip_xfip"]["home_starter_era"] == (None if failure else 3.5)
    assert result["advanced_eligibility"]["eligible"] is False
