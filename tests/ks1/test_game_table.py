from copy import deepcopy
import hashlib
import io
import json

import pyarrow.compute as pc
import pytest
from botocore.exceptions import ClientError

from ks1.features import Features, offense
from ks1.inventory import encode
from ks1.publish import PREFIX, publish
from ks1.table import build, market_for


def game(pk, date, hits=8, completed=None):
    return {"officialGamePk": pk, "startAtUtc": date+"T20:00:00Z",
            "completedAtUtc": completed or date+"T23:00:00Z", "gameType": "R",
            "teams": {side: {"id": tid, "name": side.title(),
                       "batting": {"atBats": 30, "hits": hits, "baseOnBalls": 3, "hitByPitch": 0,
                                   "sacFlies": 0, "doubles": 1, "triples": 0, "homeRuns": 1},
                       "priorStarters": {"strikeOuts": 6, "baseOnBalls": 2, "battersFaced": 24},
                       "relief": {"pitches": 40, "outs": 9}}
                      for side, tid in (("home", 1), ("away", 2))}}


def fixture():
    games = [game(1, "2026-08-01"), game(2, "2026-08-02"), game(3, "2026-08-03")]
    return {"compact": games, "schedule": [
        {"gamePk": g["officialGamePk"], "gameDate": g["startAtUtc"], "season": "2026", "gameType": "R",
         "teams": {side: {"team": {"id": t["id"], "name": t["name"]}, "score": score}
                   for (side, t), score in zip(g["teams"].items(), (4, 2))},
         "status": {"abstractGameState": "Final"}} for g in games]}


def test_target_future_and_later_resumption_excluded():
    prior = game(1, "2026-08-01")
    target = game(2, "2026-08-02", hits=27)
    future = game(3, "2026-08-03", hits=28)
    resumed = game(4, "2026-07-31", hits=29, completed="2026-08-03T23:00:00Z")
    expected = Features([prior]).at("2026-08-02T19:50:00Z", "1")
    assert Features([prior, target, future, resumed]).at("2026-08-02T19:50:00Z", "1") == expected
    assert expected["team_starter_whip_10d"] is None


def test_shrinkage_and_cold_start():
    ordinary = game(1, "2026-08-01")["teams"]["home"]["batting"]
    extreme = {**ordinary, "hits": 30}
    low = offense([extreme], [ordinary])["ops"]
    high = offense([extreme]*100, [ordinary])["ops"]
    assert offense([ordinary], [ordinary])["ops"] < low < high
    assert offense([extreme], [])["ops"] is None


def test_labels_and_postgame_starters_do_not_change_features():
    bundle = fixture()
    before, *_ = build(bundle)
    bundle["schedule"][-1]["teams"]["home"]["score"] = 0
    after, *_ = build(bundle)
    cols = [c for c in before.column_names if "offense" in c or "starter" in c or "bullpen" in c]
    assert before.select(cols).equals(after.select(cols))
    assert before["home_starter_id"].null_count == 3
    assert after["home_win"].to_pylist() == [True, True, False]


def test_date_rebuild_and_full_column_dictionary():
    table, report, dictionary, sample, _ = build(fixture())
    one, *_ = build(fixture(), "2026-08-02")
    assert one.equals(table.filter(pc.equal(table["date"], "2026-08-02")))
    assert set(table.column_names) == {r["column"] for r in dictionary}
    assert report["coverage"]["pct_with_final_score"] == 100
    assert len(sample) == 3


def test_conflicting_final_scores_fail():
    bundle = fixture()
    bundle["finals"] = [{"officialGamePk": 1, "completed": True, "homeScore": 7,
                         "awayScore": 1, "source_key": "stored"}]
    with pytest.raises(ValueError, match="conflicting final scores"):
        build(bundle)


def test_missing_target_box_reuses_exact_existing_crosswalk():
    bundle = fixture()
    bundle["reconstructed"] = [{"officialGamePk": 4, "slateDateEt": "2026-08-04",
                                "commenceTime": "2026-08-04T20:00:00Z",
                                "homeTeam": "Home", "awayTeam": "Away"}]
    table, report, *_ = build(bundle)
    row = table.to_pylist()[-1]
    assert row["game_id"] == "4" and row["home_id"] == "1"
    assert row["home_identity_method"] == "exact_unique_observed_name_crosswalk"
    assert report["exclusions"] == []
    # A same-name second official ID cannot be guessed away.
    extra = game(10, "2026-07-31")
    extra["teams"]["home"]["id"] = 777
    bundle["compact"].append(extra)
    _, report, *_ = build(bundle)
    assert report["exclusions"] == [{"game_id": "4", "reason": "missing_official_team_identity"}]


def test_missing_known_prior_box_does_not_become_zero_or_complete_workload():
    bundle = fixture()
    bundle["finals"] = [{"officialGamePk": 99, "officialDate": "2026-08-02", "completed": True,
                         "homeTeam": "Home", "awayTeam": "Away", "homeScore": 3, "awayScore": 1,
                         "source_key": "retained-final"}]
    table, *_ = build(bundle)
    row = table.to_pylist()[-1]
    assert row["home_missing_history_boxes_75d"] == 1
    assert row["home_history_status"] == "partial_known_missing_boxes"
    assert row["home_bullpen_pitches_1d"] is None
    assert row["home_offense_games_10d"] == 2  # counts expose observed sample


def test_original_starter_requires_timing_identity_and_hash():
    bundle = fixture()
    features = {"marketHomeProbability": 0.55}
    snapshot = {"officialGamePk": 3, "commenceTime": "2026-08-03T20:00:00Z",
                "capturedAtUtc": "2026-08-03T19:20:00Z", "featureCutoffUtc": "2026-08-03T19:30:00Z",
                "originalObservation": True, "outcomeKnownAtCapture": False,
                "features": features, "featureFingerprint": hashlib.sha256(encode(features)).hexdigest(),
                "playerWindows": {"teams": {"home": {"teamId": 1, "starterId": 99,
                                        "players": [{"id": 99, "name": "Observed starter"}]}}}}
    bundle["snapshots"] = [snapshot]
    table, *_ = build(bundle)
    assert table["home_starter_id"].to_pylist()[-1] == "99"
    snapshot["capturedAtUtc"] = "2026-08-03T20:01:00Z"
    table, *_ = build(bundle)
    assert table["home_starter_id"].null_count == 3


class MemoryS3:
    def __init__(self):
        self.objects, self.writes = {}, []

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body
        self.writes.append(Key)
        return {}

    def get_object(self, Bucket, Key, **kwargs):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


def test_atomic_date_scoped_publication_and_noop():
    s3 = MemoryS3()
    table, *_ = build(fixture())
    publish(s3, "test", table, [], require_pipeline=False)
    frozen = deepcopy(s3.objects)
    one, *_ = build(fixture(), "2026-08-02")
    assert publish(s3, "test", one, [], require_pipeline=False)["write_keys"] == []
    changed = fixture()
    changed["schedule"][1]["teams"]["home"]["score"] = 9
    one, *_ = build(changed, "2026-08-02")
    result = publish(s3, "test", one, [], require_pipeline=False)
    assert all(k.startswith(PREFIX+"date=2026-08-02/") for k in result["write_keys"])
    assert all(s3.objects[k] == v for k, v in frozen.items() if "date=2026-08-02/" not in k)
    assert s3.writes[-1].endswith("manifest.json")


def test_publish_requires_main_pipeline():
    table, *_ = build(fixture())
    with pytest.raises(ValueError, match="restricted"):
        publish(MemoryS3(), "test", table, [])


def test_odds_freshness_vig_and_doubleheader_binding():
    row = {"home_team": "Home", "away_team": "Away", "as_of_timestamp": "2026-08-03T19:50:00Z",
           "commence_time": "2026-08-03T20:00:00Z", "market_home_prob": None}
    event = {"eventId": "observed", "commenceTime": row["commence_time"], "bookmakers": [
        {"lastUpdate": "2026-08-03T19:45:00Z", "markets": {"h2h": [
            {"name": "Home", "price": -110}, {"name": "Away", "price": -110}]}}]}
    markets = {("Home", "Away"): [("2026-08-03T19:45:00Z", event, "stored")]}
    assert market_for(row, markets)["market_home_prob"] == 0.5
    event["commenceTime"] = "2026-08-03T23:00:00Z"
    assert market_for(row, markets) == {}
    event["commenceTime"] = row["commence_time"]
    event["bookmakers"][0]["lastUpdate"] = "2026-08-03T19:51:00Z"
    assert market_for(row, markets) == {}
