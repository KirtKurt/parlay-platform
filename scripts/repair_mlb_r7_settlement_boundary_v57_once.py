from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"expected {label} anchor was not found")
    return text.replace(old, new, 1)


def patch_v4() -> None:
    path = ROOT / "scripts" / "reconcile_mlb_prospective_backlog_v4.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''VERSION = (
    "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v4.4-"
    "verified-persistent-missed-replay"
)
''',
        '''VERSION = (
    "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v4.5-"
    "explicit-settlement-authority-boundary"
)
''',
        "v4 version",
    )
    text = replace_once(
        text,
        '''    invoke: Any = invoke_json_with_backpressure,
    status_sleep: Any = time.sleep,
) -> Dict[str, Any]:
''',
        '''    invoke: Any = invoke_json_with_backpressure,
    status_sleep: Any = time.sleep,
    settlement_authoritative_persistent_missed: bool = False,
) -> Dict[str, Any]:
''',
        "v4 settlement-authority argument",
    )
    text = replace_once(
        text,
        '''            lock_evidence = _official_evidence(official_status, slate_date)
            if _status_requires_terminal_durability_replay(official_status):
                raise base.ReconciliationError(
                    "official_status_terminal_durability_incomplete"
                )
''',
        '''            lock_evidence = _official_evidence(official_status, slate_date)
            if (
                _status_requires_terminal_durability_replay(official_status)
                and not settlement_authoritative_persistent_missed
            ):
                raise base.ReconciliationError(
                    "official_status_terminal_durability_incomplete"
                )
''',
        "v4 initial persistent-missed gate",
    )
    text = replace_once(
        text,
        '''        "statusFirst": True,
        "readOnlyOfficialStatusProof": True,
''',
        '''        "statusFirst": True,
        "settlementAuthoritativePersistentMissed": (
            settlement_authoritative_persistent_missed
        ),
        "readOnlyOfficialStatusProof": True,
''',
        "v4 result evidence",
    )
    path.write_text(text, encoding="utf-8")


def patch_v5() -> None:
    path = ROOT / "scripts" / "reconcile_mlb_prospective_backlog_v5.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''VERSION = (
    "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v5.6-"
    "dephased-lease-retry-exact-slate-terminal-identity"
)
''',
        '''VERSION = (
    "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v5.7-"
    "settlement-authoritative-persistent-missed-boundary"
)
''',
        "v5 version",
    )
    text = replace_once(
        text,
        '''    max_replays = int(kwargs.get("max_slate_days") or base.DEFAULT_MAX_SLATE_DAYS)
    repaired: Dict[str, Dict[str, Any]] = {}

    for _ in range(max_replays + 1):
''',
        '''    max_replays = int(kwargs.get("max_slate_days") or base.DEFAULT_MAX_SLATE_DAYS)
    repaired: Dict[str, Dict[str, Any]] = {}
    # V5 alone may defer a complete read-only status that preserves historical
    # MISSED_NOT_BACKFILLED telemetry to canonical settlement. Standalone V4
    # retains its default protected-replay gate. Settlement remains fail closed:
    # only the exact conflict-free 409 shape can trigger the validated replay.
    kwargs["settlement_authoritative_persistent_missed"] = True

    for _ in range(max_replays + 1):
''',
        "v5 settlement-authority handoff",
    )
    text = replace_once(
        text,
        '''    value["settlementTriggeredProtectedTerminalReplayCount"] = len(repaired)
''',
        '''    value["settlementAuthoritativePersistentMissed"] = True
    value["settlementTriggeredProtectedTerminalReplayCount"] = len(repaired)
''',
        "v5 result evidence",
    )
    path.write_text(text, encoding="utf-8")


def write_regression_test() -> None:
    path = (
        ROOT
        / "tests"
        / "unit"
        / "test_reconcile_mlb_prospective_backlog_v5_persistent_missed.py"
    )
    path.write_text(
        '''from __future__ import annotations

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
''',
        encoding="utf-8",
    )


def main() -> None:
    patch_v4()
    patch_v5()
    write_regression_test()


if __name__ == "__main__":
    main()
