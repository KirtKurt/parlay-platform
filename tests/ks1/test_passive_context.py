import copy
import hashlib
import json
from decimal import Decimal

import pytest

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
    assert "xwOBA" in profile["unavailable_fields"]
    assert features["home_lineup_observed_batters"] == 9
    assert features["away_bullpen_context_unknown_count"] == 5
    claimed = profile.pop("sha256")
    assert hashlib.sha256(encode(profile)).hexdigest() == claimed
    assert len(history.calls) == 2


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
