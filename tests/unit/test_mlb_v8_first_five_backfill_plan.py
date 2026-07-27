from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import plan_mlb_v8_historical_first_five_backfill as planner


def _state(game_count=3108):
    return {
        "revision": 1400,
        "phase": "BACKFILLING",
        "currentDate": "2026-05-18",
        "completeSlateCount": 2,
        "completedSlates": [
            {"slateDateEt": "2026-05-17", "featureDatasetVersion": planner.EXPECTED_DATASET},
            {"slateDateEt": "2026-05-18", "featureDatasetVersion": planner.EXPECTED_DATASET},
        ],
        "eligibleGameCount": game_count,
        "featureDatasetVersion": planner.EXPECTED_DATASET,
        "featureRematerializationComplete": True,
        "featureRematerializedSlateCount": 2,
        "featureRematerializationTotalSlateCount": 2,
        "featureRematerializationPaidHistoricalCalls": 0,
        "featureRematerializationErrors": [],
        "lastError": None,
        "creditsConsumed": 203100,
        "maximumCredits": 300000,
        "lastQuota": {"x-requests-remaining": 4672852},
    }


def _items(count=3):
    return [
        {
            "slateDateEt": "2026-05-17",
            "officialGamePk": str(1000 + index),
            "providerEventId": f"event-{index}",
            "predictionLockAtUtc": f"2026-05-17T{10 + index:02d}:00:00Z",
            "homeTeam": f"Home {index}",
            "awayTeam": f"Away {index}",
            "sourceDataset": {
                "bucket": "historical",
                "key": f"dataset-{index}.json",
                "sha256": f"sha-{index}",
                "versionId": f"version-{index}",
            },
        }
        for index in range(count)
    ]


def test_full_current_cohort_cost_fits_75k_plan_ceiling():
    cost = planner._cost(3108)
    assert cost["historicalRequestCount"] == 6216
    assert cost["creditsPerHistoricalRequest"] == 10
    assert cost["estimatedCredits"] == 62160
    assert cost["estimatedCredits"] <= planner.DEFAULT_MAX_CREDITS == 75000


def test_plan_is_outcome_invariant_and_never_authorizes_itself():
    state = _state()
    first_items = _items()
    second_items = copy.deepcopy(first_items)
    for index, row in enumerate(first_items):
        row["homeWon"] = index % 2
        row["winner"] = row["homeTeam"] if row["homeWon"] else row["awayTeam"]
    for index, row in enumerate(second_items):
        row["homeWon"] = 1 - (index % 2)
        row["winner"] = row["homeTeam"] if row["homeWon"] else row["awayTeam"]
    first = planner.build_plan(state=state, items=first_items, maximum_credits=75000)
    second = planner.build_plan(state=state, items=second_items, maximum_credits=75000)
    assert first["selectionUsedOutcomes"] is False
    assert first["selectionFingerprint"] == second["selectionFingerprint"]
    assert first["planFingerprint"] == second["planFingerprint"]
    assert first["authorization"]["required"] is True
    assert first["authorization"]["authorized"] is False
    assert first["authorization"]["executionAllowed"] is False
    assert first["authorization"]["paidCollectionStarted"] is False
    assert first["authority"] == "PLAN_ONLY"
    assert first["providerCallsMade"] == 0
    assert first["productionAuthorityChanged"] is False


def test_plan_fails_when_state_pointer_counts_are_stale():
    state = _state()
    state["featureRematerializedSlateCount"] = 1
    checks = planner._state_checks(state)
    assert checks["rematerializedCountMatches"] is False
    try:
        planner.build_plan(state=state, items=_items(), maximum_credits=75000)
        assert False, "expected stale pointer state to fail"
    except RuntimeError as exc:
        assert "historical_state_not_ready" in str(exc)


def test_plan_fails_when_credit_reserve_is_insufficient():
    state = _state()
    state["maximumCredits"] = 203120
    try:
        planner.build_plan(state=state, items=_items(), maximum_credits=75000)
        assert False, "expected configured credit guard to fail"
    except RuntimeError as exc:
        assert "first_five_backfill_budget_blocked" in str(exc)


def test_planner_source_contains_no_provider_request_path():
    source = Path("scripts/plan_mlb_v8_historical_first_five_backfill.py").read_text()
    assert "ODDS_API_KEY" not in source
    assert "requests.get" not in source
    assert "urllib.request" not in source
    assert "event_odds_url" not in source
    assert '"providerCallsMade": 0' in source
    assert '"executionAllowed": False' in source
    assert 'IfNoneMatch="*"' in source
    assert "put_item(" not in source
