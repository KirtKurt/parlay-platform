from __future__ import annotations

import copy

import mlb_no_bbd_context_bridge_v1 as bridge


def _record(fingerprint: str = "context-a"):
    return {
        "officialGamePk": "123",
        "predictionLockAtUtc": "2026-07-01T18:15:00+00:00",
        "slateDateEt": "2026-07-01",
        "homeSignal": {"marketConsensusProbability": 0.55},
        "awaySignal": {"marketConsensusProbability": 0.45},
        "frozenFundamentalsSnapshot": {
            "officialGamePk": "123",
            "predictionLockAtUtc": "2026-07-01T18:15:00+00:00",
            "fingerprint": fingerprint,
            "snapshotRole": "HISTORICAL_COMPOSITE_POINT_IN_TIME_AT_T_MINUS_45",
            "trainingEligible": True,
            "pointInTimeVerified": True,
            "postgameFieldsExcluded": True,
            "selectionUsedOutcomes": False,
            "targetGameOutcomeUsed": False,
            "featureFamilies": {
                "targetGame": {
                    "available": True,
                    "trainingEligible": True,
                    "pointInTimeVerified": True,
                }
            },
            "home": {
                "starterQuality": 1.2,
                "bullpenQuality": 0.4,
                "lineupQuality": 108.0,
            },
            "away": {
                "starterQuality": 0.7,
                "bullpenQuality": 0.2,
                "lineupQuality": 101.0,
            },
        },
    }


def test_v7_v9_bridge_exposes_verified_context_without_provider_calls():
    rows, proof = bridge.augment_v7_v9_records([_record()])
    assert proof["eligibleFeatureGameCount"] == 1
    assert proof["providerCallsMade"] == 0
    assert proof["liveBbdApiRequired"] is False
    assert proof["selectionUsedOutcomes"] is False
    assert rows[0]["homeSignal"]["fundamentalsSnapshotV2"]["starterQuality"] == 1.2
    assert rows[0]["awaySignal"]["fundamentalsSnapshotV2"]["lineupQuality"] == 101.0


def test_v10_side_view_namespaces_context_and_never_adds_outcome():
    record = _record()
    record["homeWon"] = True
    view = bridge.v10_side_feature_view(record, "home")
    assert view["historicalPointInTimeContext"]["bullpenQuality"] == 0.4
    assert "homeWon" not in view
    assert "winner" not in view


def test_context_fingerprint_changes_only_when_context_changes():
    first = _record("a")
    second = _record("b")
    assert bridge.context_fingerprint([first]) != bridge.context_fingerprint([second])
    changed_outcome = copy.deepcopy(first)
    changed_outcome["homeWon"] = True
    assert bridge.context_fingerprint([first]) == bridge.context_fingerprint([changed_outcome])


def test_target_outcome_contract_fails_closed():
    record = _record()
    record["frozenFundamentalsSnapshot"]["targetGameOutcomeUsed"] = True
    rows, proof = bridge.augment_v7_v9_records([record])
    assert proof["eligibleFeatureGameCount"] == 0
    assert proof["rejectionCounts"]["target_game_outcome_used"] == 1
    assert "fundamentalsSnapshotV2" not in rows[0]["homeSignal"]
