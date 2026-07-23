from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "hello_world" / "mlb_ml_runtime_install_v3.py"


def _module(name: str) -> ModuleType:
    return ModuleType(name)


def test_installer_disables_legacy_gate_without_sitecustomize() -> None:
    """Lambda must not depend on startup-time sitecustomize discovery."""

    events: list[str] = []
    engine = _module("mlb_game_winner_engine")
    engine.predict_all = lambda *args, **kwargs: {"predictions": []}

    accuracy = _module("mlb_accuracy_target_policy_v1")
    accuracy.install = lambda: {"ok": True, "errors": []}

    overlay = _module("mlb_ml_runtime_overlay")
    safety = _module("mlb_ml_runtime_safety_patch")
    safety.apply = lambda module: events.append("runtime_safety")

    fundamentals = _module("mlb_fundamentals_snapshot_v1")
    fundamentals.apply = lambda module: setattr(
        module, "_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V1_APPLIED", True
    )
    fundamentals_v2 = _module("mlb_fundamentals_snapshot_v2")
    fundamentals_v2.VERSION = "test-fundamentals-v2"

    def apply_fundamentals_v2(module: ModuleType) -> None:
        events.append("fundamentals_v2_apply")
        module._INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V2_APPLIED = True
        module.MLB_FUNDAMENTALS_SNAPSHOT_V2_VERSION = fundamentals_v2.VERSION

    fundamentals_v2.apply = apply_fundamentals_v2
    champion = _module("mlb_ml_champion_runtime_v1")
    champion.apply = lambda module: setattr(
        module, "_INQSI_MLB_ML_CHAMPION_RUNTIME_V1_APPLIED", True
    )
    semantics = _module("mlb_official_prediction_semantics")
    semantics.apply = lambda module: setattr(
        module, "_INQSI_MLB_OFFICIAL_PREDICTION_SEMANTICS_APPLIED", True
    )

    frozen = _module("mlb_ml_frozen_features")
    exact_vector = _module("mlb_ml_exact_lock_vector_patch")
    exact_vector.apply = lambda module: setattr(
        module, "_INQSI_MLB_EXACT_LOCK_VECTOR_PATCH_APPLIED", True
    )
    freeze_bridge = _module("mlb_official_freeze_bridge")
    freeze_bridge.apply = lambda module: setattr(
        module, "_INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED_V2", True
    )

    immutable = _module("mlb_immutable_locked_storage_patch")
    immutable.VERSION = "test-immutable-version"

    def apply_immutable(module: ModuleType) -> None:
        module._INQSI_MLB_IMMUTABLE_LOCKED_STORAGE_APPLIED = True
        module.IMMUTABLE_LOCKED_STORAGE_VERSION = immutable.VERSION

    immutable.apply = apply_immutable

    finalizer = _module("mlb_locked_prediction_storage_finalizer_v1")

    def apply_finalizer(module: ModuleType) -> None:
        events.append("storage_finalizer_apply")
        original = module.predict_all

        def final_storage_writer(*args, **kwargs):
            return original(*args, **kwargs)

        module.predict_all = final_storage_writer
        module._INQSI_MLB_LOCKED_STORAGE_FINALIZER_V1_APPLIED = True

    finalizer.apply = apply_finalizer

    legacy_gate = _module("mlb_last_possible_prediction_gate")

    def apply_legacy_gate(module: ModuleType) -> None:
        # The regression condition: no startup hook pre-installed this flag.
        assert not getattr(module, "_INQSI_MLB_LAST_POSSIBLE_GATE_APPLIED", False)
        events.append("legacy_gate_apply")
        module._INQSI_MLB_LAST_POSSIBLE_GATE_APPLIED = True

    legacy_gate.apply = apply_legacy_gate

    probability = _module("mlb_prediction_probability_contract_v1")
    probability.VERSION = "test-probability-contract"

    def apply_probability(module: ModuleType) -> None:
        events.append("probability_contract_apply")
        module._INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED = True
        module.MLB_PREDICTION_PROBABILITY_CONTRACT_VERSION = probability.VERSION

    probability.apply = apply_probability

    actionability = _module("mlb_probability_actionability_guard")
    actionability.PATCH_VERSION = "test-provider-neutral-actionability"

    def apply_actionability(module: ModuleType) -> None:
        events.append("probability_actionability_apply")
        module._INQSI_MLB_PROVIDER_NEUTRAL_CALIBRATION_APPLIED = True
        module.MLB_PROBABILITY_ACTIONABILITY_GUARD_VERSION = (
            actionability.PATCH_VERSION
        )

    actionability.apply = apply_actionability

    signal_policy = _module("mlb_signal_policy_v12")
    signal_policy.VERSION = "MLB-SIGNAL-POLICY-v1.7-test"

    def apply_signal_policy(module: ModuleType) -> None:
        events.append("signal_policy_v13_apply")
        module._INQSI_MLB_SIGNAL_POLICY_V12_APPLIED = True

    signal_policy.apply = apply_signal_policy

    slate_lock = _module("mlb_slate_prediction_lock")
    slate_lock.apply = lambda module: events.append("slate_lock_apply")

    coverage = _module("mlb_slate_coverage_patch")
    coverage.AUTHORITY_VERSION = "test-public-authority-version"
    coverage.apply = lambda module: events.append("coverage_apply")

    def install_public_authority(module: ModuleType, lock_module: ModuleType) -> None:
        events.append("public_authority_apply")
        module._INQSI_MLB_PUBLIC_PER_GAME_AUTHORITY_APPLIED = True
        module.MLB_PUBLIC_PER_GAME_AUTHORITY_VERSION = coverage.AUTHORITY_VERSION
        module.read_persisted_predictions = module.predict_all
        lock_module._INQSI_MLB_LAST_PRELOCK_PROMOTION_AUTHORITY_APPLIED = True

    coverage.install_public_authority = install_public_authority

    stubs = {
        module.__name__: module
        for module in (
            accuracy,
            overlay,
            safety,
            engine,
            fundamentals,
            fundamentals_v2,
            champion,
            semantics,
            freeze_bridge,
            frozen,
            exact_vector,
            immutable,
            finalizer,
            legacy_gate,
            probability,
            actionability,
            signal_policy,
            coverage,
            slate_lock,
        )
    }
    previous = {name: sys.modules.get(name) for name in stubs}
    module_name = f"_test_mlb_runtime_install_{uuid.uuid4().hex}"
    try:
        sys.modules.update(stubs)
        spec = importlib.util.spec_from_file_location(module_name, INSTALLER)
        assert spec is not None and spec.loader is not None
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)

        status = installer.install()

        assert status["ok"] is True
        assert status["version"] == (
            "MLB-ML-RUNTIME-INSTALL-v4.2-signal-policy-prelock-persistence-"
            "verified-stage-promotion-authority-aws-v2-shadow-manual-first"
        )
        assert status["steps"]["legacyV1AuthorityDisabled"] is True
        assert status["steps"]["v2ShadowManualFirst"] is True
        assert status["steps"]["legacyFinalGateDisabled"] is True
        assert status["steps"]["lastPrelockPromotionAuthority"] is True
        assert status["steps"][
            "providerNeutralCalibrationAndActionability"
        ] is True
        assert status["steps"]["signalPolicyV13Installed"] is True
        assert status["signalPolicyV13Version"] == signal_policy.VERSION
        assert events.count("legacy_gate_apply") == 1
        assert events.count("signal_policy_v13_apply") == 1
        assert events.index("fundamentals_v2_apply") < events.index(
            "storage_finalizer_apply"
        )
        # V2 must wrap prediction generation before public authority captures
        # that chain and before the storage finalizer freezes it. Persisted
        # public reads retain their separate unwrapped alias below.
        assert events.index("fundamentals_v2_apply") < events.index(
            "public_authority_apply"
        )
        assert events.index("probability_contract_apply") < events.index(
            "probability_actionability_apply"
        )
        assert events.index("probability_actionability_apply") < events.index(
            "signal_policy_v13_apply"
        )
        assert events.index("signal_policy_v13_apply") < events.index(
            "public_authority_apply"
        )
        assert engine.read_persisted_predictions is not engine.predict_all
        assert engine._INQSI_MLB_CANONICAL_PER_GAME_AUTHORITY_ENABLED is True
    finally:
        sys.modules.pop(module_name, None)
        for name, original in previous.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
