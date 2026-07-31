import copy

import mlb_historical_v7_prior_signal_bridge_v1 as subject


def _record():
    return {
        "slateDateEt": "2026-07-01",
        "officialGamePk": "123",
        "homeSignal": {"marketConsensusProbability": 0.55},
        "awaySignal": {"marketConsensusProbability": 0.45},
        "historicalBbsFundamentals": {
            "trainingEligible": True,
            "snapshotFingerprint": "prior-fp",
        },
        "frozenFundamentalsSnapshot": {
            "snapshotRole": "BBD_STRICTLY_PRIOR_COMPLETED_GAME_FEATURES_AT_T_MINUS_45",
            "fingerprint": "prior-fp",
            "trainingEligible": True,
            "pointInTimeVerified": True,
            "postgameFieldsExcluded": True,
            "sameDayResultsExcluded": True,
            "targetGameOutcomeUsed": False,
            "productionAuthorityChanged": False,
            "home": {
                "bbsHistoryGames": 30,
                "bbsWinRate10": 0.7,
                "bbsRunDiffPerGame10": 1.2,
            },
            "away": {
                "bbsHistoryGames": 25,
                "bbsWinRate10": 0.4,
                "bbsRunDiffPerGame10": -0.5,
            },
        },
    }


def test_prior_snapshot_is_projected_into_copied_team_signals():
    raw = _record()
    before = copy.deepcopy(raw)
    rows, proof = subject.materialize_prior_signals([raw])

    assert raw == before
    assert rows[0] is not raw
    assert rows[0]["homeSignal"]["fundamentalsSnapshotV2"]["bbsWinRate10"] == 0.7
    assert rows[0]["awaySignal"]["fundamentalsSnapshotV2"]["bbsRunDiffPerGame10"] == -0.5
    assert rows[0]["homeSignal"]["historicalBbsPriorContextApplied"] is True
    assert proof["priorSnapshotRecordCount"] == 1
    assert proof["priorSignalPairCount"] == 1
    assert proof["priorHistoryFiveGamePairCount"] == 1
    assert proof["selectionUsedOutcomes"] is False
    assert proof["productionAuthorityChanged"] is False


def test_invalid_prior_snapshot_is_not_projected():
    record = _record()
    record["frozenFundamentalsSnapshot"]["sameDayResultsExcluded"] = False
    rows, proof = subject.materialize_prior_signals([record])
    assert "fundamentalsSnapshotV2" not in rows[0]["homeSignal"]
    assert proof["priorSnapshotRecordCount"] == 0


def test_install_composes_prior_projection_before_existing_feature_bridge():
    class FeatureBridge:
        @staticmethod
        def materialize_training_signals(records, learner):
            assert records[0]["homeSignal"]["historicalBbsPriorContextApplied"] is True
            return list(records), {"targetSignalPairCount": 0}

    subject.install(FeatureBridge)
    rows, proof = FeatureBridge.materialize_training_signals([_record()], object())
    assert rows[0]["homeSignal"]["fundamentalsSnapshotV2"]["bbsHistoryGames"] == 30
    assert proof["priorSignalPairCount"] == 1
    assert proof["priorSignalMaterialization"]["version"] == subject.VERSION
