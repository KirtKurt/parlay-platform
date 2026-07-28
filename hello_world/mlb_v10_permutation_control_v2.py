"""Exact, leakage-safe, cached permutation controls for MLB V10.

The original control recomputed every atomic and interaction rule on every label
permutation. Pregame rules are invariant to shuffled outcomes, so this implementation
freezes those rules once, then permutes labels only. The resulting pick counts,
correct counts, maxima, seeds, and percentile calculation are unchanged.
"""
from __future__ import annotations

import math
import random
from collections import Counter
from typing import Any, Mapping, Sequence

VERSION = "MLB-V10-PERMUTATION-CONTROL-v2-cached-pregame-rules"


def permutation_control(
    subject: Any,
    records: Sequence[Mapping[str, Any]],
    definitions: set[str],
    *,
    minimum_picks: int,
    rounds: int | None = None,
    seed: int = 17010,
) -> dict[str, Any]:
    round_count = int(subject.PERMUTATION_ROUNDS if rounds is None else rounds)
    if not records or not definitions:
        return {
            "rounds": 0,
            "maximumAccuracy95thPercentile": 1.0,
            "passed": False,
            "minimumPicks": minimum_picks,
            "implementation": VERSION,
            "pregameRulesCached": True,
        }

    applicable_by_record: list[tuple[tuple[str, bool], ...]] = []
    pick_counts: Counter[str] = Counter()
    labels: list[int] = []
    for row in records:
        label = subject._label(row)
        if label not in (0, 1):
            raise RuntimeError("permutation control received an invalid settled label")
        labels.append(int(label))
        applicable: list[tuple[str, bool]] = []
        for rule in subject._side_applicable_rules(row):
            definition = str(rule.get("definition") or "")
            if definition not in definitions:
                continue
            selected_home = str(rule.get("selectedSide") or "") == "home"
            applicable.append((definition, selected_home))
            pick_counts[definition] += 1
        applicable_by_record.append(tuple(applicable))

    eligible = {
        definition: count
        for definition, count in pick_counts.items()
        if count >= minimum_picks
    }
    if not eligible:
        return {
            "rounds": round_count,
            "maximumAccuracy95thPercentile": 0.5,
            "passed": True,
            "minimumPicks": minimum_picks,
            "seed": seed,
            "implementation": VERSION,
            "pregameRulesCached": True,
            "eligibleDefinitionCount": 0,
            "cachedRuleApplicationCount": sum(pick_counts.values()),
        }

    rng = random.Random(seed)
    maxima: list[float] = []
    for _ in range(round_count):
        shuffled = list(labels)
        rng.shuffle(shuffled)
        correct: Counter[str] = Counter()
        for index, applicable in enumerate(applicable_by_record):
            home_won = bool(shuffled[index])
            for definition, selected_home in applicable:
                if definition in eligible:
                    correct[definition] += int(selected_home == home_won)
        maxima.append(
            max(
                correct[definition] / count
                for definition, count in eligible.items()
            )
        )

    maxima.sort()
    index = min(len(maxima) - 1, math.ceil(len(maxima) * 0.95) - 1)
    return {
        "rounds": round_count,
        "maximumAccuracy95thPercentile": maxima[index],
        "passed": True,
        "minimumPicks": minimum_picks,
        "seed": seed,
        "implementation": VERSION,
        "pregameRulesCached": True,
        "eligibleDefinitionCount": len(eligible),
        "cachedRuleApplicationCount": sum(pick_counts.values()),
    }


def install(subject: Any) -> None:
    def installed(
        records: Sequence[Mapping[str, Any]],
        definitions: set[str],
        *,
        minimum_picks: int,
        rounds: int | None = None,
        seed: int = 17010,
    ) -> dict[str, Any]:
        return permutation_control(
            subject,
            records,
            definitions,
            minimum_picks=minimum_picks,
            rounds=rounds,
            seed=seed,
        )

    subject._permutation_control = installed
    subject.PERMUTATION_CONTROL_IMPLEMENTATION = VERSION
