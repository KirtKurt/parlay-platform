from __future__ import annotations

import copy
import importlib.util
import json
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
HANDLER = HELLO / "mlb_manual_pull_protected.py"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_locked_prediction_storage_finalizer_v1 as finalizer


RUNTIME_VERSION = "MLB-ML-RUNTIME-INSTALL-v-postcutoff-test"
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


def test_finalizer_skips_post_cutoff_lifecycle_rows_without_pregame_writes():
    source = {
        "ok": True,
        "gameCount": 3,
        "allGamesPredicted": False,
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "predictions": [
            {
                "gameId": "missed-lock",
                "predictedWinner": None,
                "predictedSide": None,
                "lockStatus": "MISSED_LOCK",
                "officialPredictionStatus": "MISSED_LOCK",
                "recommendationStatus": "MISSED_LOCK",
                "displayGroup": "lock_failure",
                "perGameCanonicalLock": {"status": "MISSED_LOCK"},
            },
            {
                "gameId": "missed-not-backfilled",
                "predictedWinner": None,
                "predictedSide": None,
                "lockStatus": "MISSED_NOT_BACKFILLED",
                "officialPredictionStatus": "MISSED_NOT_BACKFILLED",
                "recommendationStatus": "MISSED_NOT_BACKFILLED",
                "displayGroup": "lock_failure",
                "perGameCanonicalLock": {"status": "MISSED_NOT_BACKFILLED"},
            },
            {
                "gameId": "terminal-no-data",
                "predictedWinner": None,
                "predictedSide": None,
                "lockStatus": "LOCKED_NO_PREDICTION_DATA",
                "officialPredictionStatus": "LOCKED_NO_PREDICTION_DATA",
                "recommendationStatus": "LOCKED_NO_PREDICTION_DATA",
                "displayGroup": "lock_outcome_no_prediction_data",
                "perGameCanonicalLock": {"status": "LOCKED_NO_PREDICTION_DATA"},
            },
        ],
    }
    writes = []
    module = SimpleNamespace(
        predict_all=lambda *args, **kwargs: copy.deepcopy(source),
        _store_prediction=lambda row: writes.append(copy.deepcopy(row)) or {"ok": True},
    )
    finalizer.apply(module)

    result = module.predict_all("2026-07-22", store=True)

    assert writes == []
    assert result["ok"] is True
    assert result["allGamesPredicted"] is False
    assert result["preLockStorageLifecycleAware"] is True
    assert result["preLockStorageCandidateCount"] == 0
    assert result["preLockStoredCount"] == 0
    assert result["preLockStorageComplete"] is True
    assert result["preLockStorageLifecycleSkippedCount"] == 3
    assert result["preLockStorageDispositionCount"] == 3
    assert result["preLockStorageDispositionComplete"] is True
    assert result["preLockStorageLifecycleSkippedStatuses"] == [
        "LOCKED_NO_PREDICTION_DATA",
        "MISSED_LOCK",
        "MISSED_NOT_BACKFILLED",
    ]
    for row in result["predictions"]:
        assert row["preLockStoreSkipped"] is True
        assert row["preLockStoreSkipReason"] == (
            "post_cutoff_lifecycle_status_not_a_prediction_candidate"
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
def _load_handler(delegate_payload: dict):
    manual_pull = ModuleType("mlb_manual_pull")
    manual_pull.lambda_handler = lambda event, context: {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(delegate_payload),
    }

    runtime = ModuleType("mlb_ml_runtime_install_v3")
    runtime.VERSION = RUNTIME_VERSION

    def install():
        sys.modules["mlb_manual_pull"] = manual_pull
        return copy.deepcopy(_runtime_status())

    runtime.install = install
    previous_runtime = sys.modules.get("mlb_ml_runtime_install_v3")
    previous_manual = sys.modules.get("mlb_manual_pull")
    module_name = f"_test_mlb_postcutoff_handler_{uuid.uuid4().hex}"
    try:
        sys.modules["mlb_ml_runtime_install_v3"] = runtime
        sys.modules.pop("mlb_manual_pull", None)
        spec = importlib.util.spec_from_file_location(module_name, HANDLER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(module_name, None)
        if previous_runtime is None:
            sys.modules.pop("mlb_ml_runtime_install_v3", None)
        else:
            sys.modules["mlb_ml_runtime_install_v3"] = previous_runtime
        if previous_manual is None:
            sys.modules.pop("mlb_manual_pull", None)
        else:
            sys.modules["mlb_manual_pull"] = previous_manual


def _manifest_payload(result: dict) -> dict:
    return {
        "ok": True,
        "live_pull_ok": True,
        "count": 2,
        "providerScheduleManifestComplete": True,
        "provider_schedule_manifests": [
            {
                "game_date_et": "2026-07-22",
                "gameCount": 2,
                "version": "INQSI-PROVIDER-SCHEDULE-MANIFEST-v-test",
                "fingerprint": "a" * 64,
                "pk": "PROVIDER_MANIFEST#mlb#2026-07-22",
                "sk": "OBSERVED#test",
                "immutable": True,
                "fullProviderSchedule": True,
                "boundToCanonicalPull": True,
                "ok": True,
            }
        ],
        "game_winner_predictions": [{"game_date_et": "2026-07-22", **result}],
    }


def test_scheduled_pull_accepts_complete_post_cutoff_lifecycle_disposition():
    result = {
        "ok": True,
        "gameCount": 2,
        "count": 2,
        "allGamesPredicted": False,
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "preLockStorageLifecycleAware": True,
        "preLockStorageCandidateCount": 0,
        "preLockStoredCount": 0,
        "preLockStorageComplete": True,
        "preLockStorageLifecycleSkippedCount": 2,
        "preLockStorageDispositionCount": 2,
        "preLockStorageDispositionComplete": True,
    }
    with _load_handler(_manifest_payload(result)) as handler:
        response = handler.lambda_handler(
            {"sport": "mlb", "run": "postcutoff_lifecycle_test"},
            None,
        )

    assert response["statusCode"] == 200


def test_scheduled_pull_still_fails_when_open_candidate_is_not_persisted():
    result = {
        "ok": False,
        "gameCount": 2,
        "count": 2,
        "allGamesPredicted": False,
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "preLockStorageLifecycleAware": True,
        "preLockStorageCandidateCount": 1,
        "preLockStoredCount": 0,
        "preLockStorageComplete": False,
        "preLockStorageLifecycleSkippedCount": 1,
        "preLockStorageDispositionCount": 2,
        "preLockStorageDispositionComplete": True,
    }
    with _load_handler(_manifest_payload(result)) as handler:
        try:
            handler.lambda_handler(
                {"sport": "mlb", "run": "open_candidate_failure_test"},
                None,
            )
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("expected scheduled lifecycle-aware persistence failure")

    assert "prelock_storage_incomplete:2026-07-22" in message
    assert "prelock_candidate_count_mismatch:2026-07-22" in message


def test_scheduled_pull_fails_when_storage_disposition_does_not_cover_slate():
    result = {
        "ok": True,
        "gameCount": 2,
        "count": 2,
        "allGamesPredicted": False,
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "preLockStorageLifecycleAware": True,
        "preLockStorageCandidateCount": 0,
        "preLockStoredCount": 0,
        "preLockStorageComplete": True,
        "preLockStorageLifecycleSkippedCount": 1,
        "preLockStorageDispositionCount": 1,
        "preLockStorageDispositionComplete": False,
    }
    with _load_handler(_manifest_payload(result)) as handler:
        try:
            handler.lambda_handler(
                {"sport": "mlb", "run": "disposition_failure_test"},
                None,
            )
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("expected scheduled storage disposition failure")

    assert "prelock_storage_disposition_incomplete:2026-07-22" in message
