import copy
from datetime import date, timedelta
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_dual_model_v2 as dual
import mlb_ml_experiment_v2 as experiment
import mlb_ml_walk_forward_v2 as walk_forward


DEPLOYMENT_IDENTITY = {"gitSha": "a" * 40, "templateSha256": "b" * 64}


def row(slate_date, game_index):
    home_won = game_index % 2 == 0
    selected_home = game_index % 3 != 0
    correct = home_won == selected_home
    home_market = 0.46 + (game_index % 9) * 0.01
    source = f"{slate_date}T17:00:00+00:00"
    lock = f"{slate_date}T17:15:00+00:00"
    starter_home = 0.2 + (game_index % 7) * 0.04
    starter_away = 0.45 - (game_index % 5) * 0.03
    bullpen_home = 0.3 + (game_index % 6) * 0.02
    bullpen_away = 0.4 - (game_index % 4) * 0.02
    lineup_home = 95 + (game_index % 12)
    lineup_away = 105 - (game_index % 10)
    snapshot = {
        "version": dual.FUNDAMENTALS_VERSION,
        "fingerprint": f"fundamentals-{slate_date}-{game_index}",
        "groups": {
            "starter_quality": {
                "status": "CONNECTED",
                "values": {
                    "homeComposite": starter_home,
                    "awayComposite": starter_away,
                },
            },
            "bullpen_availability": {
                "status": "CONNECTED",
                "values": {
                    "homeComposite": bullpen_home,
                    "awayComposite": bullpen_away,
                },
            },
            "confirmed_lineups": {
                "status": "CONNECTED",
                "values": {
                    "homeWrcPlus": lineup_home,
                    "awayWrcPlus": lineup_away,
                },
            },
        },
    }
    features = {
        "deltaGapHome": (game_index % 11 - 5) / 10,
        "bookAgreementGapHome": (game_index % 7 - 3) / 10,
        "reversalGapHome": (game_index % 5 - 2) / 10,
        "homeAwayVelocityPpHr60mDiff": (game_index % 13 - 6) / 10,
        "selectedScore": 0.8 if correct else -0.4,
        "selectedDelta": 0.5 if correct else -0.2,
        "selectedBookDivergence": (game_index % 4) / 10,
        "selectedReversalCountFull": game_index % 3,
        "selectedCoverageRatioFull": 0.8 + (game_index % 3) * 0.05,
        "selectedVolatilityPpPerPull180m": (game_index % 5) / 10,
        "selectedHome": 1.0 if selected_home else 0.0,
    }
    return {
        "gameId": f"{slate_date}-{game_index}",
        "officialGamePk": f"{slate_date.replace('-', '')}{game_index:03d}",
        "slateDateEt": slate_date,
        "commenceTime": f"{slate_date}T18:00:00+00:00",
        "labelRetrievedAtUtc": f"{slate_date}T23:59:00+00:00",
        "homeTeam": "Home",
        "awayTeam": "Away",
        "winner": "Home" if home_won else "Away",
        "predictedSide": "home" if selected_home else "away",
        "correct": correct,
        "lockedAmericanOdds": -110,
        "homeMarketDeVigProbability": home_market,
        "awayMarketDeVigProbability": 1.0 - home_market,
        "marketProbabilitySourceAtUtc": source,
        "marketProbabilityVersion": "market-devig-v1",
        "marketProbabilityFingerprint": f"market-{slate_date}-{game_index}",
        "fundamentalsSnapshotV2": snapshot,
        "fundamentalsSnapshotRefV2": {
            "version": snapshot["version"],
            "fingerprint": snapshot["fingerprint"],
        },
        "featureSnapshot": {
            "version": "vector-v2",
            "fingerprint": f"vector-{slate_date}-{game_index}",
            "sourcePullAtUtc": source,
            "lockAtUtc": lock,
            "features": features,
        },
    }


def partitions():
    first = date(2026, 7, 21)
    result = {"train": [], "validation": [], "prospectiveTest": []}
    global_index = 0
    for day_index in range(34):
        slate = (first + timedelta(days=day_index)).isoformat()
        if day_index < 20:
            target = "train"
        elif day_index < 27:
            target = "validation"
        else:
            target = "prospectiveTest"
        for _ in range(15):
            result[target].append(row(slate, global_index))
            global_index += 1
    return result


def manifest(rows):
    value = experiment.new_manifest(
        experiment_id="dual-model-test",
        release_contract_id="release-contract-r1",
        release_cutoff_utc="2026-07-21T00:00:00+00:00",
        feature_vector_version="vector-v2",
        model_feature_schemas={
            "outcome": dual.OUTCOME_FEATURES,
            "reliability": dual.RELIABILITY_FEATURES,
        },
    )
    for name, partition_rows in rows.items():
        value["partitions"][name]["rowCount"] = len(partition_rows)
        value["partitions"][name]["frozen"] = True
        value["partitions"][name]["partitionFingerprint"] = f"{name}-fingerprint"
    value["prospectiveTestSealed"] = True
    value["manifestDigest"] = experiment.manifest_digest(value)
    return value


def test_models_are_regularized_and_limited_to_ten_prespecified_features():
    assert len(dual.OUTCOME_FEATURES) == 10
    assert len(dual.RELIABILITY_FEATURES) == 10
    rows = partitions()
    trained = dual.train_fixed_partitions(rows, manifest(rows))
    assert trained["ok"] is True
    assert trained["split"]["counts"] == {
        "train": 300,
        "validation": 105,
        "prospectiveTest": 105,
    }
    assert trained["outcomeModel"]["training"]["l2"] > 0
    assert trained["reliabilityModel"]["training"]["l2"] > 0
    assert trained["outcomeModel"]["features"] == dual.OUTCOME_FEATURES
    assert trained["reliabilityModel"]["features"] == dual.RELIABILITY_FEATURES
    assert trained["prospectiveTest"]["sealedBeforeEvaluation"] is True
    assert trained["prospectiveTest"]["outcome"]["baseline"]["logLoss"] is not None
    assert (
        trained["prospectiveTest"]["outcome"]["pairedAccuracyRegression"]["ok"]
        is True
    )


def test_no_v2_entrypoint_can_recreate_the_legacy_moving_140_row_split():
    with pytest.raises(RuntimeError, match="dynamic V2 training is disabled"):
        dual.train([])
    with pytest.raises(RuntimeError, match="dynamic V2 partitioning is disabled"):
        walk_forward.split_chronological([])


def _selection_manifest():
    rows = partitions()
    value = manifest(rows)
    value["prospectiveCutoverAtUtc"] = "2026-08-23T00:00:00+00:00"
    value["prospectiveAfterSlateDate"] = "2026-08-20"
    value["frozenChallenger"] = {
        "artifactDigest": "a" * 64,
        "artifact": {
            "bucket": "artifacts",
            "key": "challenger.json",
            "versionId": "v1",
            "sha256": "a" * 64,
        },
        "selectedThreshold": 0.6,
        "boundAtUtc": "2026-08-23T00:00:00+00:00",
    }
    value["manifestDigest"] = experiment.manifest_digest(value)
    return value


def test_selection_evaluation_revalidates_immutable_pre_outcome_contract():
    manifest_value = _selection_manifest()
    settled = row("2026-09-02", 1)
    unlabeled = copy.deepcopy(settled)
    unlabeled.pop("winner")
    unlabeled.pop("correct")
    entry = experiment.selection_ledger_entry(
        manifest_value,
        unlabeled,
        reliability_probability=0.7,
        deployment_identity=DEPLOYMENT_IDENTITY,
        captured_at_utc="2026-09-02T17:00:00+00:00",
    )

    result = dual.evaluate_selection_ledger(
        [settled],
        [entry],
        challenger_artifact_digest="a" * 64,
        experiment_manifest=manifest_value,
    )
    assert result["ok"] is True
    assert result["settledSelectedRecommendationCount"] == 1

    mutations = [
        ("wrong-version", lambda value: value.update({"version": "legacy"})),
        ("wrong-experiment", lambda value: value.update({"experimentId": "other"})),
        (
            "wrong-challenger",
            lambda value: value.update({"challengerArtifactDigest": "b" * 64}),
        ),
        (
            "late-capture",
            lambda value: value.update({"capturedAtUtc": value["commenceTime"]}),
        ),
        (
            "invalid-probability",
            lambda value: value.update({"reliabilityProbability": float("nan")}),
        ),
        ("wrong-threshold", lambda value: value.update({"selectedThreshold": 0.9})),
        ("wrong-selection", lambda value: value.update({"selected": False})),
        (
            "outcome-known",
            lambda value: value.update({"outcomeKnownAtCapture": True}),
        ),
        ("not-write-once", lambda value: value.update({"writeOnce": False})),
        (
            "capture-fingerprint-tamper",
            lambda value: value.update({"capturedAtUtc": "2026-09-02T17:01:00+00:00"}),
        ),
    ]
    for label, mutate in mutations:
        tampered = copy.deepcopy(entry)
        mutate(tampered)
        rejected = dual.evaluate_selection_ledger(
            [settled],
            [tampered],
            challenger_artifact_digest="a" * 64,
            experiment_manifest=manifest_value,
        )
        assert rejected["ok"] is False, label
        assert rejected["settledSelectedRecommendationCount"] == 0, label
        assert rejected["conflicts"][0]["reason"] == "invalid_selection_contract"
