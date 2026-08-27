from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v4 as v4
import reconcile_mlb_prospective_backlog_v5 as v5


SLATE = "2026-08-04"


class FakeCloudFormation:
    def describe_stack_resource(self, *, StackName, LogicalResourceId):
        assert StackName == "stack"
        return {
            "StackResourceDetail": {
                "PhysicalResourceId": f"physical-{LogicalResourceId}"
            }
        }


class FakeLambda:
    def get_function_configuration(self, *, FunctionName):
        assert FunctionName == "physical-MLBMLTrainingFunction"
        return {
            "Environment": {
                "Variables": {
                    "MLB_ML_RELEASE_CUTOFF_UTC": "2026-08-04T04:00:00+00:00"
                }
            }
        }


def official_status_with_persistent_missed():
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": SLATE,
        "gameCount": 15,
        "officialScheduleBacked": True,
        "officialScheduleAuthorityVersion": base.OFFICIAL_SCHEDULE_AUTHORITY_VERSION,
        "officialScheduleAuthoritativeStartTimes": True,
        "officialScheduleGameCount": 15,
        "lockedPredictionCount": 0,
        "noPredictionDataCount": 15,
        "lockedStatusCount": 15,
        "lockStatusComplete": True,
        # Historical lifecycle telemetry intentionally remains nonzero after a
        # durable terminal outcome exists for every official game.
        "missedGameCount": 15,
    }


def successful_settlement():
    return {
        "ok": True,
        "slateDateEt": SLATE,
        "slateFinalized": True,
        "settledLabelCount": 0,
    }


def generic_protected_replay_without_exact_terminal_proof():
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": SLATE,
        "perGameLockProgress": {
            "manifestGameCount": 15,
            "canonicalCount": 0,
            "noPredictionDataCount": 15,
            "lockOutcomeCount": 15,
            "missedCount": 0,
            "dueMissingCount": 0,
        },
    }


def test_v5_reaches_authoritative_settlement_before_replaying_persistent_count():
    calls = []

    def invoke(client, function, event):
        del client, function
        calls.append(event)
        if event.get("httpMethod") == "GET":
            return official_status_with_persistent_missed()
        if event.get("run") == v5.SETTLEMENT_RUN:
            return successful_settlement()
        raise AssertionError(f"unexpected pre-settlement mutation: {event}")

    result = v5.reconcile(
        FakeCloudFormation(),
        FakeLambda(),
        stack_name="stack",
        now_utc=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        max_slate_days=31,
        target_slate_date=SLATE,
        invoke=invoke,
    )

    assert result["ok"] is True
    assert result["exactSlateSelection"] is True
    assert result["selectedSlateDates"] == [SLATE]
    assert result["reconciledSlateCount"] == 1
    assert result["settlementAuthoritativePersistentMissed"] is True
    assert result["settlementTriggeredProtectedTerminalReplayCount"] == 0
    assert not any(event.get("force") is True for event in calls)
    assert [event.get("run") for event in calls].count(v5.SETTLEMENT_RUN) == 1
    assert result["directTableWrite"] is False
    assert result["postStartPredictionCreationAllowed"] is False
    assert result["productionAuthorityChanged"] is False


def test_standalone_v4_keeps_protected_replay_gate_fail_closed():
    calls = []

    def invoke(client, function, event):
        del client, function
        calls.append(event)
        if event.get("httpMethod") == "GET":
            return official_status_with_persistent_missed()
        if event.get("force") is True:
            return generic_protected_replay_without_exact_terminal_proof()
        if event.get("run") == v5.SETTLEMENT_RUN:
            raise AssertionError("standalone V4 must not reach settlement")
        raise AssertionError(event)

    with pytest.raises(
        base.ReconciliationError,
        match="official_status_terminal_durability_incomplete",
    ):
        v4.reconcile(
            FakeCloudFormation(),
            FakeLambda(),
            stack_name="stack",
            now_utc=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
            max_slate_days=31,
            slate_dates=[SLATE],
            invoke=invoke,
        )

    assert [event.get("force") for event in calls].count(True) == 1
    assert not any(event.get("run") == v5.SETTLEMENT_RUN for event in calls)


def test_boundary_sources_contain_no_direct_storage_or_authority_writer():
    for name in (
        "reconcile_mlb_prospective_backlog_v4.py",
        "reconcile_mlb_prospective_backlog_v5.py",
    ):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        for forbidden in (
            "put_item(",
            "update_item(",
            "delete_item(",
            "predictedWinner",
            "predicted_winner",
            "productionAuthorityChanged = True",
            "liveInferenceAuthority = True",
        ):
            assert forbidden not in source
    assert "settlement_authoritative_persistent_missed" in (
        ROOT / "scripts" / "reconcile_mlb_prospective_backlog_v4.py"
    ).read_text(encoding="utf-8")
