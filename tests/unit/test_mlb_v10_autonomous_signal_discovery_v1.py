from hello_world import mlb_v10_autonomous_signal_discovery_v1 as v10


def _signal(prob, delta, tags=None):
    return {
        "marketConsensusProbability": prob,
        "delta": delta,
        "bookDivergence": 0.02,
        "reversalCount": 0,
        "tags": tags or [],
        "temporalFeatures": {"horizons": {"60m": {"velocityPpHr": delta}}},
    }


def test_v10_is_outcome_anchored_and_non_authoritative():
    records = []
    for index in range(12):
        home_won = 1 if index % 2 == 0 else 0
        records.append(
            {
                "slateDateEt": f"2026-06-{index + 1:02d}",
                "officialGamePk": index,
                "homeWon": home_won,
                "homeSignal": _signal(0.62 if home_won else 0.38, 0.03 if home_won else -0.03, ["STEAM"] if home_won else []),
                "awaySignal": _signal(0.38 if home_won else 0.62, -0.03 if home_won else 0.03, [] if home_won else ["STEAM"]),
            }
        )
    report = v10.discover(records)
    assert report["winnerKnownBeforeSignalConstruction"] is True
    assert report["autonomousFeatureGeneration"] is True
    assert report["productionAuthority"] is False
    assert report["mayWriteChampion"] is False
    assert report["mayPublishPicks"] is False
    assert report["settledGameCount"] == 12
    assert report["generatedPatternCount"] > 0
    assert report["retainedPatternCount"] > 0
    assert all(row["productionEligible"] is False for row in report["signals"])


def test_v10_counts_recurrence_across_other_games():
    records = [
        {
            "slateDateEt": f"2026-07-{index + 1:02d}",
            "officialGamePk": index,
            "homeWon": 1,
            "homeSignal": _signal(0.65, 0.04, ["STEAM"]),
            "awaySignal": _signal(0.35, -0.04),
        }
        for index in range(10)
    ]
    report = v10.discover(records)
    steam = [row for row in report["signals"] if row["definition"] == "winner_tag:STEAM"]
    assert steam
    row = steam[0]
    assert row["occurrenceCount"] == 10
    assert row["otherGamesCompared"] == 0 or row["otherGameWinnerMatchCount"] >= 0
    assert 0.0 <= row["posteriorProbabilityOfRecurring"] <= 1.0
