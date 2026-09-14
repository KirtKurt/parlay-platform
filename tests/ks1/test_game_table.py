from copy import deepcopy
import hashlib
import io
import json

import pyarrow.compute as pc
import pytest
from botocore.exceptions import ClientError

from ks1.features import Features, offense
from ks1.inventory import encode
from ks1.passive_context import CONTRACT as LINEUP_BULLPEN_CONTRACT
from ks1.historical_starters import (KS1_STARTER_PROFILE_CONTRACT,
                                     V8_MANIFEST_VERSION, V8_SNAPSHOT_VERSION,
                                     historical_context_index)
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


def test_starter_dictionary_separates_game_log_statcast_and_unavailable_sources():
    _, _, dictionary, _, _ = build(fixture())
    fields = {row['column']: row for row in dictionary}
    assert 'completed compact/full game boxes' in fields['home_starter_era_30d']['source']
    assert 'Baseball Savant' in fields['home_starter_xwoba_30d']['source']
    assert 'stored null' in fields['home_starter_xera_30d']['source']
    assert fields['home_starter_xera_30d']['role'] == 'feature'
    assert 'official MLB game boxes' in fields['home_lineup_ops_7d']['source']
    assert 'Baseball Savant' in fields['home_lineup_xwoba_7d']['source']
    assert 'official MLB game boxes' in fields['home_lineup_ops_talent']['source']
    assert 'plate-appearance weighted' in fields['home_lineup_ops_talent']['description']


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
    features = {"marketHomeProbability": 0.55, "home_starter_xwoba_30d": 0.31}
    snapshot = {"officialGamePk": 3, "commenceTime": "2026-08-03T20:00:00Z",
                "capturedAtUtc": "2026-08-03T19:20:00Z", "featureCutoffUtc": "2026-08-03T19:30:00Z",
                "originalObservation": True, "outcomeKnownAtCapture": False,
                "features": features, "featureFingerprint": hashlib.sha256(encode(features)).hexdigest(),
                "playerWindows": {"teams": {
                    "home": {"teamId": 1, "starterId": 99, "lineupConfirmed": True,
                             "battingOrder": list(range(100, 109)),
                             "players": [{"id": 99, "name": "Observed starter", "pitchHand": "L"},
                                         *[{"id": pid, "lineupSlot": slot, "batSide": "R"}
                                           for slot, pid in enumerate(range(100, 109), 1)]]},
                    "away": {"teamId": 2, "starterId": 199, "lineupConfirmed": True,
                             "battingOrder": list(range(200, 209)),
                             "players": [{"id": 199, "name": "Away starter", "pitchHand": "R"},
                                         *[{"id": pid, "lineupSlot": slot, "batSide": "L"}
                                           for slot, pid in enumerate(range(200, 209), 1)]]}}}}
    bundle["snapshots"] = [snapshot]
    table, *_ = build(bundle)
    row = table.to_pylist()[-1]
    assert row["home_starter_id"] == "99"
    assert row["home_starter_opponent_lhb_pct"] == 100
    assert row["away_starter_opponent_rhb_pct"] == 100
    assert row["home_starter_xwoba_30d"] == 0.31
    assert row["rolling_feature_evidence"] == "immutable original snapshot starter profile"
    profile = {
        "contract": KS1_STARTER_PROFILE_CONTRACT,
        "as_of": "2026-08-03T19:40:00Z",
        "history_as_of": "2026-08-03T19:30:00Z",
        "coverage": {"current_season_context": True},
        "sides": {side: {"starter_id": pid, "metrics": {
            "context_quality": quality, "context_command": command,
            "context_recent_form": command, "expected_innings_last5": 5.5},
            "window_statuses": {"30d": "COMPLETE", "last3": "SOURCE_INCOMPLETE"}}
            for side, pid, quality, command in (
                ("home", "99", -3.2, 18.0), ("away", "199", -4.1, 12.0))},
    }
    semantic = {key: value for key, value in profile.items()
                if key not in ("as_of", "history_as_of")}
    profile["semantic_sha256"] = hashlib.sha256(encode(semantic)).hexdigest()
    profile["sha256"] = hashlib.sha256(encode(profile)).hexdigest()
    bundle["published_predictions"] = [{
        "row": {"game_id": "3", "date": "2026-08-03",
                "commence_time": "2026-08-03T20:00:00Z", "as_of": profile["as_of"],
                "home_id": "1", "away_id": "2", "home_starter_id": "99",
                "away_starter_id": "199", "starter_profile_contract": KS1_STARTER_PROFILE_CONTRACT,
                "starter_profile_sha256": profile["sha256"],
                "starter_profile_semantic_sha256": profile["semantic_sha256"],
                "starter_profile_json": encode(profile).decode()},
        "evidence": {"bucket": "b", "key": "k", "version_id": "v1",
                     "stored_at": "2026-08-03T19:45:00Z", "sha256": "a"*64}}]
    table, *_ = build(bundle)
    row = table.to_pylist()[-1]
    assert row["pregame_evidence"] == "original_snapshot+versioned_ks1_t10_prediction"
    assert row["home_pitcher_context_quality"] == -3.2
    assert row["pitcher_context_evidence"] == "frozen_versioned_ks1_profile"
    assert row["as_of_timestamp"] == "2026-08-03T19:40:00+00:00"

    # A later immutable T-10 observation supersedes the older snapshot's
    # probable starter.  The replacement may use its own frozen context, but
    # must never inherit starter-specific fields captured for the old pitcher.
    replacement = deepcopy(profile)
    replacement["sides"]["home"]["starter_id"] = "98"
    replacement.pop("sha256")
    replacement.pop("semantic_sha256")
    semantic = {key: value for key, value in replacement.items()
                if key not in ("as_of", "history_as_of")}
    replacement["semantic_sha256"] = hashlib.sha256(encode(semantic)).hexdigest()
    replacement["sha256"] = hashlib.sha256(encode(replacement)).hexdigest()
    prediction = bundle["published_predictions"][0]["row"]
    prediction.update(
        home_starter_id="98",
        starter_profile_sha256=replacement["sha256"],
        starter_profile_semantic_sha256=replacement["semantic_sha256"],
        starter_profile_json=encode(replacement).decode(),
    )
    table, *_ = build(bundle)
    row = table.to_pylist()[-1]
    assert row["home_starter_id"] == "98"
    assert row["home_starter_status"] == "observed_versioned_t10_replacement"
    assert row["home_starter_xwoba_30d"] is None
    assert row["home_starter_opponent_lhb_pct"] is None
    assert row["home_pitcher_context_quality"] == -3.2
    assert row["pitcher_context_evidence"] == "frozen_versioned_ks1_profile"

    bundle["published_predictions"] = []
    snapshot["capturedAtUtc"] = "2026-08-03T20:01:00Z"
    table, *_ = build(bundle)
    assert table["home_starter_id"].null_count == 3


def test_versioned_t10_prediction_supplies_pregame_starter_identity():
    bundle = fixture()
    bundle["published_predictions"] = [{
        "row": {"game_id": "3", "date": "2026-08-03",
                "commence_time": "2026-08-03T20:00:00Z", "as_of": "2026-08-03T19:40:00Z",
                "home_id": "1", "away_id": "2", "home_starter_id": "99",
                "home_starter_name": "Observed", "away_starter_id": "199",
                "away_starter_name": "Away observed"},
        "evidence": {"bucket": "b", "key": "date=2026-08-03/predictions.parquet",
                     "version_id": "v1", "stored_at": "2026-08-03T19:45:00Z",
                     "sha256": "a"*64}}]
    table, *_ = build(bundle)
    row = table.to_pylist()[-1]
    assert row["home_starter_id"] == "99"
    assert row["home_starter_status"] == "observed_versioned_t10"
    assert row["pregame_evidence"] == "versioned_ks1_t10_prediction"
    assert row["pregame_version_id"] == "v1"
    assert row["as_of_timestamp"] == "2026-08-03T19:40:00Z"


def test_frozen_team_profile_restores_confirmed_lineup_metadata():
    bundle = fixture()
    profile = {
        "contract": LINEUP_BULLPEN_CONTRACT,
        "as_of": "2026-08-03T19:40:00Z",
        "history_as_of": "2026-08-03T19:30:00Z",
        "statcast_as_of": "2026-08-03T19:30:00Z",
        "game_id": "3", "commence_time": "2026-08-03T20:00:00Z",
        "coverage_status": "SUPPORTED_V1_COMPLETE",
        "sides": {
            "home": {"team_id": "1", "lineup_ids": [str(i) for i in range(101, 110)],
                     "opposing_starter_id": "199",
                     "features": {"lineup_quality_ops": .750,
                                  "lineup_pitch_type_matchup_xwoba_30d": .550}},
            "away": {"team_id": "2", "lineup_ids": [str(i) for i in range(201, 210)],
                     "opposing_starter_id": "99",
                     "features": {"lineup_quality_ops": .725}},
        },
    }
    semantic = {key: value for key, value in profile.items() if key != "as_of"}
    profile["semantic_sha256"] = hashlib.sha256(encode(semantic)).hexdigest()
    profile["sha256"] = hashlib.sha256(encode(profile)).hexdigest()
    bundle["published_predictions"] = [{
        "row": {"game_id": "3", "date": "2026-08-03",
                "commence_time": profile["commence_time"], "as_of": profile["as_of"],
                "home_id": "1", "away_id": "2",
                "lineup_bullpen_profile_contract": LINEUP_BULLPEN_CONTRACT,
                "lineup_bullpen_profile_sha256": profile["sha256"],
                "lineup_bullpen_profile_semantic_sha256": profile["semantic_sha256"],
                "lineup_bullpen_profile_json": encode(profile).decode()},
        "evidence": {"bucket": "b", "key": "k", "version_id": "v1",
                     "stored_at": "2026-08-03T19:45:00Z", "sha256": "a"*64}}]
    table, *_ = build(bundle)
    row = table.to_pylist()[-1]
    assert row["home_lineup_status"] == "confirmed"
    assert json.loads(row["home_lineup_ids"]) == list(range(101, 110))
    assert row["home_lineup_quality_ops"] == .750
    assert row["home_lineup_pitch_type_matchup_xwoba_30d"] is None
    assert row["lineup_bullpen_context_evidence"] == "frozen_versioned_ks1_profile"


def test_verified_historical_pitcher_summary_accelerates_without_inventing_identity():
    bundle = fixture()
    snapshot = {"version": V8_SNAPSHOT_VERSION,
                "authority": "V8_HISTORICAL_OFFICIAL_CONTEXT_SHADOW_ONLY",
                "officialGamePk": "3", "predictionLockAtUtc": "2026-08-03T19:15:00Z",
                "trainingEligible": True, "pointInTimeVerified": True,
                "postgameFieldsExcluded": True, "sameDayResultsExcluded": True,
                "targetGameOutcomeUsed": False, "selectionUsedOutcomes": False,
                "productionAuthorityChanged": False,
                "featureAvailabilityMode": {"pitchers": "strict_prior_projection"},
                "home": {"starterQuality": -3.2, "starterCommand": 18.0},
                "away": {"starterQuality": -4.1, "starterCommand": 12.0}}
    snapshot["fingerprint"] = hashlib.sha256(encode(snapshot)).hexdigest()
    manifest = {"version": V8_MANIFEST_VERSION, "authority": snapshot["authority"],
                "productionAuthorityChanged": False,
                "selectionUsedOutcomes": False, "eligibleGameCount": 1,
                "records": [{"officialGamePk": "3", "commenceTime": "2026-08-03T20:00:00Z",
                             "predictionLockAtUtc": "2026-08-03T19:15:00Z",
                             "homeTeam": "Home", "awayTeam": "Away",
                             "trainingEligible": True, "snapshot": snapshot}]}
    manifest["manifestDigest"] = hashlib.sha256(encode(manifest)).hexdigest()
    bundle["historical_pitcher_context"] = historical_context_index(
        manifest, {"bucket": "archive", "key": "manifest", "sha256": "b"*64})
    table, *_ = build(bundle)
    row = table.to_pylist()[-1]
    assert row["home_starter_id"] is None
    assert row["home_pitcher_context_quality"] == -3.2
    assert row["historical_pitcher_context_mode"] == "strict_prior_projection"
    assert row["as_of_timestamp"] == "2026-08-03T19:50:00+00:00"


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


@pytest.mark.parametrize("actions,repository,ref,event,allowed", [
    ("true", "KirtKurt/parlay-platform", "refs/heads/main", "push", True),
    ("true", "KirtKurt/parlay-platform", "refs/heads/main", "workflow_dispatch", True),
    ("true", "KirtKurt/parlay-platform", "refs/pull/696/merge", "pull_request", False),
    ("true", "KirtKurt/parlay-platform", "refs/heads/feature", "workflow_dispatch", False),
    ("false", "KirtKurt/parlay-platform", "refs/heads/main", "push", False),
    ("true", "different/repository", "refs/heads/main", "push", False),
])
def test_publish_requires_main_pipeline(monkeypatch, actions, repository, ref, event, allowed):
    # Simulate each context explicitly; the test must not inherit its own CI
    # job's main/PR identity when asserting whether publication is permitted.
    for key, value in {"GITHUB_ACTIONS": actions, "GITHUB_REPOSITORY": repository,
                       "GITHUB_REF": ref, "GITHUB_EVENT_NAME": event}.items():
        monkeypatch.setenv(key, value)
    table, *_ = build(fixture())
    s3 = MemoryS3()
    if allowed:
        assert len(publish(s3, "test", table, [])["partitions"]) == 3
    else:
        with pytest.raises(ValueError, match="restricted"):
            publish(s3, "test", table, [])
        assert s3.writes == []


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
