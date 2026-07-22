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
HANDLER = ROOT / "hello_world" / "mlb_manual_pull_protected.py"
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
    "scoringRunProof",
}
STUB_MODULE_NAMES = (
    "mlb_ml_runtime_install_v3",
    "mlb_manual_pull",
    "mlb_scoring_run_proof",
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
    delegate_status=200,
    scoring_proof_ok=True,
):
    events = []
    delegate_calls = []

    manual_pull = ModuleType("mlb_manual_pull")

    def delegate(event, context):
        events.append("manual_pull_delegate")
        delegate_calls.append((event, context))
        return {
            "statusCode": delegate_status,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(delegate_payload or {"ok": True, "candidateStored": True}),
        }

    manual_pull.lambda_handler = delegate

    scoring = ModuleType("mlb_scoring_run_proof")
    scoring.VERSION = "MLB-SCORING-RUN-PROOF-test"

    def attach_and_store(response, event):
        out = dict(response)
        payload = json.loads(out.get("body") or "{}")
        if payload.get("ok") is not True or int(out.get("statusCode") or 200) >= 400:
            return response
        manifests = payload.get("provider_schedule_manifests") or []
        if scoring_proof_ok:
            payload["scoringProofComplete"] = True
            payload["scoringProofStatus"] = "PASS"
            payload["scoring_proofs"] = [
                {
                    "ok": True,
                    "status": "PASS",
                    "gameDateEt": manifest.get("game_date_et"),
                }
                for manifest in manifests
                if isinstance(manifest, dict)
            ]
        else:
            payload["ok"] = False
            payload["error"] = "MLB_SCORING_RUN_PROOF_FAILED"
            payload["scoringProofComplete"] = False
            payload["scoringProofStatus"] = "FAIL"
            payload["scoring_proofs"] = [
                {
                    "ok": False,
                    "status": "FAIL",
                    "gameDateEt": manifest.get("game_date_et"),
                    "blockers": ["injected_scoring_failure"],
                }
                for manifest in manifests
                if isinstance(manifest, dict)
            ]
            out["statusCode"] = 500
        out["body"] = json.dumps(payload)
        return out

    scoring.attach_and_store = attach_and_store

    runtime = ModuleType("mlb_ml_runtime_install_v3")
    runtime.VERSION = RUNTIME_VERSION

    def install():
        events.append("runtime_install")
        if runtime_error is not None:
            raise runtime_error
        # The writer becomes importable only after installation. This makes
        # import ordering observable rather than relying on a source-text test.
        sys.modules["mlb_manual_pull"] = manual_pull
        return copy.deepcopy(runtime_status if runtime_status is not None else _runtime_status())

    runtime.install = install

    previous = {name: sys.modules.get(name) for name in STUB_MODULE_NAMES}
    module_name = f"_test_mlb_manual_pull_protected_{uuid.uuid4().hex}"
    try:
        sys.modules["mlb_scoring_run_proof"] = scoring
        sys.modules["mlb_ml_runtime_install_v3"] = runtime
        sys.modules.pop("mlb_manual_pull", None)
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


def _authorized_event():
    return {
        "httpMethod": "POST",
        "requestContext": {"stage": "test"},
        "headers": {"x-inqsi-admin-token": "test-token"},
    }


def test_installs_and_attests_exact_runtime_before_importing_or_serving_writer():
    with _load_handler() as (handler, events, delegate_calls):
        assert events == ["runtime_install"]
        assert handler.mlb_manual_pull is not None
        handler.ADMIN_TOKEN = "test-token"

        response = handler.lambda_handler(_authorized_event(), object())
        payload = _body(response)

        assert response["statusCode"] == 200
        assert payload["candidateStored"] is True
        assert payload["mlRuntimeInstallation"]["ok"] is True
        assert payload["mlRuntimeInstallation"]["applied"] is True
        assert payload["mlRuntimeInstallation"]["version"] == RUNTIME_VERSION
        assert payload["mlRuntimeInstallation"]["expectedVersion"] == RUNTIME_VERSION
        assert payload["mlRuntimeInstallation"]["candidateWriterImported"] is True
        assert payload["mlRuntimeInstallation"]["scoringProofVersion"] == "MLB-SCORING-RUN-PROOF-test"
        assert payload["mlRuntimeInstallation"]["steps"] == {
            name: True for name in REQUIRED_RUNTIME_STEPS
        }
        assert events == ["runtime_install", "manual_pull_delegate"]
        assert len(delegate_calls) == 1


def test_missing_last_prelock_authority_fails_closed_without_serving_writer():
    status = _runtime_status()
    status["steps"].pop("lastPrelockPromotionAuthority")

    with _load_handler(runtime_status=status) as (handler, events, delegate_calls):
        handler.ADMIN_TOKEN = "test-token"
        response = handler.lambda_handler(_authorized_event(), None)
        payload = _body(response)

        assert response["statusCode"] == 500
        assert payload["error"] == "MLB_ML_PULL_RUNTIME_NOT_READY"
        assert payload["status"]["ok"] is False
        assert payload["status"]["candidateWriterImported"] is False
        assert payload["status"]["missingRequiredSteps"] == [
            "lastPrelockPromotionAuthority"
        ]
        assert events == ["runtime_install"]
        assert delegate_calls == []


def test_runtime_version_mismatch_fails_closed_and_options_exposes_attestation():
    status = _runtime_status()
    status["version"] = "unexpected-runtime-version"

    with _load_handler(runtime_status=status) as (handler, _, delegate_calls):
        handler.ADMIN_TOKEN = "test-token"
        response = handler.lambda_handler(_authorized_event(), None)
        payload = _body(response)
        options = _body(handler.lambda_handler({"httpMethod": "OPTIONS"}, None))

        assert response["statusCode"] == 500
        assert payload["status"]["ok"] is False
        assert payload["status"]["expectedVersion"] == RUNTIME_VERSION
        assert options["mlRuntimeInstallation"]["ok"] is False
        assert delegate_calls == []


def test_runtime_installer_exception_fails_closed_without_importing_writer():
    with _load_handler(runtime_error=RuntimeError("injected install failure")) as (
        handler,
        events,
        delegate_calls,
    ):
        handler.ADMIN_TOKEN = "test-token"
        response = handler.lambda_handler(_authorized_event(), None)
        payload = _body(response)

        assert response["statusCode"] == 500
        assert "injected install failure" in payload["status"]["errors"]
        assert payload["status"]["candidateWriterImported"] is False
        assert handler.mlb_manual_pull is None
        assert events == ["runtime_install"]
        assert delegate_calls == []


def test_scheduled_prerequisite_failure_raises_for_eventbridge_visibility():
    status = _runtime_status()
    status["steps"]["lastPrelockPromotionAuthority"] = False

    with _load_handler(runtime_status=status) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                {"sport": "mlb", "run": "rolling_open_hot_pull"}, None
            ),
            "MLB_SCHEDULED_PULL_PREREQUISITE_FAILED",
        )
        assert delegate_calls == []


def test_scheduled_delegate_failure_raises_for_eventbridge_visibility():
    with _load_handler(
        delegate_status=500,
        delegate_payload={"ok": False, "error": "injected pull failure"},
    ) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                {"sport": "mlb", "run": "rolling_open_hot_pull"}, None
            ),
            "MLB_SCHEDULED_PULL_FAILED",
        )
        assert len(delegate_calls) == 1


def test_scheduled_candidate_persistence_failure_raises_even_when_pull_is_http_200():
    payload = {
        "ok": True,
        "live_pull_ok": True,
        "count": 1,
        "game_winner_predictions": [
            {
                "game_date_et": "2026-07-16",
                "ok": False,
                "preLockStorageComplete": False,
                "preLockStorageErrors": ["injected persistence failure"],
            }
        ],
    }
    with _load_handler(delegate_payload=payload) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                {"sport": "mlb", "run": "rolling_open_hot_pull"}, None
            ),
            "prelock_storage_incomplete:2026-07-16",
        )
        assert len(delegate_calls) == 1


def test_scheduled_incomplete_candidate_coverage_raises_even_when_storage_succeeds():
    payload = {
        "ok": True,
        "live_pull_ok": True,
        "count": 2,
        "game_winner_predictions": [
            {
                "game_date_et": "2026-07-16",
                "ok": True,
                "allGamesPredicted": False,
                "gameCount": 2,
                "preLockStorageCandidateCount": 1,
                "preLockStoredCount": 1,
                "preLockStorageComplete": True,
            }
        ],
    }
    with _load_handler(delegate_payload=payload) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                {"sport": "mlb", "run": "rolling_open_hot_pull"}, None
            ),
            "winner_prediction_coverage_incomplete:2026-07-16",
        )
        assert len(delegate_calls) == 1


def _complete_manifest_payload(*, prediction_game_count=2):
    return {
        "ok": True,
        "live_pull_ok": True,
        "count": 2,
        "providerScheduleManifestComplete": True,
        "provider_schedule_manifests": [
            {
                "game_date_et": "2026-07-16",
                "gameCount": 2,
                "version": "INQSI-PROVIDER-SCHEDULE-MANIFEST-v1",
                "fingerprint": "a" * 64,
                "pk": "PROVIDER_MANIFEST#mlb#2026-07-16",
                "sk": "OBSERVED#2026-07-16T17:00:00+00:00#PULL#test",
                "immutable": True,
                "fullProviderSchedule": True,
                "boundToCanonicalPull": True,
                "ok": True,
            }
        ],
        "game_winner_predictions": [
            {
                "game_date_et": "2026-07-16",
                "ok": True,
                "allGamesPredicted": prediction_game_count == 2,
                "gameCount": prediction_game_count,
                "preLockStorageCandidateCount": prediction_game_count,
                "preLockStoredCount": prediction_game_count,
                "preLockStorageComplete": True,
            }
        ],
    }


def test_scheduled_full_provider_manifest_and_candidate_coverage_succeeds():
    with _load_handler(delegate_payload=_complete_manifest_payload()) as (
        handler,
        _,
        delegate_calls,
    ):
        response = handler.lambda_handler(
            {"sport": "mlb", "run": "rolling_open_hot_pull"}, None
        )
        payload = _body(response)
        assert response["statusCode"] == 200
        assert payload["scoringProofComplete"] is True
        assert payload["scoring_proofs"][0]["ok"] is True
        assert len(delegate_calls) == 1


def test_scheduled_prediction_count_must_equal_full_provider_manifest_count():
    with _load_handler(
        delegate_payload=_complete_manifest_payload(prediction_game_count=1)
    ) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                {"sport": "mlb", "run": "rolling_open_hot_pull"}, None
            ),
            "winner_prediction_manifest_count_mismatch:2026-07-16",
        )
        assert len(delegate_calls) == 1


def test_scheduled_scoring_proof_failure_raises_after_candidate_storage():
    with _load_handler(
        delegate_payload=_complete_manifest_payload(),
        scoring_proof_ok=False,
    ) as (handler, _, delegate_calls):
        _assert_runtime_error(
            lambda: handler.lambda_handler(
                {"sport": "mlb", "run": "rolling_open_hot_pull"}, None
            ),
            "scoring_run_proof_incomplete",
        )
        assert len(delegate_calls) == 1
