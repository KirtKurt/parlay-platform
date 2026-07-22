from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest
from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[2]
HANDLER = ROOT / "hello_world" / "mlb_daily_pick_lock_protected.py"
RUNTIME_VERSION = "MLB-ML-RUNTIME-INSTALL-v3-test"
REQUIRED_RUNTIME_STEPS = {
    "accuracyTargetsSeparated",
    "legacyReliabilityOverlaySafety",
    "sourceHonestFundamentals",
    "sourceHonestFundamentalsV2",
    "legacyV1ChampionRuntimeInstalledForShadowDiagnostics",
    "legacyV1AuthorityDisabled",
    "v2ShadowManualFirst",
    "officialSemanticsFinalized",
    "exactCleanCohortVectorPatch",
    "officialFreezeBridge",
    "immutableFeatureFreeze",
    "immutableLockedStorageAuthority",
    "canonicalLockedStorageFinalizer",
    "lastPrelockPromotionAuthority",
    "canonicalProbabilityAndPersistedPrelockAuthority",
    "providerNeutralCalibrationAndActionability",
    "legacyFinalGateDisabled",
}
STUB_MODULE_NAMES = (
    "mlb_ml_runtime_install_v3",
    "mlb_daily_pick_lock",
    "mlb_daily_lock_coverage_patch",
    "mlb_daily_lock_ml_vector_preservation_patch",
    "mlb_daily_per_game_lock_patch",
)


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}},
        operation,
    )


class FakeLeaseTable:
    CONDITION = (
        "attribute_not_exists(PK) OR "
        "attribute_not_exists(lease_expires_at_epoch) OR "
        "lease_expires_at_epoch <= :now"
    )
    RELEASE_CONDITION = (
        "lease_owner = :owner AND record_type = :record_type AND "
        "lease_version = :lease_version"
    )

    def __init__(self) -> None:
        self.item = None
        self.put_calls = []
        self.delete_calls = []
        self.get_calls = []
        self.put_error = None
        self.put_commits_then_error = None
        self.delete_error = None
        self.get_error = None

    def put_item(self, **kwargs):
        assert kwargs["ConditionExpression"] == self.CONDITION
        self.put_calls.append(copy.deepcopy(kwargs))
        now_epoch = int(kwargs["ExpressionAttributeValues"][":now"])
        existing_expiry = (
            self.item.get("lease_expires_at_epoch")
            if isinstance(self.item, dict)
            else None
        )
        may_replace = (
            self.item is None
            or existing_expiry is None
            or int(existing_expiry) <= now_epoch
        )
        if not may_replace:
            raise _client_error(
                "ConditionalCheckFailedException",
                "PutItem",
            )
        if self.put_error is not None:
            raise self.put_error
        self.item = copy.deepcopy(kwargs["Item"])
        if self.put_commits_then_error is not None:
            raise self.put_commits_then_error
        return {}

    def delete_item(self, **kwargs):
        assert kwargs["ConditionExpression"] == self.RELEASE_CONDITION
        self.delete_calls.append(copy.deepcopy(kwargs))
        if self.delete_error is not None:
            raise self.delete_error
        values = kwargs["ExpressionAttributeValues"]
        owned = bool(
            isinstance(self.item, dict)
            and self.item.get("lease_owner") == values[":owner"]
            and self.item.get("record_type") == values[":record_type"]
            and self.item.get("lease_version") == values[":lease_version"]
        )
        if not owned:
            raise _client_error(
                "ConditionalCheckFailedException",
                "DeleteItem",
            )
        self.item = None
        return {}

    def get_item(self, **kwargs):
        self.get_calls.append(copy.deepcopy(kwargs))
        assert kwargs["ConsistentRead"] is True
        if self.get_error is not None:
            raise self.get_error
        return {"Item": copy.deepcopy(self.item)} if self.item else {}


class FakeContext:
    def __init__(
        self,
        request_id: str = "request-1",
        remaining_millis: int = 300_000,
    ) -> None:
        self.aws_request_id = request_id
        self.remaining_millis = remaining_millis

    def get_remaining_time_in_millis(self) -> int:
        return self.remaining_millis


def _scheduled_event() -> dict:
    return {
        "sport": "mlb",
        "run": "daily_lock_check",
        "auto_ingest": False,
        "date": "2026-07-21",
    }


def _manual_event(*, token: str = "test-admin-token") -> dict:
    return {
        "httpMethod": "POST",
        "requestContext": {"stage": "test"},
        "headers": {"x-inqsi-admin-token": token},
        "body": json.dumps({"date": "2026-07-21"}),
    }


def _runtime_status() -> dict:
    return {
        "applied": True,
        "ok": True,
        "version": RUNTIME_VERSION,
        "steps": {name: True for name in REQUIRED_RUNTIME_STEPS},
        "errors": [],
    }


@contextmanager
def _load_handler(
    *,
    runtime_status=None,
    runtime_error=None,
    delegate_payload=None,
    delegate_error=None,
    lease_table=None,
    lease_seconds=360,
    installed_diagnostics_version="test-diagnostics-version",
    writer_payload_fingerprint_version="test-ddb-canonical-fingerprint-version",
):
    events = []
    delegate_calls = []

    runtime = ModuleType("mlb_ml_runtime_install_v3")
    runtime.VERSION = RUNTIME_VERSION

    def install():
        events.append("runtime_install")
        if runtime_error is not None:
            raise runtime_error
        return copy.deepcopy(runtime_status if runtime_status is not None else _runtime_status())

    runtime.install = install

    daily_lock = ModuleType("mlb_daily_pick_lock")
    daily_lock.mlb_game_winner_engine = ModuleType("mlb_game_winner_engine")
    daily_lock.mlb_game_winner_engine.PAYLOAD_FINGERPRINT_VERSION = (
        writer_payload_fingerprint_version
    )
    daily_lock.history = ModuleType("inqsi_pull_history")
    daily_lock.history.CANONICAL_PAYLOAD_FINGERPRINT_VERSION = (
        "test-ddb-canonical-fingerprint-version"
    )
    daily_lock.history.canonical_payload_fingerprint = lambda value: str(value)
    daily_lock.history.verified_full_slate_manifest = lambda pulls, slate: {}
    daily_lock.TABLE = lease_table if lease_table is not None else FakeLeaseTable()

    def payload(event):
        result = {}
        if not event.get("httpMethod") and not event.get("requestContext"):
            result.update(event)
        params = event.get("queryStringParameters") or {}
        if isinstance(params, dict):
            result.update(params)
        body = event.get("body")
        if body:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                result.update(parsed)
        return result

    daily_lock._payload = payload
    daily_lock._today_et = lambda: "2026-07-21"

    def delegate(event, context):
        delegate_calls.append((event, context))
        if delegate_error is not None:
            raise delegate_error
        return {
            "statusCode": 200 if (delegate_payload or {}).get("ok", True) else 500,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(delegate_payload or {"ok": True, "delegated": True}),
        }

    daily_lock.lambda_handler = delegate

    coverage = ModuleType("mlb_daily_lock_coverage_patch")

    def apply_coverage(module):
        assert module is daily_lock
        events.append("coverage_patch")

    coverage.apply = apply_coverage

    preservation = ModuleType("mlb_daily_lock_ml_vector_preservation_patch")

    def apply_preservation(module):
        assert module is daily_lock
        events.append("vector_preservation_patch")
        return {
            "ok": True,
            "failClosed": True,
            "expectedVectorVersion": "test-vector-version",
            "selectionLockIndependentOfTrainingVector": True,
        }

    preservation.apply = apply_preservation

    per_game = ModuleType("mlb_daily_per_game_lock_patch")
    per_game.ATTEMPT_DIAGNOSTICS_VERSION = "test-diagnostics-version"
    per_game.PROMOTION_POLICY_VERSION = "test-promotion-version"
    per_game.PAYLOAD_FINGERPRINT_VERSION = "test-ddb-canonical-fingerprint-version"
    per_game.OFFICIAL_SCHEDULE_AUTHORITY_VERSION = (
        "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date"
    )
    per_game.READINESS_VERSION = "test-readiness-version"
    per_game.LOCK_OUTCOME_VERSION = "test-lock-outcome-version"
    per_game.RELEASE_ASSESSMENT_VERSION = "test-playability-version"
    per_game.LOCK_EXECUTION_LEASE_VERSION = (
        "MLB-LOCK-EXECUTION-LEASE-v2-global-all-mutating"
    )

    def apply_per_game(module):
        assert module is daily_lock
        events.append("per_game_patch")
        module._INQSI_MLB_DAILY_PER_GAME_LOCK_V1 = True
        module.MLB_DAILY_PER_GAME_LOCK_VERSION = "test-per-game-lock-version"
        module.MLB_PER_GAME_LOCK_ATTEMPT_DIAGNOSTICS_VERSION = installed_diagnostics_version
        module.MLB_LAST_PRELOCK_PROMOTION_VERSION = "test-promotion-version"
        module.MLB_LOCK_READINESS_VERSION = "test-readiness-version"
        module.MLB_LOCK_OUTCOME_VERSION = "test-lock-outcome-version"
        module.MLB_PLAYABILITY_ASSESSMENT_VERSION = "test-playability-version"
        module.MLB_LOCK_SOURCE_WINDOW_STABILIZATION_SECONDS = 0
        module.MLB_LOCK_EXECUTION_LEASE_VERSION = (
            per_game.LOCK_EXECUTION_LEASE_VERSION
        )
        module.MLB_LOCK_EXECUTION_LEASE_SECONDS = 360
        module.MLB_LOCK_EXECUTION_LEASE_SCOPE = (
            "global_all_mutating_lock_invocations"
        )
        module.MLB_LOCK_EXECUTION_LEGACY_ROLLOUT_BRIDGE = True
        module.LOCK_POLICY = "test-policy"

    per_game.apply = apply_per_game

    stubs = {
        "mlb_ml_runtime_install_v3": runtime,
        "mlb_daily_pick_lock": daily_lock,
        "mlb_daily_lock_coverage_patch": coverage,
        "mlb_daily_lock_ml_vector_preservation_patch": preservation,
        "mlb_daily_per_game_lock_patch": per_game,
    }
    previous = {name: sys.modules.get(name) for name in STUB_MODULE_NAMES}
    module_name = f"_test_mlb_daily_pick_lock_protected_{uuid.uuid4().hex}"
    previous_lease_seconds = os.environ.get("MLB_LOCK_EXECUTION_LEASE_SECONDS")
    try:
        os.environ["MLB_LOCK_EXECUTION_LEASE_SECONDS"] = str(lease_seconds)
        sys.modules.update(stubs)
        spec = importlib.util.spec_from_file_location(module_name, HANDLER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module, events, delegate_calls
    finally:
        if previous_lease_seconds is None:
            os.environ.pop("MLB_LOCK_EXECUTION_LEASE_SECONDS", None)
        else:
            os.environ["MLB_LOCK_EXECUTION_LEASE_SECONDS"] = (
                previous_lease_seconds
            )
        sys.modules.pop(module_name, None)
        for name, original in previous.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _body(response):
    return json.loads(response["body"])


def _assert_runtime_error(callable_, expected_text):
    try:
        callable_()
    except RuntimeError as exc:
        assert expected_text in str(exc)
        return
    raise AssertionError("expected RuntimeError")


def test_installs_exact_runtime_before_lock_patches_and_delegates():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, events, delegate_calls):
        assert events == [
            "runtime_install",
            "coverage_patch",
            "vector_preservation_patch",
            "per_game_patch",
        ]

        response = handler.lambda_handler(_scheduled_event(), FakeContext())
        payload = _body(response)

        assert response["statusCode"] == 200
        assert payload["delegated"] is True
        assert payload["mlRuntimeInstallation"]["ok"] is True
        assert payload["mlRuntimeInstallation"]["version"] == RUNTIME_VERSION
        assert payload["mlRuntimeInstallation"]["steps"] == {
            name: True for name in REQUIRED_RUNTIME_STEPS
        }
        assert payload["perGameLockInstallation"]["fixVersion"] == (
            "MLB-LOCK-RUNTIME-FIX-v5-official-schedule-lifecycle-vector-separation"
        )
        assert payload["perGameLockInstallation"]["lastPrelockAtCutoffBecomesFinal"] is True
        assert payload["perGameLockInstallation"]["modelOrSignalRecomputedAtLock"] is False
        assert payload["perGameLockInstallation"]["explicitMlRuntimeInstall"] is True
        assert payload["perGameLockInstallation"]["durableAttemptDiagnostics"] is True
        assert payload["perGameLockInstallation"]["candidatePayloadFingerprintVersion"] == (
            "test-ddb-canonical-fingerprint-version"
        )
        assert payload["perGameLockInstallation"]["writerPayloadFingerprintVersion"] == (
            "test-ddb-canonical-fingerprint-version"
        )
        assert payload["perGameLockInstallation"]["historyPayloadFingerprintVersion"] == (
            "test-ddb-canonical-fingerprint-version"
        )
        assert payload["perGameLockInstallation"]["candidatePayloadFingerprintDdbReadCanonical"] is True
        assert payload["perGameLockInstallation"]["readinessCheckpointsAtTMinus60AndTMinus50"] is True
        assert payload["perGameLockInstallation"]["lockOutcomeStatusSeparateFromPrediction"] is True
        assert payload["perGameLockInstallation"]["latePlayabilityAssessmentCannotRewriteSelection"] is True
        assert payload["perGameLockInstallation"]["sourceWindowStabilizationSeconds"] == 0
        assert payload["perGameLockInstallation"]["officialScheduleAuthorityRequired"] is True
        assert payload["perGameLockInstallation"]["officialScheduleAuthorityVersion"] == (
            "MLB-OFFICIAL-SCHEDULE-AUTHORITY-v1-statsapi-exact-date"
        )
        assert payload["perGameLockInstallation"]["selectionLockIndependentOfTrainingVector"] is True
        assert payload["perGameLockInstallation"]["lockExecutionConcurrency"] == {
            "version": "MLB-LOCK-EXECUTION-LEASE-v1",
            "strategy": "dynamodb_conditional_lease",
            "scope": "global_mlb_lock_execution",
            "sharedLeaseKey": True,
            "leaseSeconds": 360,
            "requiredLeaseSeconds": 360,
            "lambdaTimeoutSeconds": 300,
            "timeoutSafetyMarginSeconds": 60,
            "expiredLeaseReclaim": True,
            "ownerConditionalRelease": True,
            "reservedLambdaConcurrencyRequired": False,
        }
        assert payload["lockExecutionConcurrency"]["executionMode"] == "scheduled"
        assert payload["lockExecutionConcurrency"]["leaseAcquired"] is True
        assert payload["lockExecutionConcurrency"]["leaseReleased"] is True
        assert payload["lockExecutionConcurrency"]["overlapSkipped"] is False
        assert len(delegate_calls) == 1
        assert len(table.put_calls) == 1
        assert len(table.delete_calls) == 1
        assert table.item is None


def test_missing_required_runtime_step_fails_closed_without_delegating():
    status = _runtime_status()
    status["steps"]["officialFreezeBridge"] = False

    with _load_handler(runtime_status=status) as (handler, _, delegate_calls):
        response = handler.lambda_handler({"httpMethod": "POST", "requestContext": {"stage": "test"}}, None)
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_ML_LOCK_RUNTIME_NOT_READY"
        assert payload["status"]["ok"] is False
        assert payload["status"]["missingRequiredSteps"] == ["officialFreezeBridge"]
        assert delegate_calls == []


def test_runtime_installer_exception_fails_closed_and_remains_observable():
    with _load_handler(runtime_error=RuntimeError("injected install failure")) as (
        handler,
        _,
        delegate_calls,
    ):
        response = handler.lambda_handler({"httpMethod": "POST", "requestContext": {"stage": "test"}}, None)
        payload = _body(response)
        options = _body(handler.lambda_handler({"httpMethod": "OPTIONS"}, None))

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_ML_LOCK_RUNTIME_NOT_READY"
        assert payload["status"]["errors"] == ["injected install failure"]
        assert options["mlRuntimeInstallation"]["ok"] is False
        assert delegate_calls == []


def test_runtime_version_mismatch_fails_closed_without_delegating():
    status = _runtime_status()
    status["version"] = "unexpected-runtime-version"

    with _load_handler(runtime_status=status) as (handler, _, delegate_calls):
        response = handler.lambda_handler({"httpMethod": "POST", "requestContext": {"stage": "test"}}, None)
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["status"]["ok"] is False
        assert payload["status"]["expectedVersion"] == RUNTIME_VERSION
        assert delegate_calls == []


def test_scheduled_prerequisite_failure_raises_for_eventbridge_visibility():
    status = _runtime_status()
    status["steps"]["officialFreezeBridge"] = False

    with _load_handler(runtime_status=status) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler({"sport": "mlb", "run": "daily_lock_check"}, None),
            "MLB_SCHEDULED_LOCK_PREREQUISITE_FAILED",
        )
        assert delegate_calls == []


def test_scheduled_delegate_lock_failure_raises_after_diagnostics_return():
    payload = {
        "ok": False,
        "reason": "PER_GAME_LOCK_DUE_BUT_NOT_CANONICAL",
        "perGameLockAttemptDiagnostics": {"attemptedGameCount": 1},
    }
    with _load_handler(delegate_payload=payload) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler({"sport": "mlb", "run": "daily_lock_check"}, None),
            "MLB_SCHEDULED_LOCK_FAILED",
        )
        assert len(delegate_calls) == 1


def test_stale_attempt_diagnostics_version_fails_closed():
    with _load_handler(installed_diagnostics_version="stale-diagnostics-version") as (
        handler,
        _,
        delegate_calls,
    ):
        response = handler.lambda_handler(
            {"httpMethod": "POST", "requestContext": {"stage": "test"}},
            None,
        )
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_DAILY_PER_GAME_LOCK_NOT_INSTALLED"
        assert payload["status"]["durableAttemptDiagnostics"] is False
        assert payload["status"]["expectedAttemptDiagnosticsVersion"] == "test-diagnostics-version"
        assert delegate_calls == []


def test_writer_fingerprint_contract_mismatch_fails_closed():
    with _load_handler(writer_payload_fingerprint_version="stale-writer-fingerprint") as (
        handler,
        _,
        delegate_calls,
    ):
        response = handler.lambda_handler(
            {"httpMethod": "POST", "requestContext": {"stage": "test"}},
            None,
        )
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_DAILY_PER_GAME_LOCK_NOT_INSTALLED"
        assert payload["status"]["candidatePayloadFingerprintDdbReadCanonical"] is False
        assert payload["status"]["writerPayloadFingerprintVersion"] == (
            "stale-writer-fingerprint"
        )
        assert delegate_calls == []


def test_active_slow_run_causes_five_fresh_minute_ticks_to_skip_without_owner_leak():
    table = FakeLeaseTable()
    clock = {"now": datetime(2026, 7, 21, 22, 0, tzinfo=timezone.utc)}
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler._utc_now = lambda: clock["now"]
        handler._acquire_execution_lease(
            mode="scheduled",
            slate_date_et="2026-07-21",
            owner="slow-owner-must-not-leak",
        )

        for minute in range(1, 6):
            clock["now"] = datetime(
                2026, 7, 21, 22, minute, tzinfo=timezone.utc
            )
            response = handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id=f"tick-{minute}"),
            )
            payload = _body(response)

            assert response["statusCode"] == 200
            assert payload["status"] == "SKIPPED_OVERLAPPING_LOCK_EXECUTION"
            assert payload["skipped"] is True
            assert payload["mutatingRunAttempted"] is False
            assert payload["nextFreshScheduleIsRetry"] is True
            control = payload["lockExecutionConcurrency"]
            assert control["overlapSkipped"] is True
            assert control["activeLease"]["active"] is True
            assert control["activeLease"]["ownerPresent"] is True
            assert "slow-owner-must-not-leak" not in json.dumps(payload)

        assert delegate_calls == []
        assert len(table.put_calls) == 6
        assert len(table.delete_calls) == 0


def test_expired_execution_lease_is_reclaimed_and_released():
    table = FakeLeaseTable()
    now = datetime(2026, 7, 21, 22, 10, tzinfo=timezone.utc)
    table.item = {
        "PK": "MLB_LOCK_EXECUTION#V1",
        "SK": "LEASE",
        "record_type": "mlb_lock_execution_lease_v1",
        "lease_version": "MLB-LOCK-EXECUTION-LEASE-v1",
        "lease_owner": "expired-owner",
        "lease_expires_at_epoch": int(now.timestamp()) - 1,
    }
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler._utc_now = lambda: now

        response = handler.lambda_handler(
            _scheduled_event(),
            FakeContext(request_id="replacement-owner"),
        )

        assert response["statusCode"] == 200
        assert len(delegate_calls) == 1
        assert table.put_calls[0]["Item"]["lease_owner"] == (
            "scheduled:replacement-owner"
        )
        assert len(table.delete_calls) == 1
        assert table.item is None


def test_only_current_owner_can_release_or_delete_reclaimed_lease():
    table = FakeLeaseTable()
    clock = {"now": datetime(2026, 7, 21, 22, 0, tzinfo=timezone.utc)}
    with _load_handler(lease_table=table) as (handler, _, _):
        handler._utc_now = lambda: clock["now"]
        handler._acquire_execution_lease(
            mode="scheduled",
            slate_date_et="2026-07-21",
            owner="old-owner",
        )

        with pytest.raises(handler.LockExecutionLeaseOwnershipConflict):
            handler._release_execution_lease("wrong-owner")
        assert table.item["lease_owner"] == "old-owner"

        clock["now"] += timedelta(seconds=361)
        handler._acquire_execution_lease(
            mode="manual",
            slate_date_et="2026-07-21",
            owner="replacement-owner",
        )
        with pytest.raises(handler.LockExecutionLeaseOwnershipConflict):
            handler._release_execution_lease("old-owner")
        assert table.item["lease_owner"] == "replacement-owner"

        handler._release_execution_lease("replacement-owner")
        assert table.item is None


def test_authenticated_manual_and_scheduled_runs_share_one_lease_after_auth():
    table = FakeLeaseTable()
    now = datetime(2026, 7, 21, 22, 0, tzinfo=timezone.utc)
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler._utc_now = lambda: now
        handler.ADMIN_TOKEN = "test-admin-token"
        handler._acquire_execution_lease(
            mode="scheduled",
            slate_date_et="2026-07-21",
            owner="scheduled-owner",
        )
        put_count = len(table.put_calls)

        unauthorized = handler.lambda_handler(
            _manual_event(token="wrong-token"),
            FakeContext(request_id="unauthorized-manual"),
        )
        assert unauthorized["statusCode"] == 401
        assert len(table.put_calls) == put_count

        response = handler.lambda_handler(
            _manual_event(),
            FakeContext(request_id="authenticated-manual"),
        )
        payload = _body(response)

        assert response["statusCode"] == 409
        assert payload["ok"] is False
        assert payload["error"] == "MLB_LOCK_EXECUTION_ALREADY_RUNNING"
        assert payload["skipped"] is True
        assert payload["retryable"] is True
        assert payload["mutatingRunAttempted"] is False
        assert payload["lockExecutionConcurrency"]["executionMode"] == "manual"
        assert payload["lockExecutionConcurrency"]["overlapSkipped"] is True
        assert (
            payload["lockExecutionConcurrency"]["nextFreshScheduleIsRetry"]
            is False
        )
        assert "scheduled-owner" not in json.dumps(payload)
        assert delegate_calls == []
        assert len(table.put_calls) == put_count + 1
        handler._release_execution_lease("scheduled-owner")


def test_http_api_v2_methods_are_normalized_before_auth_lease_and_delegate():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler.ADMIN_TOKEN = "test-admin-token"

        unauthorized = handler.lambda_handler(
            {
                "requestContext": {"http": {"method": "POST"}},
                "headers": {"x-inqsi-admin-token": "wrong-token"},
                "body": json.dumps({"date": "2026-07-21"}),
            },
            FakeContext(request_id="v2-unauthorized"),
        )
        get_response = handler.lambda_handler(
            {
                "requestContext": {"http": {"method": "GET"}},
                "rawPath": "/v1/mlb/locks/status",
                "queryStringParameters": {"date": "2026-07-21"},
            },
            FakeContext(request_id="v2-get"),
        )
        options_response = handler.lambda_handler(
            {"requestContext": {"http": {"method": "OPTIONS"}}},
            FakeContext(request_id="v2-options"),
        )

        assert unauthorized["statusCode"] == 401
        assert get_response["statusCode"] == 200
        assert options_response["statusCode"] == 200
        assert len(delegate_calls) == 1
        assert delegate_calls[0][0]["httpMethod"] == "GET"
        assert table.put_calls == []
        assert table.delete_calls == []

        post_response = handler.lambda_handler(
            {
                "requestContext": {"http": {"method": "POST"}},
                "headers": {"authorization": "Bearer test-admin-token"},
                "body": json.dumps({"date": "2026-07-21"}),
            },
            FakeContext(request_id="v2-authorized"),
        )

        assert post_response["statusCode"] == 200
        assert len(delegate_calls) == 2
        assert delegate_calls[1][0]["httpMethod"] == "POST"
        assert len(table.put_calls) == 1
        assert len(table.delete_calls) == 1


@pytest.mark.parametrize(
    "event",
    (
        {"requestContext": {"stage": "test"}},
        {
            "httpMethod": "GET",
            "requestContext": {"http": {"method": "POST"}},
        },
    ),
)
def test_malformed_or_conflicting_http_method_fails_closed_without_writes(event):
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        response = handler.lambda_handler(
            event,
            FakeContext(request_id="invalid-http-method"),
        )

        assert response["statusCode"] == 400
        assert _body(response)["error"] == "MLB_LOCK_HTTP_METHOD_INVALID"
        assert delegate_calls == []
        assert table.put_calls == []
        assert table.delete_calls == []


@pytest.mark.parametrize("method", ("PUT", "PATCH", "DELETE"))
@pytest.mark.parametrize("event_version", ("v1", "v2"))
def test_unsupported_authenticated_http_methods_never_delegate_or_lease(
    method, event_version
):
    table = FakeLeaseTable()
    event = {
        "headers": {"authorization": "Bearer test-admin-token"},
        "body": json.dumps({"date": "2026-07-21"}),
    }
    if event_version == "v1":
        event.update(
            {
                "httpMethod": method,
                "requestContext": {"stage": "test"},
            }
        )
    else:
        event["requestContext"] = {"http": {"method": method}}

    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler.ADMIN_TOKEN = "test-admin-token"

        response = handler.lambda_handler(
            event,
            FakeContext(request_id=f"unsupported-{event_version}-{method}"),
        )
        payload = _body(response)

        assert response["statusCode"] == 405
        assert payload["error"] == "MLB_LOCK_HTTP_METHOD_NOT_ALLOWED"
        assert payload["method"] == method
        assert delegate_calls == []
        assert table.put_calls == []
        assert table.delete_calls == []
        assert table.get_calls == []


def test_ambiguous_acquire_commit_then_error_never_runs_unlocked():
    table = FakeLeaseTable()
    table.put_commits_then_error = _client_error(
        "InternalServerError",
        "PutItem",
    )
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="ambiguous-acquire"),
            ),
            "MLB_SCHEDULED_LOCK_PREREQUISITE_FAILED",
        )

        assert delegate_calls == []
        assert table.item is not None
        assert table.item["lease_owner"] == "scheduled:ambiguous-acquire"
        assert table.delete_calls == []


def test_nonconditional_acquire_failure_never_delegates():
    table = FakeLeaseTable()
    table.put_error = _client_error("InternalServerError", "PutItem")
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="failed-acquire"),
            ),
            "MLB_SCHEDULED_LOCK_PREREQUISITE_FAILED",
        )

        assert delegate_calls == []
        assert table.item is None
        assert table.delete_calls == []


def test_delegate_exception_releases_lease_before_reraising_primary():
    table = FakeLeaseTable()
    with _load_handler(
        lease_table=table,
        delegate_error=RuntimeError("delegate exploded"),
    ) as (handler, _, delegate_calls):
        with pytest.raises(RuntimeError, match="delegate exploded"):
            handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="delegate-error"),
            )

        assert len(delegate_calls) == 1
        assert len(table.delete_calls) == 1
        assert table.item is None


def test_delegate_and_release_failure_preserves_primary_error(capsys):
    table = FakeLeaseTable()
    table.delete_error = _client_error("InternalServerError", "DeleteItem")
    with _load_handler(
        lease_table=table,
        delegate_error=RuntimeError("primary delegate error"),
    ) as (handler, _, delegate_calls):
        with pytest.raises(RuntimeError, match="primary delegate error"):
            handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="double-error"),
            )

        assert len(delegate_calls) == 1
        assert table.item is not None
        assert "RELEASE_FAILED_AFTER_PRIMARY_ERROR" in capsys.readouterr().out


def test_successful_manual_delegate_with_release_failure_fails_closed():
    table = FakeLeaseTable()
    table.delete_error = _client_error("InternalServerError", "DeleteItem")
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        handler.ADMIN_TOKEN = "test-admin-token"

        response = handler.lambda_handler(
            _manual_event(),
            FakeContext(request_id="release-error"),
        )
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_LOCK_EXECUTION_LEASE_RELEASE_FAILED"
        assert payload["errorCode"] == "InternalServerError"
        assert len(delegate_calls) == 1
        assert table.item is not None


def test_failed_manual_response_and_release_failure_preserves_primary(capsys):
    table = FakeLeaseTable()
    table.delete_error = _client_error("InternalServerError", "DeleteItem")
    with _load_handler(
        lease_table=table,
        delegate_payload={
            "ok": False,
            "error": "PRIMARY_LOCK_FAILURE",
            "reason": "stale source",
        },
    ) as (handler, _, delegate_calls):
        handler.ADMIN_TOKEN = "test-admin-token"

        response = handler.lambda_handler(
            _manual_event(),
            FakeContext(request_id="failed-response-double-error"),
        )
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "PRIMARY_LOCK_FAILURE"
        assert payload["reason"] == "stale source"
        assert payload["lockExecutionConcurrency"]["leaseAcquired"] is True
        assert payload["lockExecutionConcurrency"]["leaseReleased"] is False
        assert len(delegate_calls) == 1
        assert table.item is not None
        assert "RELEASE_FAILED_AFTER_FAILED_RESPONSE" in capsys.readouterr().out


def test_get_and_options_are_read_only_and_bypass_execution_lease():
    table = FakeLeaseTable()
    with _load_handler(lease_table=table) as (handler, _, delegate_calls):
        get_response = handler.lambda_handler(
            {
                "httpMethod": "GET",
                "requestContext": {"stage": "test"},
                "queryStringParameters": {"date": "2026-07-21"},
            },
            FakeContext(request_id="read-only"),
        )
        options_response = handler.lambda_handler(
            {"httpMethod": "OPTIONS", "requestContext": {"stage": "test"}},
            FakeContext(request_id="options"),
        )

        assert get_response["statusCode"] == 200
        assert _body(get_response)["lockExecutionConcurrency"][
            "leaseAcquired"
        ] is False
        assert options_response["statusCode"] == 200
        assert len(delegate_calls) == 1
        assert table.put_calls == []
        assert table.delete_calls == []
        assert table.get_calls == []


def test_runtime_requires_exact_360_second_lease_and_timeout_margin():
    table = FakeLeaseTable()
    with _load_handler(
        lease_table=table,
        lease_seconds=359,
    ) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                _scheduled_event(),
                FakeContext(request_id="short-lease"),
            ),
            "MLB_SCHEDULED_LOCK_PREREQUISITE_FAILED",
        )
        assert handler.PER_GAME_LOCK_STATUS["ok"] is False
        assert delegate_calls == []
        assert table.put_calls == []

    with _load_handler(lease_table=FakeLeaseTable()) as (handler, _, _):
        with pytest.raises(
            RuntimeError,
            match="MLB_LOCK_EXECUTION_LEASE_TIMEOUT_BOUND_FAILED",
        ):
            handler._validate_lease_duration(
                FakeContext(remaining_millis=300_001)
            )


def test_fractional_acquisition_rounds_expiry_up_without_shortening_lease():
    table = FakeLeaseTable()
    clock = {
        "now": datetime(
            2026,
            7,
            21,
            22,
            0,
            0,
            999_999,
            tzinfo=timezone.utc,
        )
    }
    with _load_handler(lease_table=table) as (handler, _, _):
        handler._utc_now = lambda: clock["now"]
        item = handler._acquire_execution_lease(
            mode="scheduled",
            slate_date_et="2026-07-21",
            owner="fractional-owner",
        )
        expires_at = clock["now"] + timedelta(seconds=360)
        expires_epoch = item["lease_expires_at_epoch"]

        assert expires_epoch == math.ceil(expires_at.timestamp())
        assert expires_epoch - clock["now"].timestamp() >= 360

        clock["now"] = datetime.fromtimestamp(
            expires_epoch,
            tz=timezone.utc,
        ) - timedelta(microseconds=1)
        with pytest.raises(handler.LockExecutionLeaseUnavailable):
            handler._acquire_execution_lease(
                mode="manual",
                slate_date_et="2026-07-21",
                owner="too-early-owner",
            )
        assert table.item["lease_owner"] == "fractional-owner"

        clock["now"] = datetime.fromtimestamp(expires_epoch, tz=timezone.utc)
        replacement = handler._acquire_execution_lease(
            mode="manual",
            slate_date_et="2026-07-21",
            owner="on-time-owner",
        )
        assert replacement["lease_owner"] == "on-time-owner"
