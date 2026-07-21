from __future__ import annotations

import copy
import importlib.util
import json
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType


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

    def delegate(event, context):
        delegate_calls.append((event, context))
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
    try:
        sys.modules.update(stubs)
        spec = importlib.util.spec_from_file_location(module_name, HANDLER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module, events, delegate_calls
    finally:
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
    with _load_handler() as (handler, events, delegate_calls):
        assert events == [
            "runtime_install",
            "coverage_patch",
            "vector_preservation_patch",
            "per_game_patch",
        ]

        response = handler.lambda_handler({}, object())
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
        assert len(delegate_calls) == 1


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
