"""Cached permutation controls and the V10 development-frozen portfolio upgrade.

Pregame rule applicability is invariant to shuffled outcomes, so the individual-rule
control caches rule applications before permuting labels. ``install`` also upgrades the
V10 research engine to the V3 portfolio gate: discovery remains development-only,
walk-forward tests one frozen aggregate policy, the final holdout is audit-only, and an
unpromoted policy may continue in prospective shadow without gaining production rights.

Prospective shadow evidence is preserved in an append-only, deduplicated history so an
advancing freeze boundary cannot erase earlier observed outcomes.
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
    """Install cached controls, the V3 portfolio, and durable shadow history."""
    import mlb_v10_development_frozen_portfolio_v3 as portfolio_v3
    import mlb_v10_prospective_shadow_history_v1 as prospective_history

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

    if not hasattr(subject, "_v2_discover_before_portfolio_upgrade"):
        subject._v2_discover_before_portfolio_upgrade = subject.discover
        subject._v2_evaluate_frozen_registry_before_portfolio_upgrade = subject.evaluate_frozen_registry

    original_discover = subject._v2_discover_before_portfolio_upgrade

    def discover(
        records: Sequence[Mapping[str, Any]],
        *,
        previous_report: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        # The V2 report is retained for canonical checks, rule generation diagnostics,
        # exact tests, and individual-rule null controls. Its holdout-based registry is
        # discarded and rebuilt by V3 from the development partition only.
        base_report = original_discover(records, previous_report=None)
        report = portfolio_v3.upgrade_report(
            subject,
            records,
            base_report,
            previous_report=previous_report,
        )
        report["prospectiveShadow"] = prospective_history.enrich_snapshot(
            report.get("prospectiveShadow") or {},
            previous_report,
        )
        report["prospectiveShadowHistoryPreserved"] = True
        report["prospectiveShadowHistoryVersion"] = prospective_history.VERSION
        return report

    def evaluate_frozen_registry(
        records: Sequence[Mapping[str, Any]],
        previous: Mapping[str, Any],
    ) -> dict[str, Any]:
        current = portfolio_v3.evaluate_frozen_registry(subject, records, previous)
        return prospective_history.enrich_snapshot(current, previous)

    subject.discover = discover
    subject.evaluate_frozen_registry = evaluate_frozen_registry
    subject.VERSION = portfolio_v3.VERSION
    subject.PORTFOLIO_CONTROL_IMPLEMENTATION = portfolio_v3.CONTROL_VERSION
    subject.DEVELOPMENT_FROZEN_PORTFOLIO_INSTALLED = True
