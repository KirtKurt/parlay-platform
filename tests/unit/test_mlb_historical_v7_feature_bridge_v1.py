from __future__ import annotations

import copy
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "hello_world" / "mlb_historical_v7_feature_bridge_v1.py"


def _load_bridge():
    repairs = types.ModuleType("mlb_historical_v7_priority_repairs_v1")
    repairs.dataset_fingerprint = lambda records: "canonical-fingerprint"

    bbs = types.ModuleType("mlb_v8_historical_bbs_overlay_v1")
    bbs.load_and_apply = lambda records: (
        [copy.deepcopy(dict(row)) for row in records],
        {"status": "APPLIED", "appliedGameCount": 1},
    )

    context = types.ModuleType("mlb_v8_historical_context_overlay_v1")
    context.TARGET_FAMILY = "targetGame"
    context.AUTHORITY = "V8_HISTORICAL_BBS_SHADOW_ONLY"
    context.has_family = lambda snapshot, name: bool(
        isinstance(snapshot, dict)
        and (snapshot.get("featureFamilies") or {}).get(name, {}).get("trainingEligible")
        is True
    )
    context.load_and_apply = lambda records: (
        [copy.deepcopy(dict(row)) for row in records],
        {"status": "APPLIED", "appliedGameCount": 1},
    )

    old = {name: sys.modules.get(name) for name in (
        repairs.__name__, bbs.__name__, context.__name__
    )}
    sys.modules[repairs.__name__] = repairs
    sys.modules[bbs.__name__] = bbs
    sys.modules[context.__name__] = context
    try:
        spec = importlib.util.spec_from_file_location("bridge_under_test", MODULE)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in old.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def _record(snapshot_fingerprint="snapshot-1"):
    snapshot = {
        "trainingEligible": True,
        "pointInTimeVerified": True,
        "fingerprint": snapshot_fingerprint,
        "parkRunFactor": 1.03,
        "weatherRunFactor": 0.98,
        "home": {
            "starterQuality": 3.1,
            "bullpenQuality": 2.2,
            "lineupQuality": 108.0,
            "bbsWinRate10": 0.6,
        },
        "away": {
            "starterQuality": 2.4,
            "bullpenQuality": 1.8,
            "lineupQuality": 101.0,
            "bbsWinRate10": 0.4,
        },
        "featureFamilies": {
            "targetGame": {"available": True, "trainingEligible": True}
        },
    }
    return {
        "slateDateEt": "2026-07-01",
        "officialGamePk": "123",
        "homeWon": 1,
        "homeSignal": {"delta": 0.1, "fundamentals": {"existing": 7}},
        "awaySignal": {"delta": -0.1},
        "frozenFundamentalsSnapshot": snapshot,
        "historicalTargetGameContext": {
            "trainingEligible": True,
            "compositeFingerprint": snapshot_fingerprint,
        },
        "historicalBbsFundamentals": {
            "trainingEligible": True,
            "snapshotFingerprint": "prior-1",
        },
    }


def test_target_context_is_wired_into_both_signal_feature_surfaces():
    bridge = _load_bridge()
    row = bridge.attach_target_context_to_signals(_record())
    assert row["homeSignal"]["delta"] == 0.1
    assert row["homeSignal"]["fundamentals"]["existing"] == 7
    assert row["homeSignal"]["fundamentals"]["starterQuality"] == 3.1
    assert row["homeSignal"]["fundamentalsSnapshotV2"]["bullpenQuality"] == 2.2
    assert row["awaySignal"]["fundamentals"]["lineupQuality"] == 101.0
    assert row["homeSignal"]["historicalFundamentalsPointInTimeVerified"] is True
    assert row["homeSignal"]["historicalFundamentalsProductionAuthorityChanged"] is False


def test_ineligible_or_unverified_context_is_not_exposed_to_learning():
    bridge = _load_bridge()
    row = _record()
    row["historicalTargetGameContext"]["trainingEligible"] = False
    output = bridge.attach_target_context_to_signals(row)
    assert "historicalFundamentalsAvailable" not in output["homeSignal"]


def test_feature_state_counts_consumed_rows_and_changes_on_correction():
    bridge = _load_bridge()
    first = bridge.attach_target_context_to_signals(_record("snapshot-1"))
    second = bridge.attach_target_context_to_signals(_record("snapshot-2"))
    state_one = bridge.feature_corpus_state([first])
    state_two = bridge.feature_corpus_state([second])
    assert state_one["materializedFeatureRowCount"] == 1
    assert state_one["starterFeatureRowCount"] == 1
    assert state_one["bullpenFeatureRowCount"] == 1
    assert state_one["lineupFeatureRowCount"] == 1
    assert state_one["priorGameSupplementalRowCount"] == 1
    assert state_one["fingerprint"] != state_two["fingerprint"]
    assert bridge.dataset_fingerprint([first], state_one) != bridge.dataset_fingerprint(
        [second], state_two
    )


def test_feature_state_is_order_independent():
    bridge = _load_bridge()
    one = bridge.attach_target_context_to_signals(_record("one"))
    two = bridge.attach_target_context_to_signals(_record("two"))
    two["officialGamePk"] = "124"
    assert bridge.feature_corpus_state([one, two]) == bridge.feature_corpus_state(
        [two, one]
    )


def test_bridged_signals_are_non_degenerate_in_actual_v7_feature_repair():
    bridge = _load_bridge()
    spec = importlib.util.spec_from_file_location(
        "repairs_under_test",
        ROOT / "hello_world" / "mlb_historical_v7_priority_repairs_v1.py",
    )
    repairs = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(repairs)

    class Learner:
        FEATURES = ("marketLogit", "v8FirstFiveLogit")
        FEATURE_VERSION = "base"

        @staticmethod
        def pair_features(home, away, policy):
            return {"marketLogit": 0.2, "v8FirstFiveLogit": 0.0}

        @staticmethod
        def _fundamental(signal, names):
            source = signal.get("fundamentals") or {}
            for name in names:
                if source.get(name) is not None:
                    return float(source[name])
            return None

        @staticmethod
        def _v8(signal, name):
            return signal.get(name)

        @staticmethod
        def _temporal(signal, horizon, name):
            return 1.0

    learner = Learner()
    repairs.install_feature_repairs(learner)
    row = bridge.attach_target_context_to_signals(_record())
    features = learner.pair_features(row["homeSignal"], row["awaySignal"], {})
    assert features["starterAvailable"] == 1.0
    assert features["bullpenAvailable"] == 1.0
    assert features["lineupAvailable"] == 1.0
    assert abs(features["starterDiff"] - 0.7) < 1e-12
    assert abs(features["bullpenDiff"] - 0.4) < 1e-12
    assert features["lineupDiff"] == 7.0


def test_population_report_no_longer_marks_starter_bullpen_lineup_as_zero_only():
    bridge = _load_bridge()
    spec = importlib.util.spec_from_file_location(
        "repairs_population_under_test",
        ROOT / "hello_world" / "mlb_historical_v7_priority_repairs_v1.py",
    )
    repairs = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(repairs)

    class Learner:
        FEATURES = ("marketLogit",)
        FEATURE_VERSION = "base"

        @staticmethod
        def pair_features(home, away, policy):
            return {"marketLogit": 0.1}

        @staticmethod
        def _fundamental(signal, names):
            source = signal.get("fundamentals") or {}
            return next((float(source[name]) for name in names if source.get(name) is not None), None)

        @staticmethod
        def _v8(signal, name):
            return signal.get(name)

        @staticmethod
        def _temporal(signal, horizon, name):
            return 1.0

    learner = Learner()
    repairs.install_feature_repairs(learner)
    one = bridge.attach_target_context_to_signals(_record("one"))
    one["homeWon"] = 1
    two_raw = _record("two")
    two_raw["officialGamePk"] = "124"
    two_raw["homeWon"] = 0
    two_raw["frozenFundamentalsSnapshot"]["home"]["starterQuality"] = 4.5
    two_raw["frozenFundamentalsSnapshot"]["home"]["bullpenQuality"] = 3.0
    two_raw["frozenFundamentalsSnapshot"]["home"]["lineupQuality"] = 112.0
    two = bridge.attach_target_context_to_signals(two_raw)
    report = repairs.feature_population_report([one, two], learner, {})
    for name in ("starterAvailable", "bullpenAvailable", "lineupAvailable"):
        assert report["features"][name]["nonzeroPct"] == 1.0
    for name in ("starterDiff", "bullpenDiff", "lineupDiff"):
        assert report["features"][name]["nonzeroCount"] == 2
        assert report["features"][name]["degenerate"] is False
