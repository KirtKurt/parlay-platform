from __future__ import annotations

import hashlib
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



def fixture_terminal_games(games=15, quarantine=1):
    rows = []
    for index in range(games):
        state = (
            "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
            if index < quarantine
            else "LOCKED_NO_PREDICTION_DATA"
        )
        rows.append(
            {
                "index": index,
                "officialGamePk": str(824805 + index),
                "gameIdentity": f"provider:game-{index}",
                "durableIdentity": f"provider:game-{index}",
                "terminalState": state,
                "evidenceFingerprint": hashlib.sha256(
                    f"evidence:{index}:{state}".encode("utf-8")
                ).hexdigest(),
            }
        )
    return rows


def fixture_lifecycle_rows(games=15, quarantine=1):
    return [
        {
            "officialGamePk": row["officialGamePk"],
            "gameIdentity": row["gameIdentity"],
            "state": row["terminalState"],
            "lockStatus": row["terminalState"],
            "lockedPrediction": False,
            "officialPrediction": False,
            "playable": False,
            "trainingEligible": False,
            "accuracyEligible": False,
            "wagerAllowed": False,
            "predictionAdopted": False,
            "operationalDefect": (
                row["terminalState"]
                == "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
            ),
        }
        for row in fixture_terminal_games(games, quarantine)
    ]


def fixture_terminal_game_set_fingerprint(games=15, quarantine=1):
    return hashlib.sha256(
        json.dumps(
            fixture_terminal_games(games, quarantine),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

def official_terminal_status(
    slate_date="2026-08-04",
    games=15,
    quarantine=1,
):
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
        "noPredictionDataCount": games - quarantine,
        "missedLockValidPrelockQuarantineCount": quarantine,
        "lockedStatusCount": games,
        "lockStatusComplete": True,
        "providerManifestFingerprint": "c" * 64,
        "perGameStatus": fixture_lifecycle_rows(
            games,
            quarantine,
        ),
    }



def cooperative_progress(games=15, quarantine=1):
    return {
        "manifestGameCount": games,
        "processedGameCount": games,
        "verifiedGameCount": games,
        "verificationIndex": games,
        "verificationComplete": True,
        "atomicDurableItemCount": games + 2 * quarantine + 1,
        "atomicDurableReadSetFingerprint": "d" * 64,
        "atomicDurableProofRequired": True,
        "canonicalCount": 0,
        "noPredictionDataCount": games - quarantine,
        "missedLockValidPrelockQuarantineCount": quarantine,
        "lockOutcomeCount": games,
        "missedCount": 0,
        "dueMissingCount": 0,
        "manifestFingerprint": "b" * 64,
        "checkpointFingerprint": "a" * 64,
        "manifestAuthorityEvidenceFingerprint": "e" * 64,
        "providerManifestFingerprint": "c" * 64,
        "terminalGames": fixture_terminal_games(games, quarantine),
        "terminalGameSetFingerprint": (
            fixture_terminal_game_set_fingerprint(games, quarantine)
        ),
    }


def cooperative_public_state(state="COMPLETED", games=15, quarantine=1):
    progress = cooperative_progress(games, quarantine)
    return {
        "version": (
            "MLB-COOPERATIVE-TERMINAL-REPLAY-"
            "v1-eventbridge-owner-handoff"
        ),
        "state": state,
        "slateDateEt": "2026-08-04",
        "ownerIdentifierExposed": False,
        "terminalChunkProgress": {
            "version": (
                "MLB-COOPERATIVE-TERMINAL-CHUNK-"
                "v4-valid-prelock-quarantine"
            ),
            "valid": True,
            "manifestGameCount": games,
            "processedGameCount": games,
            "terminalCount": games,
            "verifiedGameCount": games,
            "verificationComplete": True,
            "canonicalCount": 0,
            "noPredictionDataCount": games - quarantine,
            "missedLockValidPrelockQuarantineCount": quarantine,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "productionAuthorityChanged": False,
        },
    }


def cooperative_completion_receipt(games=15, quarantine=1):
    progress = cooperative_progress(games, quarantine)
    version = (
        "MLB-COOPERATIVE-TERMINAL-CHUNK-"
        "v4-valid-prelock-quarantine"
    )
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": "2026-08-04",
        "reason": "VALID_PRELOCK_MISSED_LOCK_QUARANTINE_RECONCILED",
        "terminalChunkVersion": version,
        "checkpointFingerprint": "a" * 64,
        "manifestFingerprint": "b" * 64,
        "providerManifestFingerprint": "c" * 64,
        "atomicDurableReadSetFingerprint": "d" * 64,
        "verificationPhase": "VERIFY",
        "durableTerminalVerificationComplete": True,
        "atomicDurableProofRequired": True,
        "atomicDurableItemCount": games + 2 * quarantine + 1,
        "completionMutationLeaseRequired": True,
        "perGameLockProgress": dict(progress),
        "missedLockTerminalReconciliation": {
            "ok": True,
            "version": version,
            "slateDateEt": "2026-08-04",
            "manifestGameCount": games,
            "processedGameCount": games,
            "verifiedGameCount": games,
            "verificationIndex": games,
            "durableTerminalVerificationComplete": True,
            "atomicDurableProofRequired": True,
            "atomicDurableItemCount": games + 2 * quarantine + 1,
            "atomicDurableReadSetFingerprint": "d" * 64,
            "completionMutationLeaseRequired": True,
            "reconciledCount": quarantine,
            "missedLockValidPrelockQuarantineCount": quarantine,
            "remainingMissedCount": 0,
            "unresolved": [],
            "progressAfter": dict(progress),
            "postStartPredictionCreationAllowed": False,
            "candidateIntegrityFailuresRelabeled": False,
        },
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "directWorkflowTableWrite": False,
        "productionAuthorityChanged": False,
        "cooperativeReceiptRedacted": True,
        "cooperativeTerminalReplayCompleted": True,
        "cooperativeTerminalReplay": cooperative_public_state(
            "COMPLETED", games, quarantine
        ),
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
    assert result["protectedLockReplayRetryDistinctMinutePhaseCount"] == min(
        subject.PROTECTED_REPLAY_SCHEDULE_PERIOD_SECONDS,
        len(subject.PROTECTED_REPLAY_RETRY_DELAYS_SECONDS),
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
    expected_two_phase_bound = (
        (
            subject.PROTECTED_REPLAY_MAX_MANIFEST_GAMES
            * 2
            * subject.PROTECTED_REPLAY_MAX_EVENTBRIDGE_TICKS_PER_TARGET
            + 1
        )
        * subject.PROTECTED_REPLAY_SCHEDULE_PERIOD_SECONDS
        + subject.PROTECTED_REPLAY_LEASE_SECONDS
        + subject.PROTECTED_REPLAY_SCHEDULING_MARGIN_SECONDS
    )
    assert expected_two_phase_bound == (
        subject.PROTECTED_REPLAY_WORST_CASE_HANDOFF_SECONDS
    )
    assert sum(delays) >= expected_two_phase_bound
    assert 80 * 60 < sum(delays) < 100 * 60
    assert tuple(phases) == subject.PROTECTED_REPLAY_RETRY_PHASES_SECONDS
    assert len(set(phases)) == min(
        subject.PROTECTED_REPLAY_SCHEDULE_PERIOD_SECONDS,
        len(phases),
    )
    assert max(phases.count(phase) for phase in set(phases)) <= 2


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
                "cooperativeTerminalReplay": cooperative_public_state(
                    "ACKNOWLEDGED"
                ),
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
        result = cooperative_completion_receipt()
        # The server can project a completed receipt while the explicit ACK is
        # still requested by this checkout.
        result["cooperativeTerminalReplay"] = cooperative_public_state(
            "COMPLETED"
        )
        return result

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
    assert result["protectedLockReplayCooperativeReceiptVerified"] is True
    completion = result["cooperativeCompletionReceipt"]
    assert completion["verificationIndex"] == 15
    assert completion["canonicalCount"] == 0
    assert completion["noPredictionDataCount"] == 14
    assert completion["missedLockValidPrelockQuarantineCount"] == 1
    assert completion["lockOutcomeCount"] == 15
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
    assert subject.MAX_PROTECTED_REPLAY_ATTEMPTS == 90
    assert len(subject.PROTECTED_REPLAY_RETRY_DELAYS_SECONDS) == 89
    assert 80 * 60 <= subject.PROTECTED_REPLAY_RETRY_HORIZON_SECONDS <= 100 * 60
    assert subject.PROTECTED_REPLAY_MAX_MANIFEST_GAMES == 15
    assert (
        subject.PROTECTED_REPLAY_RETRY_HORIZON_SECONDS
        >= subject.PROTECTED_REPLAY_WORST_CASE_HANDOFF_SECONDS
    )
    assert subject.PROTECTED_REPLAY_RETRY_HORIZON_SECONDS == sum(
        subject.PROTECTED_REPLAY_RETRY_DELAYS_SECONDS
    )


def test_completion_receipt_rejects_missing_or_forged_progress():
    receipt = cooperative_completion_receipt()
    receipt["perGameLockProgress"]["verificationIndex"] = 14
    with pytest.raises(
        base.ReconciliationError,
        match="cooperative_completion_receipt_invalid",
    ):
        subject._validated_safe_cooperative_completion_receipt(
            receipt,
            "2026-08-04",
        )


def test_closed_exact_target_refreshes_receipt_on_every_rerun(monkeypatch):
    row = {
        "slateDateEt": "2026-08-04",
        "manifestGameCount": 15,
        "canonicalPredictionCount": 0,
        "terminalNoPredictionCount": 14,
        "missedLockValidPrelockQuarantineCount": 1,
        "lockOutcomeCount": 15,
    }
    monkeypatch.setattr(
        v4,
        "reconcile",
        lambda *args, **kwargs: {
            "ok": True,
            "slates": [dict(row)],
            "directTableWrite": False,
        },
    )
    calls = []
    monkeypatch.setattr(
        subject,
        "_execute_protected_terminal_replay",
        lambda *args, **kwargs: calls.append(
            kwargs["request"].slate_date
        ) or {
            "slateDateEt": kwargs["request"].slate_date,
            "cooperativeCompletionReceipt": {"verificationIndex": 15},
        },
    )

    for _ in range(2):
        result = subject.reconcile(
            object(),
            object(),
            stack_name="stack",
            max_slate_days=31,
            target_slate_date="2026-08-04",
        )
        assert result[
            "settlementTriggeredProtectedTerminalReplayCount"
        ] == 1

    assert calls == ["2026-08-04", "2026-08-04"]


def test_all_quarantine_receipt_uses_supported_atomic_read_set_range():
    receipt = cooperative_completion_receipt(games=15, quarantine=15)
    safe = subject._validated_safe_cooperative_completion_receipt(
        receipt,
        "2026-08-04",
    )
    assert safe["manifestGameCount"] == 15
    assert safe["missedLockValidPrelockQuarantineCount"] == 15
    assert safe["noPredictionDataCount"] == 0
    assert safe["atomicDurableItemCount"] == 46
    assert safe["atomicDurableItemCount"] <= 100
