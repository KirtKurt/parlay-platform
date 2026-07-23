from __future__ import annotations

import os
from typing import Any, Dict

VERSION = (
    "MLB-ML-RUNTIME-INSTALL-v4.2-signal-policy-prelock-persistence-"
    "aws-v2-shadow-manual-first"
)


def install() -> Dict[str, Any]:
    status: Dict[str, Any] = {"applied": True, "version": VERSION, "steps": {}, "errors": []}

    try:
        import mlb_accuracy_target_policy_v1
        policy = mlb_accuracy_target_policy_v1.install()
        status["steps"]["accuracyTargetsSeparated"] = policy.get("ok") is True
        status["accuracyTargetPolicy"] = policy
        if policy.get("ok") is not True:
            status["errors"].append(str(policy.get("errors") or policy))
    except Exception as exc:
        status["steps"]["accuracyTargetsSeparated"] = False
        status["errors"].append(str(exc))

    try:
        import mlb_ml_runtime_overlay
        import mlb_ml_runtime_safety_patch
        mlb_ml_runtime_safety_patch.apply(mlb_ml_runtime_overlay)
        status["steps"]["legacyReliabilityOverlaySafety"] = True
    except Exception as exc:
        status["steps"]["legacyReliabilityOverlaySafety"] = False
        status["errors"].append(str(exc))

    try:
        import mlb_game_winner_engine as engine
        import mlb_fundamentals_snapshot_v1
        import mlb_fundamentals_snapshot_v2
        import mlb_ml_champion_runtime_v1
        import mlb_official_prediction_semantics
        import mlb_official_freeze_bridge
        import mlb_ml_frozen_features
        import mlb_ml_exact_lock_vector_patch
        import mlb_immutable_locked_storage_patch
        import mlb_locked_prediction_storage_finalizer_v1
        import mlb_last_possible_prediction_gate
        import mlb_probability_actionability_guard
        import mlb_prediction_probability_contract_v1
        import mlb_signal_policy_v12
        import mlb_slate_coverage_patch
        import mlb_slate_prediction_lock

        mlb_ml_exact_lock_vector_patch.apply(mlb_ml_frozen_features)
        mlb_official_freeze_bridge.apply(mlb_official_prediction_semantics)

        for attr, patch in [
            ("_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V1_APPLIED", mlb_fundamentals_snapshot_v1),
            ("_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V2_APPLIED", mlb_fundamentals_snapshot_v2),
            ("_INQSI_MLB_ML_CHAMPION_RUNTIME_V1_APPLIED", mlb_ml_champion_runtime_v1),
            ("_INQSI_MLB_OFFICIAL_PREDICTION_SEMANTICS_APPLIED", mlb_official_prediction_semantics),
        ]:
            if not hasattr(engine, attr):
                patch.apply(engine)

        # Install the per-game read authority explicitly. The slate wrapper is
        # annotation-only; it cannot generate a second pick at the cutoff.
        mlb_slate_coverage_patch.apply(mlb_slate_prediction_lock)
        mlb_slate_prediction_lock.apply(engine)
        # Lambda adds LAMBDA_TASK_ROOT to sys.path after Python has already
        # processed sitecustomize, so this legacy wrapper cannot be assumed to
        # have been imported during interpreter startup. Install it here and
        # then disable its finality behavior with the canonical per-game flag.
        # The wrapper remains annotation-only and dynamically observes the
        # flag below on every call.
        mlb_last_possible_prediction_gate.apply(engine)
        engine._INQSI_MLB_CANONICAL_PER_GAME_AUTHORITY_ENABLED = True
        status["steps"]["legacyFinalGateDisabled"] = bool(
            getattr(engine, "_INQSI_MLB_CANONICAL_PER_GAME_AUTHORITY_ENABLED", False)
            and getattr(engine, "_INQSI_MLB_LAST_POSSIBLE_GATE_APPLIED", False)
        )

        # Finalize direction/probability before calibration, the signal-risk
        # policy, the public pre-lock authority and the sole storage writer.
        # The signal policy must run before the public authority so its
        # versioned signalPolicyV13 evidence survives into the exact public
        # PRE_LOCK_PLATFORM_PREDICTION that _store_prediction validates.
        mlb_prediction_probability_contract_v1.apply(engine)
        mlb_probability_actionability_guard.apply(engine)
        mlb_signal_policy_v12.apply(engine)
        status["steps"]["signalPolicyV13Installed"] = bool(
            getattr(engine, "_INQSI_MLB_SIGNAL_POLICY_V12_APPLIED", False)
            and str(getattr(mlb_signal_policy_v12, "VERSION", "")).startswith(
                "MLB-SIGNAL-POLICY-v1."
            )
        )
        status["signalPolicyV13Version"] = mlb_signal_policy_v12.VERSION
        engine._INQSI_MLB_PERSISTED_PRELOCK_PUBLIC_AUTHORITY_ENABLED = True

        exact_vector = getattr(mlb_ml_frozen_features, "_INQSI_MLB_EXACT_LOCK_VECTOR_PATCH_APPLIED", False)
        official_bridge = getattr(mlb_official_prediction_semantics, "_INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED_V2", False)
        status["steps"]["sourceHonestFundamentals"] = hasattr(engine, "_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V1_APPLIED")
        legacy_runtime_installed = hasattr(
            engine, "_INQSI_MLB_ML_CHAMPION_RUNTIME_V1_APPLIED"
        )
        legacy_authority_enabled = False
        automatic_promotion_enabled = str(
            os.environ.get("INQSI_MLB_ML_AUTO_PROMOTE", "false")
        ).strip().lower() in {"1", "true", "yes"}
        status["steps"][
            "legacyV1ChampionRuntimeInstalledForShadowDiagnostics"
        ] = legacy_runtime_installed
        status["steps"]["legacyV1AuthorityDisabled"] = bool(
            legacy_runtime_installed and not legacy_authority_enabled
        )
        status["steps"]["v2ShadowManualFirst"] = not automatic_promotion_enabled
        status["steps"]["officialSemanticsFinalized"] = hasattr(engine, "_INQSI_MLB_OFFICIAL_PREDICTION_SEMANTICS_APPLIED")
        status["steps"]["exactCleanCohortVectorPatch"] = exact_vector
        status["steps"]["officialFreezeBridge"] = official_bridge
        # Compatibility name used by the existing AWS deploy smoke test. It now
        # means the stronger exact clean-cohort vector path is installed.
        status["steps"]["immutableFeatureFreeze"] = bool(exact_vector and official_bridge)
        # Do not rely on sitecustomize for canonical write safety. The engine
        # itself must attest that LOCKED#GAME writes consistent-read and bind
        # the exact immutable T-minus-45 stage before storage.
        mlb_immutable_locked_storage_patch.apply(engine)
        status["steps"]["immutableLockedStorageAuthority"] = bool(
            getattr(engine, "_INQSI_MLB_IMMUTABLE_LOCKED_STORAGE_APPLIED", False)
            and getattr(engine, "IMMUTABLE_LOCKED_STORAGE_VERSION", None)
            == mlb_immutable_locked_storage_patch.VERSION
        )
        # Produce the final public pre-lock representation before persistence.
        # The storage finalizer installed immediately afterward is outermost:
        # it forces every inner wrapper to store=False, then persists the exact
        # user-visible PRE_LOCK_PLATFORM_PREDICTION returned here.
        mlb_slate_coverage_patch.install_public_authority(engine, mlb_slate_prediction_lock)
        status["steps"]["canonicalProbabilityAndPersistedPrelockAuthority"] = bool(
            getattr(
                engine,
                "_INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED",
                False,
            )
            and getattr(
                engine,
                "MLB_PREDICTION_PROBABILITY_CONTRACT_VERSION",
                None,
            )
            == mlb_prediction_probability_contract_v1.VERSION
            and getattr(
                engine,
                "_INQSI_MLB_PERSISTED_PRELOCK_PUBLIC_AUTHORITY_ENABLED",
                False,
            )
            and callable(getattr(engine, "read_persisted_predictions", None))
        )
        status["probabilityContractVersion"] = mlb_prediction_probability_contract_v1.VERSION
        status["steps"]["providerNeutralCalibrationAndActionability"] = bool(
            getattr(
                engine,
                "_INQSI_MLB_PROVIDER_NEUTRAL_CALIBRATION_APPLIED",
                False,
            )
            and getattr(
                engine,
                "MLB_PROBABILITY_ACTIONABILITY_GUARD_VERSION",
                None,
            )
            == mlb_probability_actionability_guard.PATCH_VERSION
        )
        status["probabilityActionabilityGuardVersion"] = (
            mlb_probability_actionability_guard.PATCH_VERSION
        )
        # V2 is installed before semantics/freezing, so the exact signed
        # snapshot is bound into the T-45 vector. The public persisted-read
        # alias remains inside the final storage wrapper and never recomputes.
        status["steps"]["sourceHonestFundamentalsV2"] = bool(
            getattr(engine, "_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V2_APPLIED", False)
            and getattr(engine, "MLB_FUNDAMENTALS_SNAPSHOT_V2_VERSION", None)
            == mlb_fundamentals_snapshot_v2.VERSION
        )
        status["fundamentalsSnapshotV2Version"] = mlb_fundamentals_snapshot_v2.VERSION
        status["steps"]["lastPrelockPromotionAuthority"] = bool(
            getattr(engine, "_INQSI_MLB_PUBLIC_PER_GAME_AUTHORITY_APPLIED", False)
            and getattr(
                mlb_slate_prediction_lock,
                "_INQSI_MLB_LAST_PRELOCK_PROMOTION_AUTHORITY_APPLIED",
                False,
            )
            and getattr(engine, "MLB_PUBLIC_PER_GAME_AUTHORITY_VERSION", None)
            == mlb_slate_coverage_patch.AUTHORITY_VERSION
        )
        status["lastPrelockPromotionAuthorityVersion"] = mlb_slate_coverage_patch.AUTHORITY_VERSION
        mlb_locked_prediction_storage_finalizer_v1.apply(engine)
        status["steps"]["canonicalLockedStorageFinalizer"] = hasattr(
            engine, "_INQSI_MLB_LOCKED_STORAGE_FINALIZER_V1_APPLIED"
        )
        engine.MLB_ML_RUNTIME_INSTALL_V3 = status
    except Exception as exc:
        status["steps"]["engineRuntime"] = False
        status["errors"].append(str(exc))

    status["ok"] = not status["errors"] and all(status["steps"].values())
    status["policy"] = (
        "The persisted canonical rules/market prediction remains production direction and playability authority. "
        "Legacy V1 champions are diagnostic-only. AWS V2 trains and evaluates in shadow using fixed whole-slate "
        "300/100/100 partitions; the first promotion always requires manual review and automatic promotion is disabled. "
        "Every new locked game stores the exact immutable clean-cohort vector before final labels exist. "
        "The final public lock is the validated canonical promotion of the last prediction available at each game's own T-minus-45 cutoff; no lock-time rescore is authoritative."
    )
    return status
