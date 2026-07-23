from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_precision_admission_gate_v1 as gate
import mlb_reversal_similarity_v2 as similarity
import mlb_signal_validation_registry_v1 as registry


def _selected() -> dict:
    return {
        "temporalFeatures": {
            "horizons": {
                "full": {
                    "reversalCount": 1,
                    "pathEfficiency": 0.9,
                    "latestReversalRecoveryRatio": 1.2,
                    "latestReversalMinutesBeforeEvent": 300.0,
                    "reversalMarketFlipCount": 1,
                    "latestLeg": {"amplitudePp": 2.4, "direction": 1},
                    "previousLeg": {"amplitudePp": 1.5, "direction": -1},
                    "latestReversalMarketFlip": {"amplitudePp": 2.4},
                    "market": {
                        "eligibleBookCount": 6,
                        "weightedBookDirectionAgreement": 0.82,
                        "latestBookRangePp": 1.0,
                    },
                    "signalQuality": {"signalQualityIndex": 82.0},
                }
            }
        }
    }


def _record(selected: dict) -> dict:
    signature = similarity.signature(selected)
    record = {
        "signalSignature": signature,
        "similarityVersion": similarity.VERSION,
        "prospective": True,
        "outcomeUntouched": True,
        "chronological": True,
        "selectionRuleFrozenBeforeEvaluation": True,
        "noOutcomeBasedTuning": True,
        "independentReproduction": True,
        "productionApproved": True,
        "frozenAtUtc": "2026-01-01T00:00:00+00:00",
        "evaluationStartedAtUtc": "2026-01-02T00:00:00+00:00",
        "auditArtifactSha256": "a" * 64,
        "sampleGames": 210,
        "hits": 172,
        "misses": 38,
        "slateDates": 40,
        "chronologicalFolds": [
            {"games": 70, "hits": 56, "misses": 14},
            {"games": 70, "hits": 57, "misses": 13},
            {"games": 70, "hits": 59, "misses": 11},
        ],
        "recentGames": 30,
        "recentHits": 24,
        "recentMisses": 6,
    }
    record["recordFingerprint"] = registry.record_fingerprint(record)
    return record


class _TrustedRegistry:
    def __init__(self, record: dict):
        self.record = copy.deepcopy(record)

    def get_record(self, signature: str):
        return copy.deepcopy(self.record) if signature == self.record["signalSignature"] else None

    def record_is_trusted(self, record: dict) -> bool:
        return record == self.record and registry.record_fingerprint(record) == record["recordFingerprint"]


def test_default_registry_denies_unvalidated_posthoc_signal() -> None:
    decision = gate.evaluate({}, _selected())
    assert decision["recommendationEligible"] is False
    assert decision["futureAccuracyGuaranteed"] is False
    assert "no_code_reviewed_prospective_validation_record" in decision["reasons"]
    assert registry.status()["approvedSignalCount"] == 0


def test_exact_trusted_prospective_record_passes_all_70pct_gates() -> None:
    selected = _selected()
    record = _record(selected)
    decision = gate.evaluate({}, selected, _TrustedRegistry(record))
    assert decision["recommendationEligible"] is True, decision
    assert decision["wilsonLowerBoundPct"] >= 70.0
    assert decision["evidenceAdmissionGuaranteed"] is True
    assert decision["futureAccuracyGuaranteed"] is False


def test_failing_fold_or_signature_cannot_self_approve() -> None:
    selected = _selected()
    record = _record(selected)
    record["chronologicalFolds"][1] = {"games": 70, "hits": 40, "misses": 30}
    record["hits"] = 155
    record["misses"] = 55
    record["recordFingerprint"] = registry.record_fingerprint(record)
    decision = gate.evaluate({}, selected, _TrustedRegistry(record))
    assert decision["recommendationEligible"] is False
    assert "fold_2_accuracy_below_65pct" in decision["reasons"]

    changed = copy.deepcopy(selected)
    changed["temporalFeatures"]["horizons"]["full"]["latestLeg"]["amplitudePp"] = 4.0
    changed_decision = gate.evaluate({}, changed, _TrustedRegistry(_record(selected)))
    assert changed_decision["recommendationEligible"] is False
    assert "no_code_reviewed_prospective_validation_record" in changed_decision["reasons"]
