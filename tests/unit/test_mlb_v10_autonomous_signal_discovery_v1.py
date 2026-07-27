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


def _corpus(days=150, games_per_day=4):
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
    assert all("winner" not in row["definition"] for row in report["signals"])


def test_duplicate_games_are_removed_and_conflicts_quarantined():
    first = _record(1, 1)
    same = dict(first)
    conflict = dict(first, fingerprint="different")
    report = v10.discover([first, same, conflict])
    assert report["inputIntegrity"]["duplicateRecordCount"] == 2
    assert report["inputIntegrity"]["exclusionCounts"]["conflicting_duplicate"] == 1
    assert report["settledGameCount"] == 0


def test_invalid_labels_and_noncanonical_rows_are_excluded():
    rows = [
        _record(1, 1),
        _record(2, None),
        _record(3, 0, trainingEligible=False),
        _record(4, 1, canonicalLockValid=False),
        _record(5, 1, duplicateContaminated=True),
    ]
    report = v10.discover(rows)
    counts = report["inputIntegrity"]["exclusionCounts"]
    assert report["settledGameCount"] == 1
    assert counts["invalid_label"] == 1
    assert counts["training_ineligible"] == 1
    assert counts["invalid_canonical_lock"] == 1
    assert counts["duplicate_contaminated"] == 1


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


def test_random_noise_does_not_gain_production_authority():
    rows = []
    for index in range(500):
        home_won = index % 2
        rows.append(_record(index, home_won, 0.55 if index % 3 else 0.45, 0.45 if index % 3 else 0.55))
    report = v10.discover(rows)
    assert report["productionAuthority"] is False
    assert all(row["productionEligible"] is False for row in report["signals"])


def test_report_includes_multiple_testing_uncertainty_and_negative_control():
    report = v10.discover(_corpus())
    assert report["multipleTestingCorrection"] == "Benjamini-Hochberg"
    assert report["uncertaintyInterval"] == "Wilson 95%"
    assert report["negativeControl"]["rounds"] == v10.PERMUTATION_ROUNDS
    for row in report["signals"]:
        assert "qValue" in row
        assert "developmentWilson95" in row
        assert "walkForward" in row
        assert "untouchedHoldout" in row


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
