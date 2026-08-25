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

    def invoke(self, *, FunctionName, InvocationType, Payload):
        del FunctionName, Payload
        assert InvocationType == "RequestResponse"
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
