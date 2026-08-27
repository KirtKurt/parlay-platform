from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v3 as v3
import reconcile_mlb_prospective_backlog_v4 as v4
import reconcile_mlb_prospective_backlog_v5 as subject


class ResponseStream(io.BytesIO):
    pass


class FakeLambda:
    def __init__(self, response):
        self.response = response

    def invoke(self, *, FunctionName, InvocationType, Payload, LogType="None"):
        del FunctionName, Payload
        assert InvocationType == "RequestResponse"
        assert LogType in {"None", "Tail"}
        return {
            "StatusCode": 200,
            "Payload": ResponseStream(json.dumps(self.response).encode("utf-8")),
        }


def api_gateway(status, body):
    return {"statusCode": status, "body": json.dumps(body)}


def settlement_gap(slate_date="2026-08-04", games=15):
    return {
        "ok": False,
        "sport": "mlb",
        "status": "FAILED_CLOSED",
        "overall_status": "FAILED_CLOSED",
        "authoritativeSettlement": True,
        "slateDateEt": slate_date,
        "slate_date_et": slate_date,
        "officialGameCount": games,
        "officialFinalCount": games,
        "canonicalLockCount": 0,
        "rejectedCanonicalLockCount": 0,
        "terminalNoPredictionCount": 0,
        "lockTerminalConflictCount": 0,
        "terminalNoPredictionExcludedCount": 0,
        "skippedNotFinalCount": 0,
        "missingCanonicalLockCount": games,
        "identityRejectionCount": 0,
        "labelConflictCount": 0,
        "immutablePregameRowsMutated": False,
        "missingCanonicalLocks": [
            {
                "officialGamePk": str(820000 + index),
                "reason": subject.MISSING_LOCK_REASON,
            }
            for index in range(games)
        ],
    }


def official_terminal_status(slate_date="2026-08-04", games=15):
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": slate_date,
        "officialScheduleBacked": True,
        "officialScheduleAuthorityVersion": base.OFFICIAL_SCHEDULE_AUTHORITY_VERSION,
        "officialScheduleAuthoritativeStartTimes": True,
        "gameCount": games,
        "officialScheduleGameCount": games,
        "lockedPredictionCount": 0,
        "noPredictionDataCount": games,
        "lockedStatusCount": games,
        "lockStatusComplete": True,
    }


def replay_required(slate_date="2026-08-04"):
    detail = subject._terminal_replay_detail(
        409,
        settlement_gap(slate_date),
        {"run": subject.SETTLEMENT_RUN, "slate_date": slate_date},
    )
    assert detail is not None
    return subject.DurableTerminalReplayRequired(slate_date, detail)


def test_exact_conflict_free_settlement_gap_requests_protected_replay():
    client = FakeLambda(api_gateway(409, settlement_gap()))
    with pytest.raises(subject.DurableTerminalReplayRequired) as raised:
        subject.invoke_json_preserving_status_body(
            client,
            "results",
            {
                "sport": "mlb",
                "run": subject.SETTLEMENT_RUN,
                "slate_date": "2026-08-04",
                "days_from": 0,
            },
        )
    assert raised.value.slate_date == "2026-08-04"
    assert raised.value.detail["missingCanonicalLockCount"] == 15
    assert raised.value.detail["conflictFree"] is True


def test_identity_conflict_never_requests_replay():
    body = settlement_gap()
    body["identityRejectionCount"] = 1
    client = FakeLambda(api_gateway(409, body))
    with pytest.raises(base.ReconciliationError) as raised:
        subject.invoke_json_preserving_status_body(
            client,
            "results",
            {"run": subject.SETTLEMENT_RUN, "slate_date": "2026-08-04"},
        )
    assert not isinstance(raised.value, subject.DurableTerminalReplayRequired)
    assert "lambda_application_status_not_success" in str(raised.value)


def test_rejected_terminal_identity_never_requests_blind_replay():
    body = settlement_gap()
    body["rejectedTerminalOutcomes"] = [
        {
            "sourcePk": "LOCKED_PICKS#mlb#2026-08-04",
            "sourceSk": "PER_GAME_LOCK_OUTCOME#TMINUS45#legacy",
            "errors": ["terminal_official_game_pk_unresolved"],
        }
    ]
    client = FakeLambda(api_gateway(409, body))

    with pytest.raises(base.ReconciliationError) as raised:
        subject.invoke_json_preserving_status_body(
            client,
            "results",
            {"run": subject.SETTLEMENT_RUN, "slate_date": "2026-08-04"},
        )

    assert not isinstance(raised.value, subject.DurableTerminalReplayRequired)
    assert "rejectedTerminalOutcomesObservedCount" in str(raised.value)


def test_reconcile_replays_then_retries_full_backlog(monkeypatch):
    attempts = []
    error = replay_required()

    def fake_reconcile(*args, **kwargs):
        del args, kwargs
        attempts.append("attempt")
        if len(attempts) == 1:
            raise error
        return {
            "ok": True,
            "version": v4.VERSION,
            "directTableWrite": False,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "promotionAuthorityChanged": False,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
        }

    monkeypatch.setattr(v4, "reconcile", fake_reconcile)
    monkeypatch.setattr(
        base,
        "resolve_stack_functions",
        lambda *args: base.StackFunctions("lock", "results", "trainer"),
    )
    invocations = []

    def fake_invoke(client, function, event):
        del client
        invocations.append((function, event))
        if event.get("httpMethod") == "GET":
            return official_terminal_status()
        return {"ok": True, "sport": "mlb", "slateDateEt": "2026-08-04"}

    monkeypatch.setattr(v4, "invoke_json_with_backpressure", fake_invoke)
    monkeypatch.setattr(
        v3,
        "validate_lock_result",
        lambda replay, status, slate: {
            "slateDateEt": slate,
            "manifestGameCount": 15,
            "canonicalPredictionCount": 0,
            "terminalNoPredictionCount": 15,
            "lockOutcomeCount": 15,
            "officialStatusReadBound": True,
        },
    )

    result = subject.reconcile(
        object(), object(), stack_name="stack", max_slate_days=30
    )
    assert len(attempts) == 2
    assert invocations[0][1]["run"] == subject.TERMINAL_REPLAY_RUN
    assert invocations[0][1]["force"] is True
    assert invocations[1][1]["httpMethod"] == "GET"
    assert result["settlementTriggeredProtectedTerminalReplayCount"] == 1
    assert result["settlement409TreatedAsSuccess"] is False
    assert result["directTableWrite"] is False
    assert result["postStartPredictionCreationAllowed"] is False


def test_protected_replay_retries_lease_overlap_without_bypassing_owner(monkeypatch):
    monkeypatch.setattr(
        base,
        "resolve_stack_functions",
        lambda *args: base.StackFunctions("lock", "results", "trainer"),
    )
    calls = []

    def fake_invoke(client, function, event):
        del client, function
        calls.append(event)
        if event.get("httpMethod") == "GET":
            return official_terminal_status()
        if len([call for call in calls if call.get("force") is True]) == 1:
            return {
                "ok": True,
                "sport": "mlb",
                "slateDateEt": "2026-08-04",
                "skipped": True,
                "reason": "SKIPPED_OVERLAPPING_LOCK_EXECUTION",
                "mutatingRunAttempted": False,
            }
        return {
            "ok": True,
            "sport": "mlb",
            "slateDateEt": "2026-08-04",
            "perGameLockProgress": {
                "manifestGameCount": 15,
                "canonicalCount": 0,
                "noPredictionDataCount": 15,
                "lockOutcomeCount": 15,
                "missedCount": 0,
                "dueMissingCount": 0,
            },
        }

    monkeypatch.setattr(v4, "invoke_json_with_backpressure", fake_invoke)
    sleeps = []

    result = subject._execute_protected_terminal_replay(
        object(),
        object(),
        stack_name="stack",
        request=replay_required(),
        sleep=sleeps.append,
        max_attempts=3,
    )

    assert result["protectedLockReplayAttemptCount"] == 2
    assert result["protectedLockReplayOverlapRetryCount"] == 1
    assert sleeps == [20]
    assert [call.get("force") for call in calls].count(True) == 2
    assert [call.get("httpMethod") for call in calls].count("GET") == 1


def test_protected_replay_waits_through_full_lease_then_succeeds(monkeypatch):
    monkeypatch.setattr(
        base,
        "resolve_stack_functions",
        lambda *args: base.StackFunctions("lock", "results", "trainer"),
    )
    forced_attempts = 0

    def fake_invoke(client, function, event):
        nonlocal forced_attempts
        del client, function
        if event.get("httpMethod") == "GET":
            return official_terminal_status()
        forced_attempts += 1
        if forced_attempts <= 18:
            return {
                "ok": True,
                "sport": "mlb",
                "slateDateEt": "2026-08-04",
                "skipped": True,
                "reason": "SKIPPED_OVERLAPPING_LOCK_EXECUTION",
                "mutatingRunAttempted": False,
            }
        return {
            "ok": True,
            "sport": "mlb",
            "slateDateEt": "2026-08-04",
            "perGameLockProgress": {
                "manifestGameCount": 15,
                "canonicalCount": 0,
                "noPredictionDataCount": 15,
                "lockOutcomeCount": 15,
                "missedCount": 0,
                "dueMissingCount": 0,
            },
        }

    monkeypatch.setattr(v4, "invoke_json_with_backpressure", fake_invoke)
    sleeps = []

    result = subject._execute_protected_terminal_replay(
        object(),
        object(),
        stack_name="stack",
        request=replay_required(),
        sleep=sleeps.append,
    )

    assert forced_attempts == 19
    assert result["protectedLockReplayAttemptCount"] == 19
    assert result["protectedLockReplayOverlapRetryCount"] == 18
    assert sum(sleeps) >= (
        subject.PROTECTED_REPLAY_LEASE_SECONDS
        + subject.PROTECTED_REPLAY_SCHEDULING_MARGIN_SECONDS
    )
    assert result["protectedLockReplayRetryScheduleDephased"] is True
    assert result["protectedLockReplayRetryDistinctMinutePhaseCount"] == len(
        subject.PROTECTED_REPLAY_RETRY_DELAYS_SECONDS
    )
    assert result["protectedLockReplayRetryHorizonSeconds"] >= (
        subject.PROTECTED_REPLAY_COOPERATIVE_BOUND_SECONDS
    )


def test_protected_replay_retry_schedule_dephases_every_minute_attempt():
    delays = subject.PROTECTED_REPLAY_RETRY_DELAYS_SECONDS
    elapsed = 0
    phases = []
    for delay in delays:
        elapsed += delay
        phases.append(
            elapsed % subject.PROTECTED_REPLAY_SCHEDULE_PERIOD_SECONDS
        )

    assert len(delays) == subject.MAX_PROTECTED_REPLAY_ATTEMPTS - 1
    assert sum(delays) == subject.PROTECTED_REPLAY_RETRY_HORIZON_SECONDS
    assert sum(delays[:-1]) >= subject.PROTECTED_REPLAY_COOPERATIVE_BOUND_SECONDS
    assert tuple(phases) == subject.PROTECTED_REPLAY_RETRY_PHASES_SECONDS
    assert len(set(phases)) == len(phases)


def test_protected_replay_polls_eventbridge_handoff_validates_then_acks(
    monkeypatch,
):
    monkeypatch.setattr(
        base,
        "resolve_stack_functions",
        lambda *args: base.StackFunctions("lock", "results", "trainer"),
    )
    calls = []
    polls = 0

    def fake_invoke(client, function, event):
        nonlocal polls
        del client, function
        calls.append(dict(event))
        if event.get("httpMethod") == "GET":
            return official_terminal_status()
        if event.get("acknowledgeCooperativeCompletion") is True:
            return {
                "ok": True,
                "sport": "mlb",
                "slateDateEt": "2026-08-04",
                "cooperativeTerminalReplayAcknowledged": True,
                "cooperativeTerminalReplay": {
                    "state": "ACKNOWLEDGED",
                },
            }
        polls += 1
        if polls <= 2:
            return {
                "ok": True,
                "sport": "mlb",
                "slateDateEt": "2026-08-04",
                "status": "QUEUED_FOR_EVENTBRIDGE_LOCK_OWNER",
                "reason": "QUEUED_FOR_EVENTBRIDGE_LOCK_OWNER",
                "skipped": True,
                "mutatingRunAttempted": False,
                "cooperativeTerminalReplayCompleted": False,
                "cooperativeTerminalReplay": {
                    "state": "QUEUED" if polls == 1 else "CLAIMED",
                },
            }
        progress = {
            "manifestGameCount": 15,
            "canonicalCount": 0,
            "noPredictionDataCount": 15,
            "lockOutcomeCount": 15,
            "missedCount": 0,
            "dueMissingCount": 0,
        }
        return {
            "ok": True,
            "sport": "mlb",
            "slateDateEt": "2026-08-04",
            "reason": "PROVEN_NO_PREDICTION_TERMINALS_RECONCILED",
            "postStartPredictionCreationAllowed": False,
            "perGameLockProgress": progress,
            "missedLockTerminalReconciliation": {
                "ok": True,
                "slateDateEt": "2026-08-04",
                "reconciledCount": 15,
                "remainingMissedCount": 0,
                "unresolved": [],
                "progressAfter": progress,
                "postStartPredictionCreationAllowed": False,
            },
            # The server auto-acknowledges on the first completed poll so an
            # older v5.7 checkout can safely advance to its next exact date.
            "cooperativeTerminalReplayCompleted": True,
            "cooperativeTerminalReplay": {
                "state": "ACKNOWLEDGED",
            },
        }

    monkeypatch.setattr(v4, "invoke_json_with_backpressure", fake_invoke)
    sleeps = []

    result = subject._execute_protected_terminal_replay(
        object(),
        object(),
        stack_name="stack",
        request=replay_required(),
        sleep=sleeps.append,
        max_attempts=5,
    )

    assert sleeps == [20, 61]
    assert result["protectedLockReplayAttemptCount"] == 3
    assert result["protectedLockReplayOverlapRetryCount"] == 0
    assert result["protectedLockReplayCooperativePollCount"] == 2
    assert result["protectedLockReplayCooperativeHandoffObserved"] is True
    assert result["protectedLockReplayCooperativeAcknowledged"] is True
    assert result["protectedLockReplayAutomaticExecutionOwner"] == (
        "eventbridge_daily_lock_schedule"
    )
    assert result["directWorkflowTableWrite"] is False
    assert result["activeLeaseMutationAllowed"] is False
    assert result["immutablePredictionRewriteAllowed"] is False
    assert [call.get("httpMethod") for call in calls].count("GET") == 1
    assert [
        call for call in calls if call.get("acknowledgeCooperativeCompletion")
    ]


def test_cooperative_handoff_exhaustion_is_bounded_and_fails_closed(
    monkeypatch,
):
    monkeypatch.setattr(
        base,
        "resolve_stack_functions",
        lambda *args: base.StackFunctions("lock", "results", "trainer"),
    )

    def queued(client, function, event):
        del client, function, event
        return {
            "ok": True,
            "sport": "mlb",
            "slateDateEt": "2026-08-04",
            "status": "QUEUED_FOR_EVENTBRIDGE_LOCK_OWNER",
            "reason": "QUEUED_FOR_EVENTBRIDGE_LOCK_OWNER",
            "skipped": True,
            "mutatingRunAttempted": False,
            "cooperativeTerminalReplayCompleted": False,
            "cooperativeTerminalReplay": {"state": "QUEUED"},
        }

    monkeypatch.setattr(v4, "invoke_json_with_backpressure", queued)
    sleeps = []
    with pytest.raises(
        base.ReconciliationError,
        match=(
            "protected_terminal_replay_cooperative_retry_exhausted:"
            "2026-08-04"
        ),
    ):
        subject._execute_protected_terminal_replay(
            object(),
            object(),
            stack_name="stack",
            request=replay_required(),
            sleep=sleeps.append,
        )

    assert len(sleeps) == subject.MAX_PROTECTED_REPLAY_ATTEMPTS - 1
    assert sum(sleeps) == subject.PROTECTED_REPLAY_RETRY_HORIZON_SECONDS


def test_protected_replay_overlap_exhaustion_remains_bounded_fail_closed(
    monkeypatch,
):
    monkeypatch.setattr(
        base,
        "resolve_stack_functions",
        lambda *args: base.StackFunctions("lock", "results", "trainer"),
    )

    def always_overlap(client, function, event):
        del client, function, event
        return {
            "ok": True,
            "sport": "mlb",
            "slateDateEt": "2026-08-04",
            "skipped": True,
            "reason": "SKIPPED_OVERLAPPING_LOCK_EXECUTION",
            "mutatingRunAttempted": False,
        }

    monkeypatch.setattr(v4, "invoke_json_with_backpressure", always_overlap)
    sleeps = []

    with pytest.raises(
        base.ReconciliationError,
        match="protected_terminal_replay_overlap_retry_exhausted:2026-08-04",
    ):
        subject._execute_protected_terminal_replay(
            object(),
            object(),
            stack_name="stack",
            request=replay_required(),
            sleep=sleeps.append,
        )

    assert len(sleeps) == subject.MAX_PROTECTED_REPLAY_ATTEMPTS - 1
    assert sum(sleeps) == subject.PROTECTED_REPLAY_RETRY_HORIZON_SECONDS


def test_same_slate_remaining_incomplete_after_replay_fails_closed(monkeypatch):
    error = replay_required()
    monkeypatch.setattr(
        v4,
        "reconcile",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        subject,
        "_execute_protected_terminal_replay",
        lambda *args, **kwargs: {"slateDateEt": "2026-08-04"},
    )
    with pytest.raises(
        base.ReconciliationError,
        match="settlement_terminal_replay_failed_to_close_gap:2026-08-04",
    ):
        subject.reconcile(
            object(), object(), stack_name="stack", max_slate_days=30
        )


def test_source_has_no_direct_storage_prediction_or_authority_writer():
    source = (
        ROOT / "scripts" / "reconcile_mlb_prospective_backlog_v5.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "put_item(",
        "update_item(",
        "delete_item(",
        "predictedWinner",
        "predicted_winner",
        "INQSI_MLB_ML_AUTO_PROMOTE",
        "productionAuthorityChanged = True",
        "liveInferenceAuthority = True",
    ):
        assert forbidden not in source
    assert subject.SETTLEMENT_RUN in source
    assert subject.TERMINAL_REPLAY_RUN in source
    assert "settlement409TreatedAsSuccess" in source


def test_default_protected_replay_horizon_covers_bounded_two_phase_handoff():
    assert subject.MAX_PROTECTED_REPLAY_ATTEMPTS == 70
    assert len(subject.PROTECTED_REPLAY_RETRY_DELAYS_SECONDS) == 69
    assert 60 * 60 <= subject.PROTECTED_REPLAY_RETRY_HORIZON_SECONDS <= 75 * 60
    assert subject.PROTECTED_REPLAY_RETRY_HORIZON_SECONDS == sum(
        subject.PROTECTED_REPLAY_RETRY_DELAYS_SECONDS
    )
