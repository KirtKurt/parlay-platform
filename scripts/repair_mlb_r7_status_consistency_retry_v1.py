#!/usr/bin/env python3
"""Install bounded read-only semantic retries for MLB R7 reconciliation.

The repair does not weaken any exact-date, official-game-set, terminal-count,
immutability, promotion, production-authority, or sport-isolation requirement.
It only retries a read-only lock-status request when the immediately preceding
protected write may not yet be visible or the status endpoint reports a bounded
transient unhealthy state. Persistent or structurally invalid evidence remains
fail-closed.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V4 = ROOT / "scripts/reconcile_mlb_prospective_backlog_v4.py"
V5 = ROOT / "scripts/reconcile_mlb_prospective_backlog_v5.py"
TEST_V4 = ROOT / "tests/unit/test_reconcile_mlb_prospective_backlog_v4.py"
TEST_V5 = ROOT / "tests/unit/test_reconcile_mlb_prospective_backlog_v5.py"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_v4() -> None:
    text = V4.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'VERSION = "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v4.1-durable-terminal-replay"\nMAX_INVOKE_ATTEMPTS = 12\nRETRY_DELAYS_SECONDS = (5, 10, 20, 30, 45, 60, 60, 60, 60, 60, 60)\n',
        'VERSION = "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v4.2-status-consistency-retry"\nMAX_INVOKE_ATTEMPTS = 12\nRETRY_DELAYS_SECONDS = (5, 10, 20, 30, 45, 60, 60, 60, 60, 60, 60)\nSTATUS_CONSISTENCY_MAX_ATTEMPTS = 5\nSTATUS_CONSISTENCY_RETRY_DELAYS_SECONDS = (2, 5, 10, 20)\nTRANSIENT_STATUS_ERRORS = frozenset({"official_status_unhealthy"})\nPOST_MUTATION_STATUS_ERRORS = frozenset(\n    {\n        *TRANSIENT_STATUS_ERRORS,\n        "official_schedule_game_count_mismatch",\n        "official_status_terminal_counts_inconsistent",\n        "official_status_terminal_coverage_incomplete",\n        "official_status_not_complete",\n    }\n)\n',
        label="v4 constants",
    )
    text = replace_once(
        text,
        '''def _status_event(slate_date: str) -> Dict[str, Any]:
    return {
        "httpMethod": "GET",
        "path": "/v1/mlb/locks/status",
        "queryStringParameters": {"date": slate_date},
    }


def _incomplete_status_error(exc: base.ReconciliationError) -> bool:
''',
        '''def _status_event(slate_date: str) -> Dict[str, Any]:
    return {
        "httpMethod": "GET",
        "path": "/v1/mlb/locks/status",
        "queryStringParameters": {"date": slate_date},
    }


def _status_consistency_delay_seconds(attempt: int) -> int:
    return STATUS_CONSISTENCY_RETRY_DELAYS_SECONDS[
        min(max(attempt - 1, 0), len(STATUS_CONSISTENCY_RETRY_DELAYS_SECONDS) - 1)
    ]


def read_official_status_with_consistency_retry(
    lambda_client: Any,
    function_name: str,
    slate_date: str,
    *,
    invoke: Any = invoke_json_with_backpressure,
    retryable_errors: frozenset[str] = TRANSIENT_STATUS_ERRORS,
    sleep: Any = time.sleep,
    max_attempts: int = STATUS_CONSISTENCY_MAX_ATTEMPTS,
) -> Dict[str, Any]:
    """Read and validate one exact MLB slate with bounded semantic retries.

    Every attempt is read-only. Exact sport, date, official authority, start
    times, manifest cardinality, terminal arithmetic, and complete coverage are
    revalidated on every response. No invalid response is admitted.
    """

    if max_attempts < 1:
        raise base.ReconciliationError("official_status_max_attempts_invalid")
    event = _status_event(slate_date)
    for attempt in range(1, max_attempts + 1):
        status = invoke(lambda_client, function_name, event)
        try:
            base._validate_official_status(status, slate_date)
        except base.ReconciliationError as exc:
            if str(exc) not in retryable_errors:
                raise
            if attempt >= max_attempts:
                raise base.ReconciliationError(
                    f"official_status_consistency_retry_exhausted:{exc}"
                ) from exc
            sleep(_status_consistency_delay_seconds(attempt))
            continue
        return status
    raise base.ReconciliationError("official_status_retry_state_invalid")


def _incomplete_status_error(exc: base.ReconciliationError) -> bool:
''',
        label="v4 status helper",
    )
    text = replace_once(
        text,
        '''    max_slate_days: int = base.DEFAULT_MAX_SLATE_DAYS,
    invoke: Any = invoke_json_with_backpressure,
) -> Dict[str, Any]:
''',
        '''    max_slate_days: int = base.DEFAULT_MAX_SLATE_DAYS,
    invoke: Any = invoke_json_with_backpressure,
    status_sleep: Any = time.sleep,
) -> Dict[str, Any]:
''',
        label="v4 reconcile signature",
    )
    text = replace_once(
        text,
        '''    for slate_date in slate_dates:
        status_event = _status_event(slate_date)
        official_status = invoke(lambda_client, functions.lock, status_event)
        mutation_payload: Optional[Dict[str, Any]] = None
        mutation_executed = False
        try:
            lock_evidence = _official_evidence(official_status, slate_date)
''',
        '''    for slate_date in slate_dates:
        mutation_payload: Optional[Dict[str, Any]] = None
        mutation_executed = False
        try:
            official_status = read_official_status_with_consistency_retry(
                lambda_client,
                functions.lock,
                slate_date,
                invoke=invoke,
                retryable_errors=TRANSIENT_STATUS_ERRORS,
                sleep=status_sleep,
            )
            lock_evidence = _official_evidence(official_status, slate_date)
''',
        label="v4 status-first read",
    )
    text = replace_once(
        text,
        '''            official_status = invoke(lambda_client, functions.lock, status_event)
            lock_evidence = v3.validate_lock_result(
''',
        '''            official_status = read_official_status_with_consistency_retry(
                lambda_client,
                functions.lock,
                slate_date,
                invoke=invoke,
                retryable_errors=POST_MUTATION_STATUS_ERRORS,
                sleep=status_sleep,
            )
            lock_evidence = v3.validate_lock_result(
''',
        label="v4 post-mutation readback",
    )
    text = replace_once(
        text,
        '''                "readOnlyOfficialStatusProof": True,
                "backpressureRetryInstalled": True,
                "directTableWrite": False,
''',
        '''                "readOnlyOfficialStatusProof": True,
                "backpressureRetryInstalled": True,
                "semanticStatusConsistencyRetryInstalled": True,
                "directTableWrite": False,
''',
        label="v4 row evidence",
    )
    text = replace_once(
        text,
        '''        "readOnlyOfficialStatusProof": True,
        "backpressureRetryInstalled": True,
        "directTableWrite": False,
''',
        '''        "readOnlyOfficialStatusProof": True,
        "backpressureRetryInstalled": True,
        "semanticStatusConsistencyRetryInstalled": True,
        "directTableWrite": False,
''',
        label="v4 report evidence",
    )
    V4.write_text(text, encoding="utf-8")


def patch_v5() -> None:
    text = V5.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''VERSION = (
    "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v5.3-"
    "full-bearer-redacted-function-error-evidence"
)
''',
        '''VERSION = (
    "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v5.4-"
    "status-consistency-retry-and-redacted-function-error-evidence"
)
''',
        label="v5 version",
    )
    text = replace_once(
        text,
        '''        status = v4.invoke_json_with_backpressure(
            lambda_client,
            functions.lock,
            {
                "httpMethod": "GET",
                "path": STATUS_PATH,
                "queryStringParameters": {"date": request.slate_date},
            },
        )
''',
        '''        status = v4.read_official_status_with_consistency_retry(
            lambda_client,
            functions.lock,
            request.slate_date,
            invoke=v4.invoke_json_with_backpressure,
            retryable_errors=v4.POST_MUTATION_STATUS_ERRORS,
        )
''',
        label="v5 protected replay readback",
    )
    text = replace_once(
        text,
        '''    value["readOnlyNonSuccessStatusBodiesPreserved"] = True
    value["mutatingNonSuccessStatusesStillFailClosed"] = True
''',
        '''    value["readOnlyNonSuccessStatusBodiesPreserved"] = True
    value["semanticStatusConsistencyRetryInstalled"] = True
    value["mutatingNonSuccessStatusesStillFailClosed"] = True
''',
        label="v5 report evidence",
    )
    V5.write_text(text, encoding="utf-8")


def patch_test_v4() -> None:
    text = TEST_V4.read_text(encoding="utf-8")
    insertion = '''

def test_transient_unhealthy_status_is_retried_read_only_without_mutation():
    calls = []
    sleeps = []
    statuses = [
        {"ok": False, "sport": "mlb", "slateDateEt": "2026-08-03"},
        official_status("2026-08-03"),
    ]

    def invoke(client, function, event):
        del client, function
        calls.append(event)
        if event.get("httpMethod") == "GET":
            return statuses.pop(0)
        if event.get("run") == "prospective_backlog_settlement_v4":
            return settlement("2026-08-03")
        raise AssertionError(event)

    result = subject.reconcile(
        FakeCloudFormation(),
        FakeLambda(),
        stack_name="stack",
        now_utc=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
        invoke=invoke,
        status_sleep=sleeps.append,
    )

    assert result["reconciledSlateCount"] == 1
    assert result["semanticStatusConsistencyRetryInstalled"] is True
    assert [event.get("httpMethod") for event in calls].count("GET") == 2
    assert not any(event.get("force") is True for event in calls)
    assert sleeps == [2]


def test_post_mutation_status_readback_retries_until_exact_counts_converge():
    calls = []
    sleeps = []
    statuses = [
        official_status("2026-08-03", games=12, canonical=8, terminal=3),
        {"ok": False, "sport": "mlb", "slateDateEt": "2026-08-03"},
        official_status("2026-08-03", games=12, canonical=8, terminal=4),
    ]

    def invoke(client, function, event):
        del client, function
        calls.append(event)
        if event.get("httpMethod") == "GET":
            return statuses.pop(0)
        if event.get("force") is True:
            return mutation("2026-08-03")
        if event.get("run") == "prospective_backlog_settlement_v4":
            return settlement("2026-08-03")
        raise AssertionError(event)

    result = subject.reconcile(
        FakeCloudFormation(),
        FakeLambda(),
        stack_name="stack",
        now_utc=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
        invoke=invoke,
        status_sleep=sleeps.append,
    )

    row = result["slates"][0]
    assert row["protectedLockReplay"] is True
    assert row["manifestGameCount"] == 12
    assert row["semanticStatusConsistencyRetryInstalled"] is True
    assert [event.get("httpMethod") for event in calls].count("GET") == 3
    assert [event.get("force") for event in calls].count(True) == 1
    assert sleeps == [2]
'''
    text = replace_once(
        text,
        '\n\ndef test_schedule_authority_failure_does_not_trigger_mutation():\n',
        insertion + '\n\ndef test_schedule_authority_failure_does_not_trigger_mutation():\n',
        label="v4 retry tests",
    )
    text = replace_once(
        text,
        '''    assert "postStartPredictionCreationAllowed" in source
''',
        '''    assert "postStartPredictionCreationAllowed" in source
    assert "semanticStatusConsistencyRetryInstalled" in source
    assert "official_status_consistency_retry_exhausted" in source
''',
        label="v4 source assertions",
    )
    TEST_V4.write_text(text, encoding="utf-8")


def patch_test_v5() -> None:
    text = TEST_V5.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''def test_unhealthy_status_body_does_not_trigger_protected_mutation(monkeypatch):
    calls = []

    def fake_invoke(client, function, event):
        del client, function
        calls.append(event)
        return {"ok": False, "sport": "mlb", "slateDateEt": "2026-08-03"}

    monkeypatch.setattr(base, "invoke_json", fake_invoke)
    with pytest.raises(base.ReconciliationError, match="official_status_unhealthy"):
        v4.reconcile(
            FakeCloudFormation(),
            FakeLambda(),
            stack_name="stack",
            now_utc=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
            invoke=fake_invoke,
        )
    assert len(calls) == 1
    assert calls[0]["httpMethod"] == "GET"
''',
        '''def test_unhealthy_status_body_does_not_trigger_protected_mutation(monkeypatch):
    calls = []
    sleeps = []

    def fake_invoke(client, function, event):
        del client, function
        calls.append(event)
        return {"ok": False, "sport": "mlb", "slateDateEt": "2026-08-03"}

    monkeypatch.setattr(base, "invoke_json", fake_invoke)
    with pytest.raises(
        base.ReconciliationError,
        match="official_status_consistency_retry_exhausted:official_status_unhealthy",
    ):
        v4.reconcile(
            FakeCloudFormation(),
            FakeLambda(),
            stack_name="stack",
            now_utc=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
            invoke=fake_invoke,
            status_sleep=sleeps.append,
        )
    assert len(calls) == v4.STATUS_CONSISTENCY_MAX_ATTEMPTS
    assert all(call["httpMethod"] == "GET" for call in calls)
    assert not any(call.get("force") is True for call in calls)
    assert sleeps == list(v4.STATUS_CONSISTENCY_RETRY_DELAYS_SECONDS)
''',
        label="v5 persistent unhealthy test",
    )
    text = replace_once(
        text,
        '''    assert result["readOnlyNonSuccessStatusBodiesPreserved"] is True
    assert result["mutatingNonSuccessStatusesStillFailClosed"] is True
''',
        '''    assert result["readOnlyNonSuccessStatusBodiesPreserved"] is True
    assert result["semanticStatusConsistencyRetryInstalled"] is True
    assert result["mutatingNonSuccessStatusesStillFailClosed"] is True
''',
        label="v5 retry flag assertion",
    )
    text = replace_once(
        text,
        '''    assert "lambdaFunctionErrorsRedacted" in source
''',
        '''    assert "lambdaFunctionErrorsRedacted" in source
    assert "read_official_status_with_consistency_retry" in source
    assert "semanticStatusConsistencyRetryInstalled" in source
''',
        label="v5 source assertions",
    )
    TEST_V5.write_text(text, encoding="utf-8")


def verify() -> None:
    v4 = V4.read_text(encoding="utf-8")
    v5 = V5.read_text(encoding="utf-8")
    assert "STATUS_CONSISTENCY_MAX_ATTEMPTS = 5" in v4
    assert 'TRANSIENT_STATUS_ERRORS = frozenset({"official_status_unhealthy"})' in v4
    assert "read_official_status_with_consistency_retry" in v4
    assert "retryable_errors=POST_MUTATION_STATUS_ERRORS" in v4
    assert "semanticStatusConsistencyRetryInstalled" in v4
    assert "read_official_status_with_consistency_retry" in v5
    assert "semanticStatusConsistencyRetryInstalled" in v5
    for text in (v4, v5):
        for forbidden in (
            "put_item(",
            "update_item(",
            "delete_item(",
            "productionAuthorityChanged = True",
            "liveInferenceAuthority = True",
            "postStartPredictionCreationAllowed = True",
        ):
            assert forbidden not in text, forbidden


def main() -> int:
    patch_v4()
    patch_v5()
    patch_test_v4()
    patch_test_v5()
    verify()
    print("Installed bounded read-only MLB R7 semantic status retries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
