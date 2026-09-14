import copy
import hashlib
import json
from decimal import Decimal

import pytest

from ks1.features import Features
from ks1.inventory import encode
from ks1.passive_context import (BATTING_VERSION, CONTRACT, TEAM_CONTEXT_VERSION,
                                 build_profile, frozen_profile_features)


class History:
    def __init__(self):
        self.calls = []

    def bullpen_roster_at(self, as_of, team_id, roster_ids):
        self.calls.append((as_of, team_id, roster_ids))
        return {"bullpen_context_roster_count": float(len(roster_ids)),
                "bullpen_context_available_count": 0.0,
                "bullpen_context_unknown_count": float(len(roster_ids))}


def provenance(game_id, at="2026-09-14T16:00:00+00:00"):
    return {"provider": "MLB Stats API", "dataset": TEAM_CONTEXT_VERSION,
            "endpoint": f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live",
            "retrievedAtUtc": at, "sourceEffectiveAtUtc": at,
            "payloadFingerprint": "a"*64}


def stored_observation():
    game_id = 900001
    line = {"game_pk": game_id, "lineupSeasonBattingVersion": BATTING_VERSION,
            "sourceProvenance": provenance(game_id)}
    bullpen = {"game_pk": game_id, "bullpenRosterObservationStatus": "OBSERVED_ROSTER_ONLY",
               "bullpenRosterSourceProvenance": provenance(game_id)}
    for side, base in (("home", 100), ("away", 200)):
        team_id = 10 if side == "home" else 20
        line[side+"_team_id"] = team_id
        bullpen[side+"_team_id"] = team_id
        ids = list(range(base+1, base+10))
        line[side+"_lineup_confirmed"] = True
        line[side+"_batting_order"] = ids
        line[side+"_lineup_season_batting"] = [
            {"playerId": pid, "battingSlot": slot, "plateAppearances": 100,
             "ops": Decimal("0.700")+Decimal(slot)/100,
             "obp": Decimal("0.300")+Decimal(slot)/1000,
             "slg": Decimal("0.400")+Decimal(slot)/1000,
             "rateObservationCount": 3, "sampleStatus": "OBSERVED"}
            for slot, pid in enumerate(ids, 1)]
        bullpen[side+"_bullpen_roster_player_ids"] = list(range(base+50, base+55))
    return {"PK": "GAME_WINNERS#mlb#2026-09-14", "SK": "GAME#900001",
            "data": {"officialGamePk": game_id, "commenceTime": "2026-09-14T18:00:00+00:00",
                     "passiveTeamContext": {"confirmed_lineups": line, "bullpen_fatigue": bullpen}}}


def game_row():
    game = {"gamePk": 900001, "gameDate": "2026-09-14T18:00:00+00:00"}
    row = {"home_id": "10", "away_id": "20"}
    for side, base in (("home", 100), ("away", 200)):
        row[side+"_lineup_ids"] = json.dumps(list(range(base+1, base+10)))
    return game, row


def test_builds_checksum_bound_profile_and_supported_features():
    history = History(); game, row = game_row()
    profile, features = build_profile(stored_observation(), game, row,
                                      "2026-09-14T16:05:00+00:00", history)
    assert profile["contract"] == CONTRACT
    assert profile["coverage_status"] == "SUPPORTED_V1_COMPLETE"
    assert profile["sides"]["home"]["availability_status"] == "UNKNOWN_ROSTER_ONLY"
    assert "bullpen_SIERA" in profile["unavailable_fields"]
    assert features["home_lineup_observed_batters"] == 9
    assert features["away_bullpen_context_unknown_count"] == 5
    claimed = profile.pop("sha256")
    assert hashlib.sha256(encode(profile)).hexdigest() == claimed
    assert len(history.calls) == 2


def test_incomplete_history_coverage_fails_closed():
    game, row = game_row()
    coverage = {key: True for key in (
        "7d", "30d", "last3", "current_season_context", "prior_year", "statcast_30d")}
    coverage["30d"] = False
    with pytest.raises(ValueError, match="history coverage incomplete"):
        build_profile(stored_observation(), game, row,
                      "2026-09-14T16:05:00+00:00", History(), history_coverage=coverage)


def test_incomplete_lineup_plate_appearances_stay_null():
    stored = stored_observation(); game, row = game_row()
    sample = stored["data"]["passiveTeamContext"]["confirmed_lineups"][
        "home_lineup_season_batting"][0]
    sample.update(plateAppearances=None, ops=None, obp=None, slg=None,
                  rateObservationCount=0, sampleStatus="SAMPLE_UNAVAILABLE")
    _, features = build_profile(stored, game, row,
                                "2026-09-14T16:05:00+00:00", History())
    assert features["home_lineup_total_pa"] is None
    assert features["home_lineup_quality_ops"] is None


def test_profile_binds_matchup_to_opposing_starter_identity():
    game, row = game_row()
    row.update(home_starter_id="501", away_starter_id="601",
               _home_starter_pitch_hand="R", _away_starter_pitch_hand="L")
    profile, _ = build_profile(stored_observation(), game, row,
                               "2026-09-14T16:05:00+00:00", History())
    assert profile["sides"]["home"]["opposing_starter_id"] == "601"
    assert profile["sides"]["away"]["opposing_starter_id"] == "501"


def test_rejects_post_cutoff_or_mismatched_lineup():
    game, row = game_row()
    late = stored_observation()
    for block, key in ((late["data"]["passiveTeamContext"]["confirmed_lineups"], "sourceProvenance"),
                       (late["data"]["passiveTeamContext"]["bullpen_fatigue"], "bullpenRosterSourceProvenance")):
        block[key] = provenance(900001, "2026-09-14T17:55:00+00:00")
    with pytest.raises(ValueError, match="point-in-time"):
        build_profile(late, game, row, "2026-09-14T17:50:00+00:00", History())
    row["home_lineup_ids"] = json.dumps(list(range(301, 310)))
    with pytest.raises(ValueError, match="persisted/live lineup mismatch"):
        build_profile(stored_observation(), game, row, "2026-09-14T16:05:00+00:00", History())


def test_roster_presence_cannot_claim_availability():
    stored = stored_observation(); game, row = game_row()
    stored["data"]["passiveTeamContext"]["bullpen_fatigue"]["home_available_relievers"] = [151]
    with pytest.raises(ValueError, match="must not claim availability"):
        build_profile(stored, game, row, "2026-09-14T16:05:00+00:00", History())


def test_rejects_cross_team_roster_identity():
    stored = stored_observation(); game, row = game_row()
    stored["data"]["passiveTeamContext"]["bullpen_fatigue"]["away_bullpen_roster_player_ids"][0] = 151
    with pytest.raises(ValueError, match="cross-team identity overlap"):
        build_profile(stored, game, row, "2026-09-14T16:05:00+00:00", History())


def test_rejects_swapped_persisted_team_identity():
    stored = stored_observation(); game, row = game_row()
    line = stored["data"]["passiveTeamContext"]["confirmed_lineups"]
    line["home_team_id"], line["away_team_id"] = line["away_team_id"], line["home_team_id"]
    with pytest.raises(ValueError, match="team identity mismatch"):
        build_profile(stored, game, row, "2026-09-14T16:05:00+00:00", History())


def test_missing_current_team_identity_fails_closed():
    game, row = game_row()
    row["home_id"] = None
    with pytest.raises(ValueError, match="team identity mismatch"):
        build_profile(stored_observation(), game, row,
                      "2026-09-14T16:05:00+00:00", History())


def test_roster_only_shape_binds_through_validated_lineup_teams():
    stored = stored_observation(); game, row = game_row()
    bullpen = stored["data"]["passiveTeamContext"]["bullpen_fatigue"]
    bullpen.pop("home_team_id"); bullpen.pop("away_team_id")
    profile, _ = build_profile(stored, game, row,
                               "2026-09-14T16:05:00+00:00", History())
    assert profile["sides"]["home"]["team_id"] == "10"


def test_frozen_reader_rejects_tampering_and_recovers_only_features():
    history = History(); game, row = game_row()
    profile, features = build_profile(stored_observation(), game, row,
                                      "2026-09-14T16:05:00+00:00", history)
    locked = {**row, "game_id": "900001", "commence_time": game["gameDate"],
              "as_of": "2026-09-14T16:05:00+00:00",
              "lineup_bullpen_profile_contract": CONTRACT,
              "lineup_bullpen_profile_sha256": profile["sha256"],
              "lineup_bullpen_profile_semantic_sha256": profile["semantic_sha256"],
              "lineup_bullpen_profile_json": encode(profile).decode()}
    assert frozen_profile_features(locked)["features"] == features
    tampered = copy.deepcopy(locked)
    body = json.loads(tampered["lineup_bullpen_profile_json"])
    body["sides"]["home"]["features"]["lineup_quality_ops"] = 9.9
    tampered["lineup_bullpen_profile_json"] = json.dumps(body)
    assert frozen_profile_features(tampered) is None


def test_batter_windows_and_pitch_matchup_use_only_earlier_games():
    batting = {"atBats": 4, "hits": 2, "baseOnBalls": 1, "hitByPitch": 0,
               "sacFlies": 0, "doubles": 1, "triples": 0, "homeRuns": 0,
               "strikeOuts": 1}
    def prior_game(pk, day, stats):
        return {"officialGamePk": pk, "startAtUtc": day+"T18:00:00Z",
                "completedAtUtc": day+"T21:00:00Z", "gameType": "R",
                "teams": {side: {"team": {"id": tid, "name": side},
                                  "teamStats": {"batting": batting},
                                  "players": ({"ID101": {"person": {"id": 101},
                                                           "stats": {"batting": stats}}}
                                              if side == "home" else {})}
                          for side, tid in (("home", 10), ("away", 20))}}
    rows = [{"game_pk": "1", "batter": "101", "pitcher": "500", "p_throws": "R",
             "pitch_type": "FF", "type": "X", "description": "hit_into_play",
             "woba_denom": "1", "woba_value": ".9",
             "estimated_woba_using_speedangle": ".8", "launch_speed": "100",
             "launch_speed_angle": "6"}]
    engine = Features([prior_game(1, "2026-09-01", batting),
                       prior_game(2, "2026-09-15", {**batting, "hits": 0})], rows)
    profiles, features = engine.lineup_batters_at(
        "2026-09-10T17:50:00Z", list(range(101, 110)), "500", "R",
        game_date="2026-09-10")
    assert profiles[0]["windows"]["30d"]["pa"] == 5
    assert features["lineup_ops_30d"] == pytest.approx(.6+.75)
    assert features["lineup_xwoba_30d"] == pytest.approx(.8)
    assert features["lineup_barrel_pct_30d"] == 100
    assert features["lineup_pitch_type_matchup_xwoba_30d"] == pytest.approx(.8)
    assert features["lineup_ops_talent"] == pytest.approx(.6+.75)


def test_reliever_profile_has_strict_prior_workload_quality_and_arsenal():
    pitching = {"outs": 3, "earnedRuns": 0, "runs": 0, "hits": 1, "homeRuns": 0,
                "baseOnBalls": 0, "hitBatsmen": 0, "strikeOuts": 2,
                "battersFaced": 4, "wins": 0, "losses": 0, "gamesStarted": 0,
                "numberOfPitches": 25}
    game = {"officialGamePk": 8, "startAtUtc": "2026-09-09T18:00:00Z",
            "completedAtUtc": "2026-09-09T21:00:00Z", "gameType": "R",
            "teams": {side: {"team": {"id": tid, "name": side},
                              "teamStats": {"batting": {}},
                              "players": ({"ID151": {"person": {"id": 151},
                                                       "stats": {"pitching": pitching}}}
                                          if side == "home" else {})}
                      for side, tid in (("home", 10), ("away", 20))}}
    pitches = [{"game_pk": "8", "pitcher": "151", "type": "S", "pitch_type": "FF",
                "description": "swinging_strike", "release_speed": "96",
                "release_spin_rate": "2400", "release_extension": "6.5",
                "pfx_x": "-.5", "pfx_z": "1.2"} for _ in range(25)]
    values = Features([game], pitches).bullpen_roster_at(
        "2026-09-10T17:50:00Z", "10", ["151"])
    assert values["bullpen_context_limited_count"] == 1
    assert values["bullpen_context_k_bb_pct_7d"] is not None
    reliever = values["_reliever_profiles"][0]
    assert reliever["workload"]["1d"]["pitches"] == 25
    assert reliever["windows"]["7d"]["velocity"] == 96
    assert reliever["windows"]["7d"]["pitch_arsenal"]["FF"]["mix_pct"] == 100


def test_reliever_history_survives_trade_and_missing_pitch_count_is_unknown():
    pitching = {"outs": 3, "earnedRuns": 0, "runs": 0, "hits": 1, "homeRuns": 0,
                "baseOnBalls": 0, "hitBatsmen": 0, "strikeOuts": 2,
                "battersFaced": 4, "wins": 0, "losses": 0, "gamesStarted": 0,
                "numberOfPitches": None}
    game = {"officialGamePk": 9, "startAtUtc": "2026-09-09T18:00:00Z",
            "completedAtUtc": "2026-09-09T21:00:00Z", "gameType": "R",
            "teams": {side: {"team": {"id": tid, "name": side},
                              "teamStats": {"batting": {}},
                              "players": ({"ID151": {"person": {"id": 151},
                                                       "stats": {"pitching": pitching}}}
                                          if side == "home" else {})}
                      for side, tid in (("home", 99), ("away", 20))}}
    values = Features([game], []).bullpen_roster_at(
        "2026-09-10T17:50:00Z", "10", ["151"])
    assert values["bullpen_context_unknown_count"] == 1
    assert values["bullpen_context_fatigue_score"] is None
    assert values["bullpen_context_era_7d"] == 0


def test_bullpen_contact_rates_use_pooled_contact_denominators():
    def stats(pitches):
        return {"outs": 3, "earnedRuns": 0, "runs": 0, "hits": 1, "homeRuns": 0,
                "baseOnBalls": 0, "hitBatsmen": 0, "strikeOuts": 2,
                "battersFaced": 4, "wins": 0, "losses": 0, "gamesStarted": 0,
                "numberOfPitches": pitches}
    game = {"officialGamePk": 10, "startAtUtc": "2026-09-09T18:00:00Z",
            "completedAtUtc": "2026-09-09T21:00:00Z", "gameType": "R",
            "teams": {side: {"team": {"id": tid, "name": side},
                              "teamStats": {"batting": {}},
                              "players": ({"ID151": {"person": {"id": 151},
                                                       "stats": {"pitching": stats(100)}},
                                           "ID152": {"person": {"id": 152},
                                                       "stats": {"pitching": stats(10)}}}
                                          if side == "home" else {})}
                      for side, tid in (("home", 10), ("away", 20))}}
    pitches = ([{"game_pk": "10", "pitcher": "151", "type": "X" if i == 0 else "B",
                 "description": "hit_into_play" if i == 0 else "ball",
                 "launch_speed": "100" if i == 0 else None,
                 "launch_speed_angle": "6" if i == 0 else None,
                 "release_speed": "95"} for i in range(100)]
               + [{"game_pk": "10", "pitcher": "152", "type": "X",
                   "description": "hit_into_play", "launch_speed": "80",
                   "launch_speed_angle": "1", "release_speed": "90"}
                  for _ in range(10)])
    values = Features([game], pitches).bullpen_roster_at(
        "2026-09-10T17:50:00Z", "10", ["151", "152"])
    assert values["bullpen_context_barrel_pct_7d"] == pytest.approx(100/11)
    assert values["bullpen_context_avg_ev_allowed_7d"] == pytest.approx(900/11)


def test_pooled_bullpen_statcast_excludes_current_reliever_start():
    starter = {"outs": 3, "earnedRuns": 0, "runs": 0, "hits": 1, "homeRuns": 0,
               "baseOnBalls": 0, "hitBatsmen": 0, "strikeOuts": 2,
               "battersFaced": 4, "wins": 0, "losses": 0, "gamesStarted": 1,
               "numberOfPitches": 3}
    relief = {**starter, "gamesStarted": 0, "numberOfPitches": 2}
    game = {"officialGamePk": 12, "startAtUtc": "2026-09-09T18:00:00Z",
            "completedAtUtc": "2026-09-09T21:00:00Z", "gameType": "R",
            "teams": {side: {"team": {"id": tid, "name": side},
                              "teamStats": {"batting": {}},
                              "players": ({"ID151": {"person": {"id": 151},
                                                       "stats": {"pitching": starter}},
                                           "ID152": {"person": {"id": 152},
                                                       "stats": {"pitching": relief}}}
                                          if side == "home" else {})}
                      for side, tid in (("home", 10), ("away", 20))}}
    pitches = ([{"game_pk": "12", "pitcher": "151", "type": "B",
                 "description": "ball", "release_speed": "99"} for _ in range(3)]
               + [{"game_pk": "12", "pitcher": "152", "type": "B",
                   "description": "ball", "release_speed": "91"} for _ in range(2)])
    values = Features([game], pitches).bullpen_roster_at(
        "2026-09-10T17:50:00Z", "10", ["151", "152"])
    assert values["bullpen_context_velocity_7d"] == 91


def test_pitch_matchup_requires_full_starter_mix_coverage():
    batting = {"atBats": 4, "hits": 2, "baseOnBalls": 1, "hitByPitch": 0,
               "sacFlies": 0, "doubles": 1, "triples": 0, "homeRuns": 0,
               "strikeOuts": 1}
    game = {"officialGamePk": 11, "startAtUtc": "2026-09-01T18:00:00Z",
            "completedAtUtc": "2026-09-01T21:00:00Z", "gameType": "R",
            "teams": {side: {"team": {"id": tid, "name": side},
                              "teamStats": {"batting": batting},
                              "players": ({"ID101": {"person": {"id": 101},
                                                       "stats": {"batting": batting}}}
                                          if side == "home" else {})}
                      for side, tid in (("home", 10), ("away", 20))}}
    pitches = [{"game_pk": "11", "pitcher": "500", "batter": "101", "pitch_type": "SL",
                "type": "X", "description": "hit_into_play", "woba_denom": "1",
                "woba_value": ".4", "estimated_woba_using_speedangle": ".5"}]
    pitches += [{"game_pk": "11", "pitcher": "500", "batter": str(200+i), "pitch_type": "FF",
                 "type": "B", "description": "ball", "woba_denom": "0"} for i in range(9)]
    profiles, features = Features([game], pitches).lineup_batters_at(
        "2026-09-10T17:50:00Z", list(range(101, 110)), "500", "R",
        game_date="2026-09-10")
    assert profiles[0]["windows"]["30d"]["pitch_type_xwoba"]["SL"]["xwoba"] == .5
    assert features["lineup_pitch_type_matchup_xwoba_30d"] is None
