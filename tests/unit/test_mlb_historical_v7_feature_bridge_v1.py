import copy

import mlb_historical_v7_feature_bridge_v1 as subject


class FakeLearner:
    @staticmethod
    def _v8_trainable(game, latest, side):
        expansion = latest.get("oddsMarketExpansionFeatures") or {}
        probability = expansion.get(f"{side}Probability")
        return {
            "version": "test-v8",
            "available": probability is not None,
            "observationCount": 1 if probability is not None else 0,
            "h2hMedianImpliedProbability": probability,
        }


def _record(snapshot_fingerprint="target-fp", manifest_digest="manifest-a"):
    return {
        "slateDateEt": "2026-07-29",
        "officialGamePk": "777",
        "predictionLockAtUtc": "2026-07-29T18:15:00+00:00",
        "homeWon": 1,
        "fingerprint": "canonical-fp",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "homeSignal": {"marketConsensusProbability": 0.57},
        "awaySignal": {"marketConsensusProbability": 0.43},
        "oddsMarketExpansionFeatures": {
            "homeProbability": 0.57,
            "awayProbability": 0.43,
        },
        "historicalTargetGameContext": {
            "trainingEligible": True,
            "manifestDigest": manifest_digest,
            "snapshotFingerprint": snapshot_fingerprint,
            "compositeFingerprint": f"composite-{snapshot_fingerprint}",
        },
        "frozenFundamentalsSnapshot": {
            "snapshotRole": subject.COMPOSITE_ROLE,
            "fingerprint": snapshot_fingerprint,
            "trainingEligible": True,
            "pointInTimeVerified": True,
            "postgameFieldsExcluded": True,
            "targetGameOutcomeUsed": False,
            "productionAuthorityChanged": False,
            "home": {
                "starterQuality": 2.4,
                "bullpenQuality": 1.2,
                "lineupQuality": 104.0,
            },
            "away": {
                "starterQuality": 1.7,
                "bullpenQuality": 0.8,
                "lineupQuality": 98.0,
            },
            "parkRunFactor": 1.03,
            "weatherRunFactor": 0.02,
        },
    }


def test_materializes_target_context_into_copied_team_signals():
    raw = _record()
    before = copy.deepcopy(raw)
    records, proof = subject.materialize_training_signals([raw], FakeLearner)

    assert raw == before
    assert records[0] is not raw
    home = records[0]["homeSignal"]
    away = records[0]["awaySignal"]
    assert home["fundamentals"]["starterQuality"] == 2.4
    assert away["fundamentals"]["bullpenQuality"] == 0.8
    assert home["fundamentals"]["parkRunFactor"] == 1.03
    assert home["historicalTargetContextApplied"] is True
    assert home["v8TrainableFeatures"]["h2hMedianImpliedProbability"] == 0.57
    assert away["v8TrainableFeatures"]["h2hMedianImpliedProbability"] == 0.43
    assert proof["targetSnapshotRecordCount"] == 1
    assert proof["targetSignalPairCount"] == 1
    assert proof["starterPairAvailableCount"] == 1
    assert proof["bullpenPairAvailableCount"] == 1
    assert proof["lineupPairAvailableCount"] == 1
    assert proof["v8TrainablePairCount"] == 1
    assert proof["providerCallsMade"] == 0
    assert proof["productionAuthorityChanged"] is False


def test_feature_aware_fingerprint_is_order_independent_and_overlay_sensitive():
    first = _record()
    second = _record(snapshot_fingerprint="other-fp", manifest_digest="manifest-b")
    second["officialGamePk"] = "778"
    second["fingerprint"] = "canonical-fp-2"

    assert subject.dataset_fingerprint([first, second]) == subject.dataset_fingerprint(
        [second, first]
    )
    changed = copy.deepcopy(first)
    changed["historicalTargetGameContext"]["manifestDigest"] = "manifest-new"
    assert subject.dataset_fingerprint([first]) != subject.dataset_fingerprint([changed])


def test_prior_only_snapshot_is_not_misrepresented_as_target_game_context():
    record = _record()
    record.pop("historicalTargetGameContext")
    record["frozenFundamentalsSnapshot"]["snapshotRole"] = (
        "BBD_STRICTLY_PRIOR_COMPLETED_GAME_FEATURES"
    )
    record["frozenFundamentalsSnapshot"].pop("targetGameOutcomeUsed")
    records, proof = subject.materialize_training_signals([record], FakeLearner)
    assert "fundamentals" not in records[0]["homeSignal"]
    assert proof["targetSnapshotRecordCount"] == 0


def test_install_replaces_legacy_dataset_fingerprint_idempotently():
    class Repairs:
        @staticmethod
        def dataset_fingerprint(records):
            return "legacy"

    subject.install(Repairs)
    first = Repairs.dataset_fingerprint
    subject.install(Repairs)
    assert Repairs.dataset_fingerprint is first
    assert Repairs.dataset_fingerprint([_record()]) != "legacy"
    assert Repairs.V7_FEATURE_BRIDGE_VERSION == subject.VERSION
