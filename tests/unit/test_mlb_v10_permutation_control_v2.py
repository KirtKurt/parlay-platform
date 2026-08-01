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


def _portfolio_corpus(days: int = 90, games_per_day: int = 3) -> list[dict]:
    rows: list[dict] = []
    index = 0
    for day in range(days):
        date = f"2026-{1 + day // 28:02d}-{1 + day % 28:02d}"
        for game in range(games_per_day):
            home_won = (day + game) % 5 != 0
            home_prob = 0.68 if home_won else 0.32
            away_prob = 1.0 - home_prob
            rows.append({
                "slateDateEt": date,
                "officialGamePk": index,
                "homeWon": int(home_won),
                "trainingEligible": True,
                "canonicalLockValid": True,
                "duplicateContaminated": False,
                "featureCutoff": "each_game_t_minus_45",
                "featureVectorFingerprint": f"portfolio-{index}",
                "homeSignal": {
                    "marketConsensusProbability": home_prob,
                    "fairProbability": home_prob,
                    "probStart": home_prob - 0.02 if home_won else home_prob + 0.02,
                    "delta": 0.04 if home_won else -0.04,
                    "tags": ["FAVORITE"] if home_won else ["UNDERDOG"],
                },
                "awaySignal": {
                    "marketConsensusProbability": away_prob,
                    "fairProbability": away_prob,
                    "probStart": away_prob + 0.02 if home_won else away_prob - 0.02,
                    "delta": -0.04 if home_won else 0.04,
                    "tags": ["UNDERDOG"] if home_won else ["FAVORITE"],
                },
            })
            index += 1
    return rows


def _restore(subject, snapshot):
    subject._permutation_control = snapshot["control"]
    subject.discover = snapshot["discover"]
    subject.evaluate_frozen_registry = snapshot["evaluate"]
    subject.VERSION = snapshot["version"]
    for name in (
        "_v2_discover_before_portfolio_upgrade",
        "_v2_evaluate_frozen_registry_before_portfolio_upgrade",
        "PORTFOLIO_CONTROL_IMPLEMENTATION",
        "DEVELOPMENT_FROZEN_PORTFOLIO_INSTALLED",
    ):
        if name in snapshot:
            setattr(subject, name, snapshot[name])
        elif hasattr(subject, name):
            delattr(subject, name)


def _snapshot(subject):
    value = {
        "control": subject._permutation_control,
        "discover": subject.discover,
        "evaluate": subject.evaluate_frozen_registry,
        "version": subject.VERSION,
    }
    for name in (
        "_v2_discover_before_portfolio_upgrade",
        "_v2_evaluate_frozen_registry_before_portfolio_upgrade",
        "PORTFOLIO_CONTROL_IMPLEMENTATION",
        "DEVELOPMENT_FROZEN_PORTFOLIO_INSTALLED",
    ):
        if hasattr(subject, name):
            value[name] = getattr(subject, name)
    return value


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


def test_install_preserves_expected_control_signature_and_metadata():
    records = [_record(index, index % 2) for index in range(20)]
    definitions = {
        rule["definition"]
        for row in records
        for rule in v10._side_applicable_rules(row)
    }
    snapshot = _snapshot(v10)
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
        assert v10.DEVELOPMENT_FROZEN_PORTFOLIO_INSTALLED is True
    finally:
        _restore(v10, snapshot)


def test_install_replaces_empty_individual_registry_with_development_frozen_shadow_portfolio():
    snapshot = _snapshot(v10)
    try:
        cached.install(v10)
        report = v10.discover(_portfolio_corpus())
        assert report["version"].endswith("development-frozen-portfolio")
        assert report["holdoutSelectionDefectFixed"] is True
        assert report["emptyRegistryStallFixed"] is True
        assert report["learningActive"] is True
        assert report["shadowSignalCount"] >= 3
        assert report["registryFreeze"]["portfolio"]
        assert report["registryFreeze"]["selectionUsedWalkForwardLabels"] is False
        assert report["registryFreeze"]["selectionUsedUntouchedHoldoutLabels"] is False
        assert report["portfolioValidation"]["untouchedHoldoutUsedForSelection"] is False
        assert report["aggregateResearchPolicy"]["walkForward"]["pickCount"] > 0
        assert report["productionAuthority"] is False
        assert report["mayWriteChampion"] is False
        assert report["mayPublishPicks"] is False
    finally:
        _restore(v10, snapshot)
