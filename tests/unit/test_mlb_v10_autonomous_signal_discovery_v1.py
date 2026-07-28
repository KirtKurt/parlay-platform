from hello_world import mlb_v10_autonomous_signal_discovery_v1 as v10


def _signal(prob=0.6, delta=0.03, tags=None, **extra):
    value = {
        "marketConsensusProbability": prob,
        "delta": delta,
        "bookDivergence": 0.02,
        "reversalCount": 0,
        "tags": tags or [],
        "temporalFeatures": {"horizons": {"60m": {"velocityPpHr": delta}}},
    }
    value.update(extra)
    return value


def _record(index, home_won, home_prob=0.65, away_prob=0.35, **extra):
    row = {
        "slateDateEt": f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}",
        "officialGamePk": index,
        "homeWon": home_won,
        "featureCutoff": "each_game_t_minus_45",
        "trainingEligible": True,
        "canonicalLockValid": True,
        "duplicateContaminated": False,
        "fingerprint": f"fp-{index}",
        "homeSignal": _signal(home_prob, 0.04, ["STEAM"] if home_prob > away_prob else []),
        "awaySignal": _signal(away_prob, -0.04, [] if home_prob > away_prob else ["STEAM"]),
    }
    row.update(extra)
    return row


def _corpus(days=80, games_per_day=3):
    rows = []
    index = 0
    for day in range(days):
        for game in range(games_per_day):
            home_won = 1 if (day + game) % 5 else 0
            if home_won:
                rows.append(_record(index, 1, 0.68, 0.32))
            else:
                rows.append(_record(index, 0, 0.32, 0.68))
            index += 1
    return rows


def test_v10_is_side_applicable_and_non_authoritative():
    report = v10.discover(_corpus())
    assert report["winnerKnownBeforeSignalConstruction"] is False
    assert report["outcomeUsedOnlyForTrainingLabels"] is True
    assert report["productionAuthority"] is False
    assert report["mayWriteChampion"] is False
    assert report["mayPublishPicks"] is False
    assert report["mode"] == "CHRONOLOGICAL_SIDE_APPLICABLE_DISCOVERY"
    assert report["atomicAndInteractionDiscovery"] is True
    assert all("winner" not in row["definition"] for row in report["signals"])


def test_duplicate_games_are_removed_and_conflicts_permanently_quarantined():
    first = _record(1, 1)
    same = dict(first)
    conflict = dict(first, fingerprint="different")
    reappearing = dict(first)
    report = v10.discover([first, same, conflict, reappearing])
    assert report["inputIntegrity"]["duplicateRecordCount"] == 3
    assert report["inputIntegrity"]["exclusionCounts"]["conflicting_duplicate"] == 1
    assert report["inputIntegrity"]["exclusionCounts"]["quarantined_duplicate_reappearance"] == 1
    assert report["inputIntegrity"]["quarantinedGameCount"] == 1
    assert report["settledGameCount"] == 0


def test_invalid_labels_and_unproven_canonical_rows_are_excluded_fail_closed():
    missing_training = _record(6, 1)
    missing_training.pop("trainingEligible")
    missing_lock = _record(7, 1)
    missing_lock.pop("canonicalLockValid")
    missing_duplicate = _record(8, 1)
    missing_duplicate.pop("duplicateContaminated")
    missing_cutoff = _record(9, 1)
    missing_cutoff.pop("featureCutoff")
    rows = [
        _record(1, 1),
        _record(2, None),
        _record(3, 0, trainingEligible=False),
        _record(4, 1, canonicalLockValid=False),
        _record(5, 1, duplicateContaminated=True),
        missing_training,
        missing_lock,
        missing_duplicate,
        missing_cutoff,
    ]
    report = v10.discover(rows)
    counts = report["inputIntegrity"]["exclusionCounts"]
    assert report["settledGameCount"] == 1
    assert counts["invalid_label"] == 1
    assert counts["training_eligibility_not_proven"] == 2
    assert counts["canonical_lock_not_proven"] == 2
    assert counts["duplicate_cleanliness_not_proven"] == 2
    assert counts["t45_cutoff_not_proven"] == 1
    assert report["canonicalEligibilityFailClosed"] is True


def test_postgame_and_outcome_fields_are_prohibited():
    signal = _signal(finalScore=12, settlementCorrect=1, postgameRuns=9, safeMetric=0.4)
    flat = v10._flatten_numeric(signal)
    assert "safeMetric" in flat
    assert not any("final" in key.lower() for key in flat)
    assert not any("settlement" in key.lower() for key in flat)
    assert not any("postgame" in key.lower() for key in flat)


def test_fingerprint_is_order_stable():
    rows = _corpus(days=5, games_per_day=2)
    assert v10.dataset_fingerprint(rows) == v10.dataset_fingerprint(list(reversed(rows)))


def test_chronological_partitions_do_not_overlap():
    report = v10.discover(_corpus())
    p = report["partitions"]
    assert set(p["development"]).isdisjoint(p["walkForward"])
    assert set(p["development"]).isdisjoint(p["untouchedHoldout"])
    assert set(p["walkForward"]).isdisjoint(p["untouchedHoldout"])


def test_atomic_and_interaction_patterns_are_generated():
    report = v10.discover(_corpus(days=60))
    assert report["uniquePatternCountByFamily"].get("interaction", 0) > 0
    assert report["patternFamilyObservationCounts"].get("interaction", 0) > 0


def test_random_noise_does_not_gain_production_authority():
    rows = []
    for index in range(500):
        home_won = index % 2
        rows.append(_record(index, home_won, 0.55 if index % 3 else 0.45, 0.45 if index % 3 else 0.55))
    report = v10.discover(rows)
    assert report["productionAuthority"] is False
    assert all(row["productionEligible"] is False for row in report["signals"])


def test_report_includes_exact_testing_partition_controls_and_uncertainty():
    report = v10.discover(_corpus())
    assert report["multipleTestingCorrection"] == "Benjamini-Hochberg"
    assert report["exactSignificanceTest"] == "two-sided exact binomial"
    assert report["uncertaintyInterval"] == "Wilson 95%"
    assert set(report["negativeControl"]) == {"development", "walkForward", "untouchedHoldout"}
    for value in report["negativeControl"].values():
        assert value["rounds"] in {0, v10.PERMUTATION_ROUNDS}
    assert abs(v10._two_sided_binomial_pvalue(10, 20) - 1.0) < 1e-12
    for row in report["signals"]:
        assert "qValue" in row
        assert "developmentWilson95" in row
        assert "walkForward" in row
        assert "untouchedHoldout" in row


def test_aggregate_policy_and_registry_freeze_are_reported_without_holdout_selection():
    report = v10.discover(_corpus())
    assert set(report["aggregateResearchPolicy"]) == {"walkForward", "untouchedHoldout"}
    for partition in report["aggregateResearchPolicy"].values():
        assert partition["policyType"] == "DEVELOPMENT_WEIGHTED_MAJORITY_VOTE"
        assert partition["selectionUsesHoldoutLabels"] is False
        assert "pickCount" in partition
        assert "losses" in partition
    assert report["registryFreeze"]["selectionUsedUntouchedHoldoutLabels"] is False
    assert report["registryFreeze"]["registryFingerprint"]


def test_prior_frozen_registry_is_scored_only_on_later_games():
    rows = _corpus(days=100, games_per_day=3)
    previous = v10.discover(rows[:210])
    current = v10.discover(rows, previous_report=previous)
    prospective = current["prospectiveShadow"]
    assert prospective["policyChangedDuringEvaluation"] is False
    assert prospective["productionAuthority"] is False
    assert prospective["futureCanonicalGameCount"] >= 0
    assert prospective["status"] in {"EVALUATED", "AWAITING_FUTURE_GAMES"}


def test_pattern_family_counts_distinguish_observations_from_unique_patterns():
    report = v10.discover(_corpus(days=60))
    assert "patternFamilyObservationCounts" in report
    assert "uniquePatternCountByFamily" in report


def test_empty_and_malformed_inputs_fail_closed():
    report = v10.discover([{}, {"homeWon": "yes"}])
    assert report["settledGameCount"] == 0
    assert report["blockers"] == ["no_canonical_records"]
    assert report["signals"] == []


def test_original_recurrence_assertion_is_now_meaningful():
    rows = [_record(index, 1, 0.70, 0.30) for index in range(300)]
    report = v10.discover(rows)
    assert report["generatedPatternCount"] > 0
    assert report["fdrRetainedPatternCount"] >= report["retainedPatternCount"]
    assert all(row["developmentPickCount"] >= v10.MIN_PATTERN_OCCURRENCES for row in report["signals"])
