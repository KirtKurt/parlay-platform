from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))

import mlb_v7_learning_integration_v1 as integration


class FakeOptimizer:
    VERSION = "fake-v7"

    def __init__(self):
        self.seen = None

    def _signal(self, game, observations, side, expected_slots):
        return {"bookDivergence": 0.05, "coverageRatio": 1.0, "derivedFeatures": {"existing": 1.0}}

    def search(self, records, config=None, **kwargs):
        self.seen = list(records)
        return {"ok": True, "status": "CANDIDATE_REJECTED"}


def _rows():
    return [
        {
            "slateDateEt": "2026-07-01",
            "officialGamePk": "1",
            "homeWon": 1,
            "postLockDataExcluded": True,
            "gameSpecificLockClipping": True,
        },
        {
            "slateDateEt": "2026-07-01",
            "officialGamePk": "2",
            "homeWon": None,
            "postLockDataExcluded": True,
            "gameSpecificLockClipping": True,
        },
    ]


def test_install_filters_bad_labels_and_records_diagnostics():
    optimizer = FakeOptimizer()
    integration.install(optimizer)
    result = optimizer.search(_rows())
    assert result["ok"] is True
    assert len(optimizer.seen) == 1
    assert result["v7IntegrityValidation"]["acceptedCount"] == 1
    assert result["v7IntegrityValidation"]["rejected"]["invalid_label"] == 1


def test_signal_uses_canonical_prelock_sequence_and_keeps_existing_features():
    optimizer = FakeOptimizer()
    integration.install(optimizer)
    game = {"commenceTime": "2026-07-01T20:00:00Z"}
    observations = [
        {"observedAt": "2026-07-01T18:00:00Z", "deVigProbability": 0.50},
        {"observedAt": "2026-07-01T18:01:00Z", "deVigProbability": 0.51},
        {"observedAt": "2026-07-01T18:16:00Z", "deVigProbability": 0.49},
        {"observedAt": "2026-07-01T19:20:00Z", "deVigProbability": 0.80},
    ]
    signal = optimizer._signal(game, observations, "home", 8)
    assert signal["derivedFeatures"]["existing"] == 1.0
    assert signal["v7IntegrityProof"]["uniqueSlotCount"] == 2
    assert signal["v7IntegrityProof"]["rejected"]["duplicate_slot"] == 1
    assert signal["v7IntegrityProof"]["rejected"]["post_lock"] == 1
    assert signal["derivedFeatures"]["reversalCount"] == 0.0


def test_rejected_candidate_is_never_v7_authority():
    state = {"latestCandidate": {"policy": {"x": 1}, "promotionPassed": False}}
    authority = integration.resolve_v7_authority(state)
    assert authority["ok"] is False
    assert authority["authority"] == "NO_ACTIVE_CHAMPION"
    assert authority["candidatePresent"] is True


def test_active_champion_is_the_only_v7_authority():
    policy = {"signalWeight": 0.25}
    authority = integration.resolve_v7_authority({"activeChampion": {"policy": policy, "policyDigest": "abc"}})
    assert authority == {"ok": True, "authority": "ACTIVE_CHAMPION", "policy": policy, "policyDigest": "abc"}
