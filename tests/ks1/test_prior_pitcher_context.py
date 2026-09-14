from copy import deepcopy
import json

import pytest

from ks1.features import Features
from ks1.prior_pitcher_context import PriorPitcherContext, digest, verified_reconstruction
from ks1.table import build

STATS = dict(outs=18, earnedRuns=2, runs=3, hits=4, homeRuns=1,
             baseOnBalls=2, hitBatsmen=1, strikeOuts=7, battersFaced=25,
             wins=1, losses=0, gamesStarted=1, numberOfPitches=90)
SOURCE = dict(bucket="retained", key="mlb/development-data/research-v1/artifacts/prior-games.json",
              versionId="version1", sha256="a"*64, provider="MLB Stats API",
              retrieved_at="2026-09-14T04:00:00Z", complete_years=[2025, 2026])


def full_game(pk, date, pitcher_id, stats):
    batting = dict(atBats=30, hits=8, baseOnBalls=3, hitByPitch=0,
                   sacFlies=0, doubles=1, triples=0, homeRuns=1)
    teams = {}
    for side, tid in (("home", 1), ("away", 2)):
        pid = pitcher_id + (side == "away")
        teams[side] = {"team": {"id": tid, "name": side},
                       "teamStats": {"batting": batting},
                       "players": {"ID"+str(pid): {"person": {"id": pid},
                                                  "stats": {"pitching": dict(stats)}}}}
    return {"officialGamePk": pk, "startAtUtc": date+"T20:00:00Z",
            "completedAtUtc": date+"T23:00:00Z", "gameType": "R", "teams": teams}


def history():
    # A five-man rotation. The next starter on August 11 is the August 6 starter.
    return [full_game(i, f"2026-08-{i:02d}", 100+2*((i-1) % 5), STATS)
            for i in range(1, 11)]


def target():
    return dict(game_id="99", date="2026-08-11", commence_time="2026-08-11T20:00:00Z",
                as_of_timestamp="2026-08-11T19:50:00Z", home_id="1", away_id="2",
                home_starter_id=None, away_starter_id=None,
                home_starter_status="missing_pregame_evidence",
                away_starter_status="missing_pregame_evidence")


def attach(engine, row):
    proofs = {side: engine.at(row, side) for side in ("home", "away")}
    for side, proof in proofs.items():
        row.update({side+"_pitcher_context_"+key: value for key, value in proof["metrics"].items()})
    row["reconstructed_pitcher_context_proof"] = json.dumps(proofs)
    return row


def test_projection_matches_live_formulas_and_is_explicitly_unconfirmed():
    games, row = history(), target()
    features = Features(games)
    projected = attach(PriorPitcherContext(features.rows, SOURCE), row)
    assert verified_reconstruction(projected)
    proofs = json.loads(projected["reconstructed_pitcher_context_proof"])
    assert proofs["home"]["pitcher_id"] == "100"
    assert proofs["away"]["pitcher_id"] == "101"
    assert projected["home_starter_id"] is None
    for side, pid in (("home", "100"), ("away", "101")):
        live = features.at(row["as_of_timestamp"], row[side+"_id"], pid)
        assert proofs[side]["metrics"] == {key: live["pitcher_context_"+key]
                                          for key in proofs[side]["metrics"]}


def test_target_future_same_day_and_later_completed_games_cannot_change_projection():
    before = PriorPitcherContext(Features(history()).rows, SOURCE).at(target(), "home")
    extras = [full_game(99, "2026-08-11", 666, STATS),
              full_game(98, "2026-08-12", 777, STATS),
              full_game(97, "2026-08-11", 888, STATS),
              full_game(96, "2026-08-10", 999, STATS)]
    extras[-1]["completedAtUtc"] = "2026-08-12T23:00:00Z"
    extras[-2]["completedAtUtc"] = "2026-08-11T15:00:00Z"
    after = PriorPitcherContext(Features(history()+extras).rows, SOURCE).at(target(), "home")
    assert before == after


def test_observed_identity_wins_over_rotation_projection():
    row = target()
    row.update(home_starter_id="104", home_starter_status="observed_versioned_t10")
    row = attach(PriorPitcherContext(Features(history()).rows, SOURCE), row)
    assert verified_reconstruction(row)
    proof = json.loads(row["reconstructed_pitcher_context_proof"])["home"]
    assert proof["pitcher_id"] == "104"
    assert proof["identity_mode"] == "observed_pregame_identity_reconstructed_stats"


@pytest.mark.parametrize("mutation", ["swapped_side", "value", "identity", "late_input", "target_input", "source", "late_cutoff"])
def test_qualification_rejects_unbound_or_temporally_invalid_side(mutation):
    row = attach(PriorPitcherContext(Features(history()).rows, SOURCE), target())
    proofs = json.loads(row["reconstructed_pitcher_context_proof"])
    proof = proofs["away"]
    if mutation == "swapped_side":
        proofs["away"] = deepcopy(proofs["home"])
    elif mutation == "value":
        row["away_pitcher_context_quality"] += 1
    elif mutation == "identity":
        row["away_starter_id"] = "999"
    elif mutation == "late_input":
        proof["inputs"][0]["completed_at"] = "2026-08-12T00:00:00Z"
    elif mutation == "target_input":
        proof["inputs"][0]["game_id"] = row["game_id"]
    elif mutation == "source":
        proof["source"]["versionId"] = "null"
    else:
        row["as_of_timestamp"] = "2026-08-11T19:51:00Z"
    # Even a newly signed malformed proof cannot cross temporal/identity gates.
    for proof in proofs.values():
        proof["sha256"] = digest({k: v for k, v in proof.items() if k != "sha256"})
    row["reconstructed_pitcher_context_proof"] = json.dumps(proofs)
    assert not verified_reconstruction(row)


def test_missing_source_history_or_counts_remain_missing():
    rows = Features(history()).rows
    assert PriorPitcherContext(rows, {}).at(target(), "home") is None
    assert PriorPitcherContext(rows, {**SOURCE, "complete_years": [2025]}).at(target(), "home") is None
    assert PriorPitcherContext(rows[:4], SOURCE).at(target(), "home") is None
    for row in rows:
        for player in row["players"]:
            if player["id"] == "100":
                player["stats"].pop("hitBatsmen", None)
    assert PriorPitcherContext(rows, SOURCE).at(target(), "home") is None


def test_table_reconstructs_two_sides_without_using_target_box_identity():
    games = history()+[full_game(99, "2026-08-11", 999, STATS)]
    sch = {"gamePk": 99, "gameDate": "2026-08-11T20:00:00Z", "gameType": "R",
           "season": "2026", "teams": {s: {"team": games[-1]["teams"][s]["team"]}
                                        for s in ("home", "away")},
           "status": {"abstractGameState": "Preview"}}
    bundle = {"full": games, "schedule": [sch], "official_history_source": SOURCE}
    row = build(bundle)[0].to_pylist()[0]
    assert row["home_actual_starter_id"] == "999"
    assert row["home_starter_id"] is None
    assert verified_reconstruction(row)
    proof = row["reconstructed_pitcher_context_proof"]
    bundle["full"][-1] = full_game(99, "2026-08-11", 666, {**STATS, "earnedRuns": 20})
    assert build(bundle)[0].to_pylist()[0]["reconstructed_pitcher_context_proof"] == proof
