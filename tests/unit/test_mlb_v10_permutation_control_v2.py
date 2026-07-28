from __future__ import annotations

import math
import random

from hello_world import mlb_v10_autonomous_signal_discovery_v1 as v10
from hello_world import mlb_v10_permutation_control_v2 as cached


def _record(index: int, home_won: int) -> dict:
    home_prob = 0.68 if index % 3 else 0.43
    away_prob = 1.0 - home_prob
    return {
        "slateDateEt": f"2026-07-{1 + index // 3:02d}",
        "officialGamePk": index,
        "homeWon": home_won,
        "trainingEligible": True,
        "canonicalLockValid": True,
        "duplicateContaminated": False,
        "featureCutoff": "each_game_t_minus_45",
        "featureVectorFingerprint": f"fp-{index}",
        "homeSignal": {
            "marketConsensusProbability": home_prob,
            "delta": 0.04 if home_prob > away_prob else -0.02,
            "bookDivergence": 0.01 + index / 1000,
            "tags": ["STEAM"] if index % 4 == 0 else [],
        },
        "awaySignal": {
            "marketConsensusProbability": away_prob,
            "delta": -0.04 if home_prob > away_prob else 0.02,
            "bookDivergence": 0.02 + index / 1000,
            "tags": ["REVERSAL"] if index % 5 == 0 else [],
        },
    }


def _naive(records, definitions, minimum_picks, rounds, seed):
    rng = random.Random(seed)
    maxima = []
    labels = [v10._label(row) for row in records]
    dates = {v10._date(row) for row in records}
    for _ in range(rounds):
        shuffled = list(labels)
        rng.shuffle(shuffled)
        shadow = [dict(row, homeWon=shuffled[index]) for index, row in enumerate(records)]
        metrics = v10._evaluate(shadow, dates, definitions)
        maxima.append(
            max(
                (value["accuracy"] for value in metrics.values() if value["pickCount"] >= minimum_picks),
                default=0.5,
            )
        )
    maxima.sort()
    index = min(len(maxima) - 1, math.ceil(len(maxima) * 0.95) - 1)
    return maxima[index]


def test_cached_control_matches_original_label_permutation_semantics():
    records = [_record(index, int(index % 2 == 0)) for index in range(36)]
    definitions = {
        rule["definition"]
        for row in records
        for rule in v10._side_applicable_rules(row)
    }
    expected = _naive(records, definitions, minimum_picks=5, rounds=11, seed=812)
    actual = cached.permutation_control(
        v10,
        records,
        definitions,
        minimum_picks=5,
        rounds=11,
        seed=812,
    )
    assert actual["maximumAccuracy95thPercentile"] == expected
    assert actual["rounds"] == 11
    assert actual["seed"] == 812
    assert actual["passed"] is True
    assert actual["pregameRulesCached"] is True
    assert actual["eligibleDefinitionCount"] > 0
    assert actual["cachedRuleApplicationCount"] > 0


def test_install_preserves_expected_call_signature_and_metadata():
    records = [_record(index, index % 2) for index in range(20)]
    definitions = {
        rule["definition"]
        for row in records
        for rule in v10._side_applicable_rules(row)
    }
    original = v10._permutation_control
    try:
        cached.install(v10)
        value = v10._permutation_control(
            records,
            definitions,
            minimum_picks=3,
            rounds=3,
            seed=99,
        )
        assert value["implementation"] == cached.VERSION
        assert value["rounds"] == 3
        assert v10.PERMUTATION_CONTROL_IMPLEMENTATION == cached.VERSION
    finally:
        v10._permutation_control = original
