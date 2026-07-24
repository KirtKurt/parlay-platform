from __future__ import annotations

import copy
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))

import mlb_historical_daily_optimizer_v1 as optimizer
import mlb_historical_policy_v1 as policy


def signal(side, team, fair, price, *, delta=0.0, tags=None):
    return {
        "side": side,
        "team": team,
        "fairProbability": fair,
        "marketConsensusProbability": fair,
        "probLatest": fair,
        "delta": delta,
        "bookDivergence": 0.01,
        "reversalCount": 0,
        "americanOdds": price,
        "bookCount": 6,
        "marketSide": "favorite" if price < -110 else "underdog" if price > 110 else "pickem",
        "pullCountForGame": 20,
        "temporalFeatures": {
            "sourcePointCount": 20,
            "horizons": {
                "15m": {"velocityPpHr": 0.0, "accelerationPpHr2": 0.0, "volatilityPpPerPull": 0.0, "coverageRatio": 1.0, "reversalCount": 0},
                "60m": {"velocityPpHr": 0.0, "accelerationPpHr2": 0.0, "volatilityPpPerPull": 0.0, "coverageRatio": 1.0, "reversalCount": 0},
                "180m": {"velocityPpHr": 0.0, "accelerationPpHr2": 0.0, "volatilityPpPerPull": 0.0, "coverageRatio": 1.0, "reversalCount": 0},
                "full": {"velocityPpHr": 0.0, "accelerationPpHr2": 0.0, "volatilityPpPerPull": 0.0, "coverageRatio": 1.0, "reversalCount": 0},
            },
        },
        "tags": tags or ["BOOK_AGREEMENT"],
    }


def record(day, game_number, home_won=True):
    return {
        "version": optimizer.DATASET_VERSION,
        "slateDateEt": day,
        "officialGamePk": f"{day}-{game_number}",
        "homeTeam": f"Home {game_number}",
        "awayTeam": f"Away {game_number}",
        "winner": f"Home {game_number}" if home_won else f"Away {game_number}",
        "homeWon": 1 if home_won else 0,
        "homeSignal": signal("home", f"Home {game_number}", 0.60, -150),
        "awaySignal": signal("away", f"Away {game_number}", 0.40, 130),
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
    }


def test_snapshot_grid_starts_at_1am_et_and_advances_exactly_15_minutes():
    eastern = ZoneInfo("America/New_York")
    first = datetime(2025, 7, 4, 19, 10, tzinfo=eastern)
    grid = optimizer.build_snapshot_grid("2025-07-04", first)
    assert grid.timestamps_utc[0] == "2025-07-04T05:00:00Z"
    assert grid.lock_at_utc.startswith("2025-07-04T22:25:00")
    parsed = [optimizer._parse_dt(value) for value in grid.timestamps_utc]
    assert all((right - left).total_seconds() == 900 for left, right in zip(parsed, parsed[1:]))
    assert parsed[-1] <= optimizer._parse_dt(grid.lock_at_utc)
    assert (optimizer._parse_dt(grid.lock_at_utc) - parsed[-1]).total_seconds() < 900


def test_snapshot_grid_honors_eastern_daylight_saving_time():
    winter = optimizer.build_snapshot_grid(
        "2025-01-15", datetime(2025, 1, 15, 19, 0, tzinfo=ZoneInfo("America/New_York"))
    )
    summer = optimizer.build_snapshot_grid(
        "2025-07-15", datetime(2025, 7, 15, 19, 0, tzinfo=ZoneInfo("America/New_York"))
    )
    assert winter.timestamps_utc[0].endswith("06:00:00Z")
    assert summer.timestamps_utc[0].endswith("05:00:00Z")


def test_snapshot_grid_runs_to_last_game_lock_and_not_only_first_game_lock():
    eastern = ZoneInfo("America/New_York")
    starts = [
        datetime(2025, 7, 4, 13, 10, tzinfo=eastern),
        datetime(2025, 7, 4, 22, 10, tzinfo=eastern),
    ]
    grid = optimizer.build_snapshot_grid("2025-07-04", starts)
    assert grid.first_game_lock_at_utc.startswith("2025-07-04T16:25:00")
    assert grid.lock_at_utc.startswith("2025-07-05T01:25:00")
    assert optimizer._parse_dt(grid.timestamps_utc[-1]) <= optimizer._parse_dt(grid.lock_at_utc)
    assert optimizer._parse_dt(grid.timestamps_utc[-1]) > optimizer._parse_dt(grid.first_game_lock_at_utc)


def test_snapshot_grid_rejects_any_start_other_than_1am_or_non_15m_interval():
    first = datetime(2025, 7, 4, 19, 0, tzinfo=ZoneInfo("America/New_York"))
    for kwargs in ({"start_at_et": "00:45"}, {"interval_minutes": 30}):
        try:
            optimizer.build_snapshot_grid("2025-07-04", first, **kwargs)
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_daily_accuracy_is_full_slate_not_individual_probability():
    rows = [record("2025-07-01", i, home_won=i < 8) for i in range(10)]
    # For the last two games make the away team the market favorite so the
    # baseline gets exactly eight of ten correct.
    for item in rows[8:]:
        item["homeSignal"] = signal("home", item["homeTeam"], 0.40, 130)
        item["awaySignal"] = signal("away", item["awayTeam"], 0.60, -150)
    metrics = optimizer.evaluate_policy(rows, policy.BASELINE_POLICY)
    assert metrics["gameCount"] == 10
    assert metrics["dayCount"] == 1
    assert metrics["daily"][0]["predictionCount"] == 10
    assert metrics["daily"][0]["accuracy"] == 1.0  # all outcomes follow market favorite
    assert metrics["daily"][0]["dayPassed"] is True


def test_exactly_eight_of_ten_is_an_80_percent_passing_day():
    rows = [record("2025-07-01", i, home_won=i < 8) for i in range(10)]
    metrics = optimizer.evaluate_policy(rows, policy.BASELINE_POLICY)
    assert metrics["daily"][0]["accuracy"] == 0.8
    assert metrics["daily"][0]["dayPassed"] is True


def test_seven_of_ten_fails_the_daily_requirement():
    rows = [record("2025-07-01", i, home_won=i < 7) for i in range(10)]
    metrics = optimizer.evaluate_policy(rows, policy.BASELINE_POLICY)
    assert metrics["daily"][0]["accuracy"] == 0.7
    assert metrics["daily"][0]["dayPassed"] is False


def test_search_refuses_a_corpus_that_cannot_contain_1000_training_games_plus_holds():
    rows = []
    start = date(2025, 4, 1)
    for offset in range(93):
        day = (start + timedelta(days=offset)).isoformat()
        rows.extend(record(day, offset * 15 + game, True) for game in range(15))
    assert len(rows) == 1395
    result = optimizer.search(rows, optimizer.SearchConfig(maximum_candidates=100))
    assert result["ok"] is False
    assert result["status"] == "ACCUMULATING_HISTORICAL_GAMES"
    assert result["required"] == 1400
    assert result["requiredTrainingGames"] == 1000
    assert result["requiredWalkForwardGames"] == 200
    assert result["requiredUntouchedAuditGames"] == 200


def test_chronological_partitions_keep_whole_dates_disjoint_and_training_at_1000():
    rows = []
    start = date(2025, 4, 1)
    for offset in range(100):
        date_value = (start + timedelta(days=offset)).isoformat()
        rows.extend(record(date_value, offset * 20 + game, True) for game in range(20))
    config = optimizer.SearchConfig(maximum_candidates=100)
    partitions = optimizer.chronological_partitions(rows, config)
    train = set(partitions["train"])
    walk = set(partitions["walkForward"])
    holdout = set(partitions["untouchedHoldout"])
    assert not (train & walk or train & holdout or walk & holdout)
    assert max(train) < min(walk) < min(holdout)
    assert sum(row["slateDateEt"] in train for row in rows) >= 1000
    assert sum(row["slateDateEt"] in walk for row in rows) >= 200
    assert sum(row["slateDateEt"] in holdout for row in rows) >= 200


def test_champion_payload_cannot_be_built_from_failed_gate():
    result = {
        "promotionGate": {"passed": False},
        "candidate": {"policy": copy.deepcopy(policy.BASELINE_POLICY)},
    }
    try:
        optimizer.champion_payload(result, {}, "2026-07-23T00:00:00+00:00")
        assert False, "expected HistoricalOptimizerError"
    except optimizer.HistoricalOptimizerError:
        pass


def test_valid_promotion_payload_passes_runtime_contract():
    gate = {
        "version": policy.PROMOTION_GATE_VERSION,
        "passed": True,
        "errors": [],
        "settledGameCount": 1400,
        "trainingGameCount": 1000,
        "walkForwardGameCount": 200,
        "untouchedHoldoutGameCount": 200,
        "walkForwardDayCount": 20,
        "untouchedHoldoutDayCount": 15,
        "walkForwardMinimumDailyAccuracy": 0.80,
        "walkForwardMeanDailyAccuracy": 0.84,
        "untouchedHoldoutMinimumDailyAccuracy": 0.80,
        "untouchedHoldoutMeanDailyAccuracy": 0.83,
        "walkForwardSlateCoverage": 1.0,
        "untouchedHoldoutSlateCoverage": 1.0,
        "holdoutWasUntouchedDuringSearch": True,
        "chronologicalWholeSlateSplits": True,
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
        "overfitChecksPassed": True,
    }
    result = {
        "promotionGate": gate,
        "candidate": {"policy": copy.deepcopy(policy.BASELINE_POLICY)},
        "datasetFingerprint": "b" * 64,
        "searchVersion": optimizer.SEARCH_VERSION,
    }
    artifact = {"bucket": "bucket", "key": "experiment.json", "versionId": "v1", "sha256": "a" * 64}
    champion = optimizer.champion_payload(result, artifact, "2026-07-23T00:00:00+00:00")
    validation = policy.validate_champion({"record_type": policy.CHAMPION_RECORD_TYPE, "data": champion})
    assert validation.ok, validation.errors


def test_explicit_fresh_untouched_window_is_strictly_later_and_never_overlaps_development():
    rows = []
    start = date(2025, 4, 1)
    # 105 complete 20-game dates: 70 development/train, 20 walk-forward,
    # and 15 explicitly fresh untouched-audit dates.
    for offset in range(105):
        day = (start + timedelta(days=offset)).isoformat()
        rows.extend(record(day, offset * 20 + game, True) for game in range(20))
    fresh_dates = sorted({row["slateDateEt"] for row in rows})[-15:]
    config = optimizer.SearchConfig(maximum_candidates=100)
    partitions = optimizer.chronological_partitions(
        rows, config, untouched_holdout_dates=fresh_dates
    )
    assert partitions["untouchedHoldout"] == fresh_dates
    assert max(partitions["walkForward"]) < min(fresh_dates)
    assert not set(partitions["train"]) & set(fresh_dates)
    assert not set(partitions["walkForward"]) & set(fresh_dates)
    assert sum(row["slateDateEt"] in fresh_dates for row in rows) == 300


def test_explicit_fresh_untouched_window_rejects_any_reused_or_nonlater_date():
    rows = []
    start = date(2025, 4, 1)
    for offset in range(105):
        day = (start + timedelta(days=offset)).isoformat()
        rows.extend(record(day, offset * 20 + game, True) for game in range(20))
    all_dates = sorted({row["slateDateEt"] for row in rows})
    # Includes an older development date while retaining 15 days and 300 games.
    invalid = [all_dates[0], *all_dates[-14:]]
    try:
        optimizer.chronological_partitions(
            rows,
            optimizer.SearchConfig(maximum_candidates=100),
            untouched_holdout_dates=invalid,
        )
        assert False, "expected HistoricalOptimizerError"
    except optimizer.HistoricalOptimizerError as exc:
        assert "strictly after development" in str(exc)


def test_home_and_away_probabilities_are_complementary_for_scoring_metrics():
    row = record("2025-07-01", 1, True)
    selected = optimizer.predict_record(row, policy.BASELINE_POLICY)
    chosen, home, away = policy.select_winner(
        row["homeSignal"], row["awaySignal"], policy.BASELINE_POLICY
    )
    home_probability, away_probability = policy.complementary_probabilities(home, away)
    assert home_probability + away_probability == 1.0
    assert selected["homeWinProbability"] == home_probability
    assert chosen["side"] == "home"


def _historical_event(event_id, home, away, commence, home_price, away_price):
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "bookmakers": [
            {
                "key": "fanduel",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": home_price},
                            {"name": away, "price": away_price},
                        ],
                    }
                ],
            }
        ],
    }


def test_build_slate_dataset_clips_each_game_at_its_own_t45_boundary():
    eastern = ZoneInfo("America/New_York")
    early_start = datetime(2025, 7, 4, 3, 0, tzinfo=eastern)
    late_start = datetime(2025, 7, 4, 4, 0, tzinfo=eastern)
    grid = optimizer.build_snapshot_grid("2025-07-04", [early_start, late_start])
    official = [
        {
            "officialGamePk": "early",
            "homeTeam": "Early Home",
            "awayTeam": "Early Away",
            "gameDate": early_start.isoformat(),
            "winner": "Early Home",
            "completed": True,
        },
        {
            "officialGamePk": "late",
            "homeTeam": "Late Home",
            "awayTeam": "Late Away",
            "gameDate": late_start.isoformat(),
            "winner": "Late Home",
            "completed": True,
        },
    ]
    snapshots = []
    for index, requested in enumerate(grid.timestamps_utc):
        # Deliberately move the early-game price after its lock. Those later
        # observations remain present in the sport-level payload but must never
        # enter the early game's feature vector.
        early_home_price = -110 - index * 10
        early_away_price = 100 + index * 10
        late_home_price = -105 - index * 5
        late_away_price = -105 + index * 5
        snapshots.append(
            {
                "requestedAtUtc": requested,
                "payload": {
                    "timestamp": requested,
                    "data": [
                        _historical_event(
                            "provider-early",
                            "Early Home",
                            "Early Away",
                            early_start.isoformat(),
                            early_home_price,
                            early_away_price,
                        ),
                        _historical_event(
                            "provider-late",
                            "Late Home",
                            "Late Away",
                            late_start.isoformat(),
                            late_home_price,
                            late_away_price,
                        ),
                    ],
                },
            }
        )

    dataset = optimizer.build_slate_dataset("2025-07-04", official, snapshots, grid)
    assert dataset["completeSlate"] is True
    rows = {row["officialGamePk"]: row for row in dataset["records"]}
    early = rows["early"]
    late = rows["late"]
    assert early["observedHomePullCount"] == 6  # 01:00 through 02:15 ET
    assert late["observedHomePullCount"] == 10  # 01:00 through 03:15 ET
    assert early["observedHomePullCount"] < late["observedHomePullCount"]

    early_lock_index = 5
    expected_early_fair, _ = optimizer.devig_pair(
        -110 - early_lock_index * 10,
        100 + early_lock_index * 10,
    )
    leaked_final_fair, _ = optimizer.devig_pair(
        -110 - (len(grid.timestamps_utc) - 1) * 10,
        100 + (len(grid.timestamps_utc) - 1) * 10,
    )
    assert abs(early["homeSignal"]["probLatest"] - expected_early_fair) < 1e-12
    assert abs(early["homeSignal"]["probLatest"] - leaked_final_fair) > 1e-4
    assert dataset["snapshotAudit"][-1]["matchedOfficialGames"] == 2
    assert dataset["snapshotAudit"][-1]["acceptedBeforePerGameLock"] == 1


def test_compiled_search_metrics_match_rich_runtime_proof_within_numeric_tolerance():
    rows = []
    start = date(2025, 7, 1)
    for offset in range(3):
        day = (start + timedelta(days=offset)).isoformat()
        rows.extend(
            record(day, offset * 10 + game, home_won=(game % 2 == 0))
            for game in range(10)
        )
    dates = sorted({row["slateDateEt"] for row in rows})
    rich = optimizer.evaluate_policy(rows, policy.BASELINE_POLICY, dates)
    compiled = optimizer._evaluate_compiled_partition(
        optimizer._compile_partition_for_search(rows, dates),
        policy.BASELINE_POLICY,
        daily_target=0.80,
    )
    for field in (
        "gameCount",
        "dayCount",
        "correct",
        "overallAccuracy",
        "minimumDailyAccuracy",
        "meanDailyAccuracy",
        "dailyPassRate",
        "minimumSlateCoverage",
        "meanSlateCoverage",
    ):
        assert compiled[field] == rich[field]
    assert abs(compiled["brierScore"] - rich["brierScore"]) < 1e-7
    assert abs(compiled["logLoss"] - rich["logLoss"]) < 1e-7


def test_compiled_search_evaluator_matches_rich_production_formula_exactly():
    rows = []
    start = date(2025, 7, 1)
    for offset in range(4):
        day = (start + timedelta(days=offset)).isoformat()
        for game in range(10):
            item = record(day, offset * 10 + game, home_won=(game + offset) % 3 != 0)
            item["homeSignal"]["delta"] = ((game % 5) - 2) * 0.009
            item["awaySignal"]["delta"] = -item["homeSignal"]["delta"]
            item["homeSignal"]["bookDivergence"] = (game % 4) * 0.02
            item["awaySignal"]["bookDivergence"] = ((game + 1) % 4) * 0.02
            item["homeSignal"]["reversalCount"] = game % 4
            item["awaySignal"]["reversalCount"] = (game + 2) % 4
            item["homeSignal"]["tags"] = ["BOOK_AGREEMENT", "STEAM"] if game % 2 == 0 else ["RESISTANCE"]
            item["awaySignal"]["tags"] = ["BOOK_AGREEMENT", "RUN_LINE_CONFIRMATION"] if game % 3 == 0 else ["COMPRESSED_MARKET"]
            rows.append(item)

    dates = sorted({row["slateDateEt"] for row in rows})
    compiled = optimizer._compile_partition_for_search(rows, dates)
    candidates = list(
        optimizer.candidate_policies(
            optimizer.SearchConfig(maximum_candidates=100, random_seed=911)
        )
    )
    for candidate in candidates:
        rich = optimizer.evaluate_policy(rows, candidate, dates)
        fast = optimizer._evaluate_compiled_partition(
            compiled,
            candidate,
            daily_target=policy.MIN_DAILY_ACCURACY,
        )
        for key in (
            "gameCount",
            "dayCount",
            "correct",
            "overallAccuracy",
            "minimumDailyAccuracy",
            "meanDailyAccuracy",
            "dailyPassRate",
            "minimumSlateCoverage",
            "meanSlateCoverage",
            "brierScore",
            "logLoss",
        ):
            assert abs(float(fast[key]) - float(rich[key])) < 1e-12, key
        assert fast["daily"] == rich["daily"]
