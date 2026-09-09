from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math

import pytest

import mlb_successor_model_v2 as model
import mlb_successor_runtime_v1 as runtime
import mlb_fundamentals_snapshot_v2 as snapshots
import mlb_statsapi_starter_context as source
import mlb_ml_dual_model_v2 as r8

NOW = datetime(2026, 9, 10, 17, 20, tzinfo=timezone.utc)
DEPLOYMENT = {"gitSha": "a"*40, "templateSha256": "b"*64}


class Memory:
    def __init__(self): self.data = {}
    def get(self, key): return deepcopy(self.data.get(key))
    def once(self, key, value):
        if key in self.data and model.fingerprint(value) != model.fingerprint(self.data[key]):
            raise ValueError("immutable conflict")
        self.data[key] = deepcopy(value)
        return deepcopy(value)
    def status(self, mode, value): self.data["STATUS#"+mode] = deepcopy(value)
    def predictions(self, day):
        return [deepcopy(v) for k, v in self.data.items() if k.startswith("PREDICTION#"+day+"#")]


def locked_row(day="2026-09-10", index=0):
    observed = day+"T17:00:00+00:00"
    commence = day+"T18:00:00+00:00"
    group = {"source_status": "PARTIAL", "source_provider": "MLB Stats API",
             "dataset": source.VERSION, "home_starter_era": 3.1, "away_starter_era": 4.6,
             "home_starter_k_minus_bb_pct": 20., "away_starter_k_minus_bb_pct": 10.,
             "sourceProvenance": {"provider": "MLB Stats API", "dataset": source.VERSION,
                 "endpoint": "https://statsapi.mlb.com/api/v1/people", "retrievedAtUtc": observed,
                 "sourceEffectiveAtUtc": observed, "payloadFingerprint": "c"*64},
             "home_pitcher_id": 101, "away_pitcher_id": 202}
    row = {"gameId": f"mlb_statsapi:{824000+index}", "officialGamePk": str(824000+index),
           "slateDateEt": day, "commenceTime": commence, "homeTeam": "Home", "awayTeam": "Away",
           "predictionSourcePullAt": observed, "predictionSourcePullId": "test-pull",
           "advanced_context": {"fip_xfip": group}, "predictedSide": "home",
           "homeMarketDeVigProbability": .5, "awayMarketDeVigProbability": .5,
           "marketProbabilitySourceAtUtc": observed, "marketProbabilityVersion": "test",
           "marketProbabilityFingerprint": "d"*64,
           "canonicalLockAuthority": {"learningEligible": True}}
    snap = snapshots.build(row, captured_at_utc=observed)
    row["fundamentalsSnapshotV2"] = snap
    row["featureSnapshot"] = {"fundamentalsSnapshotV2": snap, "fingerprint": "e"*64,
        "sourcePullAtUtc": observed, "lockAtUtc": day+"T17:15:00+00:00",
        "features": {k: 0. for k in set(r8.OUTCOME_FEATURES+r8.RELIABILITY_FEATURES)}}
    row["featureSnapshot"]["features"]["deltaGapHome"] = 1.
    return row


def freeze():
    candidate = {"model": {"features": ["deltaGapHome"], "means": {"deltaGapHome": 0.},
        "scales": {"deltaGapHome": 1.}, "weights": [0., math.log(9)], "base": "marketHomeProbability"},
        "calibrator": None}
    material = {"version": model.VERSION, "experimentId": model.EXPERIMENT_ID,
        "protocolFingerprint": model.fingerprint(model.PROTOCOL), "candidate": candidate,
        "developmentCounts": {"train": 300, "calibration": 50, "selection": 50},
        "firstProspectiveSlateDate": "2026-09-10", "frozenAtUtc": "2026-09-09T20:00:00+00:00",
        "deploymentIdentity": DEPLOYMENT}
    return {**material, "artifactDigest": model.fingerprint(material)}


def passing_evidence(frozen, count=100):
    rows = []
    for i in range(count):
        day = (NOW.date()+timedelta(days=i//10)).isoformat()
        record = {"gameId": f"game-{i}", "officialGamePk": str(824000+i), "slateDateEt": day,
            "commenceTime": day+"T18:00:00+00:00", "featureLockAtUtc": day+"T17:15:00+00:00",
            "homeTeam": "Home", "awayTeam": "Away", "deltaGapHome": 1. if i%2 else -1.,
            "marketHomeProbability": .5, "marketAwayProbability": .5}
        record["inputFingerprint"] = model.input_fingerprint(record)
        p = model.score(record, frozen["candidate"])
        entry = {"version": runtime.VERSION, "artifactDigest": frozen["artifactDigest"], "record": record,
            "capturedAtUtc": day+"T17:20:00+00:00", "homeProbability": p, "awayProbability": 1-p,
            "predictedSide": "home" if p >= .5 else "away", "outcomeKnownAtCapture": False}
        label = int(p >= .5) if i%20 not in (0, 1) else 1-int(p >= .5)
        rows.append({"prediction": entry, "homeWon": label, "inputFingerprint": record["inputFingerprint"]})
    return {"version": runtime.VERSION, "artifactDigest": frozen["artifactDigest"],
        "sealedAtUtc": "2026-09-21T08:00:00+00:00", "testCanBeReopened": False, "rows": rows}


def test_real_snapshot_rates_and_label_free_parity_leave_r8_unchanged():
    row = locked_row()
    before = deepcopy(row)
    record = model.record(row, labeled=False)
    assert record["starterEraGapHome"] == pytest.approx(-1.5)
    assert record["starterKMinusBbGapHome"] == 10.
    assert record["starterRatesMissing"] == 0
    labeled = {**row, "winner": "Home", "correct": True}
    assert model.record(labeled, labeled=True)["inputFingerprint"] == record["inputFingerprint"]
    assert row == before
    assert r8._strict_features(row, row["featureSnapshot"])["starterCompositeGapHome"] is None


@pytest.mark.parametrize("mode", ["tamper", "late", "identity", "label", "boolean_rate"])
def test_bad_source_or_outcomes_cannot_enter_new_feature_model(mode):
    row = locked_row()
    snap = row["fundamentalsSnapshotV2"]
    if mode == "tamper": snap["groups"]["starter_quality"]["values"]["homeEra"] = 900
    if mode == "late":
        snap["groups"]["starter_quality"]["retrievedAtUtc"] = "2026-09-10T17:16:00+00:00"
        snap["fingerprint"] = snapshots.fingerprint_for_snapshot(snap)
    if mode == "identity": row["officialGamePk"] = "999"
    if mode == "label": row["homeWon"] = 1
    if mode == "boolean_rate":
        snap["groups"]["starter_quality"]["values"]["homeEra"] = True
        snap["fingerprint"] = snapshots.fingerprint_for_snapshot(snap)
        assert model.record(row, labeled=False)["starterRatesMissing"] == 1.
    else:
        with pytest.raises(ValueError): model.record(row, labeled=False)


def training_records(n=450):
    return [{"slateDateEt": (NOW.date()+timedelta(days=i//15)).isoformat(), "officialGamePk": str(i),
             "marketHomeProbability": .45+.1*(i%2), "homeWon": i%2,
             "deltaGapHome": (i%7)/7, "homeAwayVelocityPpHr60mDiff": (i%3)/3,
             "starterEraGapHome": .5*(i%2), "starterKMinusBbGapHome": (i%5)-2,
             "starterRatesMissing": 0., "lineupOpsGapHome": .1*(i%3),
             "bullpenPitches3dGapHome": 10*(i%4), "lineupOpsMissing": 0., "bullpenWorkloadMissing": 0.} for i in range(n)]


def test_lambda_fit_matches_independent_scipy_optimizer():
    import mlb_challenger_benchmark as reference
    data = training_records(80)
    fitted = model.fit(data, model.FEATURES, .1)
    expected = reference.fit_adjustment(data, model.FEATURES, .1)
    for row, p in zip(data, reference.predict(data, expected)):
        assert model.predict(row, fitted) == pytest.approx(p, abs=1e-6)


def test_missing_starter_history_waits_without_manufacturing_a_model():
    data = training_records()
    for r in data: r.update(starterRatesMissing=1., starterEraGapHome=None, starterKMinusBbGapHome=None)
    report = model.development(data)
    assert report["status"] == "ACCUMULATING_TEAM_CONTEXT_DEVELOPMENT_DATA"
    assert "candidate" not in report
    assert report["observedStarterCounts"] == {"train": 0, "calibration": 0, "selection": 0}


def test_development_has_disjoint_whole_slates_and_fixed_configuration_grid():
    data = training_records()
    before = deepcopy(data)
    report = model.development(data)
    assert report["counts"] == {"train": 345, "calibration": 45, "selection": 60}
    assert len(report["comparisons"]) == 6
    assert len(set(report["partitionFingerprints"].values())) == 3
    assert data == before


def test_capture_is_idempotent_and_rejects_late_or_labeled_rows():
    repo = Memory()
    repo.once("FROZEN", freeze())
    row = locked_row()
    assert runtime.capture(repo, [row], lambda: NOW, DEPLOYMENT)["capturedCount"] == 1
    assert runtime.capture(repo, [row], lambda: NOW, DEPLOYMENT)["existingCount"] == 1
    assert runtime.capture(repo, [locked_row(index=1)], lambda: NOW+timedelta(hours=1), DEPLOYMENT)["capturedCount"] == 0
    assert runtime.capture(repo, [{**locked_row(index=2), "winner": "Home"}], lambda: NOW, DEPLOYMENT)["capturedCount"] == 0


def test_review_requires_exact_qualified_evidence_and_never_allows_wagers():
    repo, frozen = Memory(), freeze()
    evidence = passing_evidence(frozen)
    repo.once("FROZEN", frozen)
    repo.once("QUALIFICATION", evidence)
    assert runtime.qualification_blockers(evidence, frozen) == []
    assert runtime.authority(repo) is None
    with pytest.raises(ValueError, match="exact model"):
        runtime.review_and_activate(repo, artifact_digest="wrong", qualification_digest=model.fingerprint(evidence), reviewer="operator", now=NOW+timedelta(days=12))
    active = runtime.review_and_activate(repo, artifact_digest=frozen["artifactDigest"], qualification_digest=model.fingerprint(evidence), reviewer="operator", now=NOW+timedelta(days=12))
    assert active["automaticWagerAllowed"] is False
    assert active["playabilityAuthorityEnabled"] is False
    assert runtime.authority(repo)["frozen"]["artifactDigest"] == frozen["artifactDigest"]
    repo.data["QUALIFICATION"]["rows"][0]["homeWon"] ^= 1
    with pytest.raises(ValueError): runtime.authority(repo)


def test_failed_or_insufficient_test_cannot_activate_even_with_true_flag():
    for count in (20, 100):
        repo, frozen = Memory(), freeze()
        evidence = passing_evidence(frozen, count)
        if count == 100:
            for r in evidence["rows"]: r["homeWon"] = 1-r["homeWon"]
        evidence["directionQualified"] = True
        repo.once("FROZEN", frozen)
        repo.once("QUALIFICATION", evidence)
        with pytest.raises(ValueError, match="qualification failed"):
            runtime.review_and_activate(repo, artifact_digest=frozen["artifactDigest"], qualification_digest=model.fingerprint(evidence), reviewer="operator", now=NOW+timedelta(days=12))
        assert repo.get("ACTIVE") is None


def test_sealed_test_never_reopens_when_later_labels_change():
    repo, frozen = Memory(), freeze()
    evidence = passing_evidence(frozen)
    repo.once("QUALIFICATION", evidence)
    result = runtime.evaluate_prospective(repo, [], frozen, NOW+timedelta(days=20))
    assert result["qualification"]["qualificationDigest"] == model.fingerprint(evidence)
    assert repo.get("QUALIFICATION") == evidence


def test_public_serving_uses_only_reviewed_artifact_predictions_without_writes():
    repo, frozen = Memory(), freeze()
    evidence = passing_evidence(frozen)
    repo.once("FROZEN", frozen)
    repo.once("QUALIFICATION", evidence)
    runtime.review_and_activate(repo, artifact_digest=frozen["artifactDigest"], qualification_digest=model.fingerprint(evidence), reviewer="operator", now=NOW+timedelta(days=12))
    entry = evidence["rows"][0]["prediction"]
    repo.once("PREDICTION#2026-09-10#824000", entry)
    before = deepcopy(repo.data)
    result = runtime.public_predictions("2026-09-10", 10, frozen["artifactDigest"], repo)
    assert result["count"] == 1
    assert result["predictions"][0]["artifactDigest"] == frozen["artifactDigest"]
    assert result["predictions"][0]["automaticWagerAllowed"] is False
    assert repo.data == before
    with pytest.raises(ValueError): runtime.public_predictions("2026-09-10", 10, "wrong", repo)


def test_dynamodb_numeric_round_trip_preserves_fingerprints():
    value = {"int": 1, "float": 1., "noninteger": .125, "boolean": True}
    round_trip = {"int": Decimal("1"), "float": Decimal("1.0"), "noninteger": Decimal(".125"), "boolean": True}
    assert model.fingerprint(value) == model.fingerprint(round_trip)
    assert model.fingerprint({"x": True}) != model.fingerprint({"x": 1})


def test_fresh_test_seals_at_first_complete_whole_slate_and_cannot_change():
    repo, frozen = Memory(), freeze()
    source = passing_evidence(frozen, 110)
    final_rows = []
    for row in source["rows"]:
        entry, label = row["prediction"], row["homeWon"]
        record = entry["record"]
        repo.once(f'PREDICTION#{record["slateDateEt"]}#{record["officialGamePk"]}', entry)
        final_rows.append({**record, "homeWon": label})
    result = runtime.evaluate_prospective(repo, final_rows, frozen, NOW+timedelta(days=15))
    assert result["qualification"]["prospectiveCount"] == 100
    sealed = deepcopy(repo.get("QUALIFICATION"))
    for row in final_rows: row["homeWon"] = 1-row["homeWon"]
    runtime.evaluate_prospective(repo, final_rows, frozen, NOW+timedelta(days=16))
    assert repo.get("QUALIFICATION") == sealed


def test_incomplete_slate_and_changed_lock_inputs_cannot_be_cherry_picked():
    repo, frozen = Memory(), freeze()
    source = passing_evidence(frozen, 100)
    final_rows = []
    for i, row in enumerate(source["rows"]):
        entry, record = row["prediction"], row["prediction"]["record"]
        if i: repo.once(f'PREDICTION#{record["slateDateEt"]}#{record["officialGamePk"]}', entry)
        final_rows.append({**record, "homeWon": row["homeWon"]})
    result = runtime.evaluate_prospective(repo, final_rows, frozen, NOW+timedelta(days=15))
    assert result["prospectiveCount"] == 90
    assert result["skippedIncompleteSlateDates"] == ["2026-09-10"]
    assert repo.get("QUALIFICATION") is None
    final_rows[10]["inputFingerprint"] = "changed"
    with pytest.raises(ValueError, match="changed frozen"):
        runtime.evaluate_prospective(repo, final_rows, frozen, NOW+timedelta(days=15))


def test_repository_immutable_conflicts_corruption_and_pagination():
    class Conditional(Exception):
        response = {"Error": {"Code": "ConditionalCheckFailedException"}}
    class Table:
        def __init__(self): self.rows = {}; self.queries = []
        def put_item(self, Item, **kwargs):
            key = (Item["PK"], Item["SK"])
            if kwargs.get("ConditionExpression") and key in self.rows: raise Conditional()
            self.rows[key] = deepcopy(Item)
        def get_item(self, Key, ConsistentRead):
            assert ConsistentRead is True
            return {"Item": deepcopy(self.rows.get((Key["PK"], Key["SK"])))}
        def query(self, **kwargs):
            self.queries.append(kwargs)
            assert kwargs["ConsistentRead"] is True
            items = list(self.rows.values())
            return {"Items": items[1:]} if "ExclusiveStartKey" in kwargs else {"Items": items[:1], "LastEvaluatedKey": {"PK": "cursor", "SK": "cursor"}}
    table = Table()
    repo = runtime.Repository(table)
    assert repo.once("A", {"value": .5}) == {"value": .5}
    assert repo.once("A", {"value": .5}) == {"value": Decimal(".5")}
    with pytest.raises(ValueError, match="immutable"): repo.once("A", {"value": .6})
    repo.once("B", {"value": 1.})
    assert len(repo.predictions("2026-09-10")) == 2
    assert len(table.queries) == 2
    table.rows[(runtime.PK, "A")]["data"]["value"] = 9
    with pytest.raises(ValueError, match="fingerprint"): repo.get("A")


def test_actual_starter_adapter_output_is_observed_by_successor():
    row = locked_row()
    moment = datetime.fromisoformat(row["predictionSourcePullAt"])
    game = {"gamePk": int(row["officialGamePk"]), "gameDate": row["commenceTime"],
            "status": {"abstractGameState": "Preview"},
            "teams": {side: {"probablePitcher": {"id": identity}}
                      for side, identity in (("home", 101), ("away", 202))}}
    people = [{"id": identity, "stats": [{"group": {"displayName": "pitching"},
               "type": {"displayName": "season"}, "splits": [{"season": "2026",
               "sport": {"id": 1}, "gameType": "R", "stat": {"era": era,
               "strikeOuts": 90, "baseOnBalls": 20, "battersFaced": 400}}]}]}
              for identity, era in ((101, "3.10"), (202, "4.60"))]
    source._CACHE.clear()
    quality, _ = source.observe(row["slateDateEt"], game,
        {"payload": {"dates": [{"games": [game]}]}},
        lambda *args, **kwargs: {"people": people}, now=lambda: moment)
    row["advanced_context"]["fip_xfip"] = quality
    snapshot = snapshots.build(row, captured_at_utc=moment.isoformat())
    row["fundamentalsSnapshotV2"] = snapshot
    row["featureSnapshot"]["fundamentalsSnapshotV2"] = snapshot
    assert snapshot["groups"]["starter_quality"]["dataset"] == source.DATASET
    assert not snapshots.validate(snapshot)
    assert model.record(row, labeled=False)["starterRatesMissing"] == 0
    assert model.record({**row, "winner": "Home", "correct": True}, labeled=True)["starterRatesMissing"] == 0
    source._CACHE.clear()


@pytest.mark.parametrize("dataset", ["untrusted; " + source.VERSION, source.DATASET + "-modified", ""])
def test_unknown_starter_dataset_cannot_receive_observed_credit(dataset):
    row = locked_row()
    snapshot = row["fundamentalsSnapshotV2"]
    snapshot["groups"]["starter_quality"]["dataset"] = dataset
    snapshot["fingerprint"] = snapshots.fingerprint_for_snapshot(snapshot)
    assert model.record(row, labeled=False)["starterRatesMissing"] == 1


def test_v2_is_a_separate_protocol_and_never_changes_v1():
    import mlb_successor_model_v1 as prior
    assert prior.EXPERIMENT_ID != model.EXPERIMENT_ID
    assert runtime.PK.endswith(model.EXPERIMENT_ID)
    assert prior.FEATURES != model.FEATURES
    assert 'teamObservedTrainMinimum' not in prior.PROTOCOL
    assert model.PROTOCOL['testCanBeReopened'] is False


def test_team_features_require_observations_in_every_partition():
    rows=training_records()
    for row in rows:row.update(lineupOpsMissing=1.,lineupOpsGapHome=None)
    result=model.development(rows)
    assert result['observedTeamCounts']=={'train':0,'calibration':0,'selection':0}
    assert len([b for b in result['blockers'] if 'TEAM_CONTEXT' in b])==3
    assert 'candidate' not in result


def test_team_adapter_snapshot_reaches_new_model_without_label_leakage():
    from tests.unit.test_mlb_statsapi_team_context import fixtures, NOW as moment
    import mlb_statsapi_team_context as team
    game,history,feed,teams,get=fixtures()
    lineup,bullpen=team.observe('2026-09-09',game,history,get,now=lambda:moment)
    row=locked_row(day='2026-09-09')
    row['advanced_context'].update(confirmed_lineups=lineup,bullpen_fatigue=bullpen)
    snap=snapshots.build(row,captured_at_utc=row['predictionSourcePullAt'])
    row['fundamentalsSnapshotV2']=snap;row['featureSnapshot']['fundamentalsSnapshotV2']=snap
    before=deepcopy(row);result=model.record(row,labeled=False)
    assert result['lineupOpsMissing']==result['bullpenWorkloadMissing']==0
    assert result['lineupOpsGapHome']==pytest.approx(0.)
    assert model.record({**row,'winner':'Home','correct':True},labeled=True)['inputFingerprint']==result['inputFingerprint']
    assert row==before
