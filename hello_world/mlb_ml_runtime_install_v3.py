from __future__ import annotations

import os
from typing import Any, Dict

# Preserve the deployed runtime identity until the historical champion is
# activated. Existing deployment probes use this as a compatibility envelope;
# the extension identity below is the authoritative version for the new path.
VERSION = (
    "MLB-ML-RUNTIME-INSTALL-v4.4-ranked-winner-v15.10-"
    "prelock-persistence-verified-stage-promotion-authority-"
    "verified-active-model-authority"
)
HISTORICAL_RUNTIME_EXTENSION_VERSION = (
    "MLB-HISTORICAL-RUNTIME-EXTENSION-v1.4-"
    "1000-train-200-validation-200-audit-atomic-historical-only-cutover"
)


def install() -> Dict[str, Any]:
    status: Dict[str, Any] = {
        "applied": True,
        "version": VERSION,
        "historicalRuntimeExtensionVersion": HISTORICAL_RUNTIME_EXTENSION_VERSION,
        "steps": {},
        "errors": [],
    }

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
        import mlb_historical_policy_v1
        import mlb_ml_champion_runtime_v1
        import mlb_official_prediction_semantics
        import mlb_official_freeze_bridge
        import mlb_ml_frozen_features
        import mlb_ml_exact_lock_vector_patch
        import mlb_immutable_locked_storage_patch
        import mlb_locked_prediction_storage_finalizer_v1
        import mlb_last_possible_prediction_gate
        import mlb_probability_actionability_guard
        import mlb_ranked_primary_v15_10
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

        # The historical policy first augments the canonical home/away market
        # signals. It is completely inert until a digest-valid champion proves
        # 1,000 training + 200 validation + 200 untouched-audit games and the
        # every-day 80% full-slate gate.
        if hasattr(engine, "_side_score"):
            mlb_historical_policy_v1.apply(engine)
        status["historicalDailySignalPolicyInstalled"] = bool(
            getattr(engine, "_INQSI_MLB_HISTORICAL_POLICY_V1_APPLIED", False)
            and getattr(engine, "MLB_HISTORICAL_POLICY_VERSION", None)
            == mlb_historical_policy_v1.VERSION
        )
        # Some isolated runtime unit tests stub only the public prediction
        # methods and intentionally omit the private side-score constructor.
        # The production engine must expose it; the cold-start/deployment probe
        # verifies this extension flag is true in the built Lambda.
        status["historicalDailySignalPolicyDeferredForMinimalStub"] = not hasattr(
            engine, "_side_score"
        )

        # V15.10 remains the rollback incumbent only until the historical gate
        # creates a champion. The historical outer wrapper installed below is
        # final and prevents this incumbent from retaining selection authority
        # once the champion exists.
        mlb_ranked_primary_v15_10.apply_direction(engine)
        status["steps"]["rankedWinnerV15_10DirectionInstalled"] = bool(
            getattr(
                engine,
                "_INQSI_MLB_RANKED_WINNER_DIRECTION_V15_10_APPLIED",
                False,
            )
            and getattr(engine, "MLB_RANKED_WINNER_VERSION", None)
            == mlb_ranked_primary_v15_10.VERSION
        )

        # Finalize active-model direction/probability before calibration, the
        # signal-risk policy, the public pre-lock authority and storage writer.
        # Signal policy remains diagnostic/risk metadata; it cannot change the
        # winner selected by the active direction authority.
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
        # The storage finalizer installed afterward is outermost: it forces
        # every inner wrapper to store=False, then persists the exact user-
        # visible row returned by the public authority and precision gate.
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

        # The incumbent ranked selection wrapper is installed first. The
        # historical wrapper is installed *after* it and is therefore the sole
        # final authority whenever a validated champion is present.
        mlb_ranked_primary_v15_10.apply_selection_authority(engine)
        status["steps"]["rankedWinnerV15_10SelectionInstalled"] = bool(
            getattr(
                engine,
                "_INQSI_MLB_RANKED_WINNER_SELECTION_V15_10_APPLIED",
                False,
            )
            and getattr(engine, "MLB_RANKED_WINNER_VERSION", None)
            == mlb_ranked_primary_v15_10.VERSION
            and callable(getattr(engine, "read_persisted_predictions", None))
        )
        status["rankedWinnerVersion"] = mlb_ranked_primary_v15_10.VERSION
        status["rankedWinnerPolicyVersion"] = mlb_ranked_primary_v15_10.POLICY_VERSION
        status["rankedWinnerFirstSlateDate"] = mlb_ranked_primary_v15_10.FIRST_SLATE_DATE
        status["rankedWinnerAllowedOutput"] = ["PICK"]
        status["rankedWinnerModelRunId"] = mlb_ranked_primary_v15_10.MODEL_RUN_ID
        status["rankedWinnerArtifactSha256"] = mlb_ranked_primary_v15_10.MODEL_ARTIFACT_SHA256

        mlb_historical_policy_v1.apply_runtime_authority(engine)
        status["historicalDailyChampionOutermostAuthorityInstalled"] = bool(
            getattr(
                engine,
                "_INQSI_MLB_HISTORICAL_RUNTIME_AUTHORITY_V1_APPLIED",
                False,
            )
            and getattr(engine, "MLB_HISTORICAL_POLICY_VERSION", None)
            == mlb_historical_policy_v1.VERSION
        )
        active_historical_champion = mlb_historical_policy_v1.active_champion()
        active_historical_cutover = (
            mlb_historical_policy_v1.active_production_cutover()
        )
        historical_load = mlb_historical_policy_v1.champion_load_status()
        cutover_load = mlb_historical_policy_v1.production_cutover_status()
        historical_active = bool(
            active_historical_champion and active_historical_cutover
        )
        positively_pre_cutover = bool(
            not active_historical_champion
            and not active_historical_cutover
            and historical_load.get("status") == "ABSENT"
            and cutover_load.get("status") == "ABSENT"
        )
        authority_state_coherent = bool(historical_active or positively_pre_cutover)
        status["steps"]["historicalAuthorityStateCoherent"] = authority_state_coherent
        if not authority_state_coherent:
            status["errors"].append(
                "historical_authority_state_incoherent:"
                + str(historical_load.get("status") or "UNKNOWN")
                + ":"
                + str(cutover_load.get("status") or "UNKNOWN")
            )
        status["historicalDailyChampionActive"] = historical_active
        status["historicalDailyChampionPointerPresent"] = bool(active_historical_champion)
        status["historicalDailyChampionLoadStatus"] = historical_load
        status["historicalProductionCutoverActive"] = bool(active_historical_cutover)
        status["historicalProductionCutoverStatus"] = cutover_load
        status["historicalDailyPolicyVersion"] = mlb_historical_policy_v1.VERSION
        status["historicalDailyPromotionGateVersion"] = (
            mlb_historical_policy_v1.PROMOTION_GATE_VERSION
        )
        status["historicalProductionCutoverVersion"] = (
            mlb_historical_policy_v1.CUTOVER_VERSION
        )
        status["productionAuthoritySource"] = (
            "mlb_historical_daily_champion_only"
            if historical_active
            else (
                # Preserve the current production identity token before cutover
                # so the existing cold-start and deployment identity contracts
                # remain valid while the historical optimizer is still inert.
                "mlb_ranked_winner_v15_10_active_ensemble"
                if positively_pre_cutover
                else "historical_authority_fail_closed"
            )
        )
        status["productionAuthorityLifecycleState"] = (
            "HISTORICAL_DAILY_ONLY"
            if historical_active
            else (
                "INCUMBENT_UNTIL_HISTORICAL_GATE"
                if positively_pre_cutover
                else "FAIL_CLOSED_INCOHERENT"
            )
        )
        status["winnerPickRequiredForEveryValidEvent"] = True
        status["precisionQualificationSeparateFromPick"] = True
        status["precisionHitRateEvidencePassed"] = historical_active
        status["dailySlateAccuracyEvidenceScope"] = (
            "complete_day_slate_not_individual_game"
        )
        status["legacyRecommendationAuthority"] = False
        status["legacyAlgorithmAuthorityDisabled"] = bool(active_historical_cutover)
        status["incumbentProductionAuthorityDestroyed"] = bool(
            active_historical_cutover
        )
        status["legacyFallbackAllowed"] = False
        status["automaticWagerAllowed"] = False
        status["predictionOnlyWagerSafetyInstalled"] = True
        status["rowLevelAutomaticWagerAllowed"] = False

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
        "The historical whole-slate optimizer is installed as the outermost "
        "production authority but remains inert until a digest-valid champion "
        "proves at least 1,000 training games, 200 walk-forward games, 200 "
        "untouched-audit games, exact slate coverage, and at least 80% on every "
        "held-out day. Before that gate, V15.10 remains the incumbent. After the "
        "gate, an atomic write-once cutover destroys V15.10 production authority. "
        "Its code may remain quarantined only as feature and explicit rollback "
        "material; it cannot be selected automatically or change the chosen team. "
        "The 80% evidence is a complete-day slate metric, not an individual "
        "game probability claim; automatic wagering remains disabled."
    )

    return status
