from __future__ import annotations

from hello_world import mlb_supervised_daily_objective_v2_1 as objective


def _metrics(*, daily_pass, minimum, mean, overall, log_loss, brier, ece):
    return {
        "dailyPassRate": daily_pass,
        "minimumDailyAccuracy": minimum,
        "meanDailyAccuracy": mean,
        "overallAccuracy": overall,
        "logLoss": log_loss,
        "brierScore": brier,
        "expectedCalibrationError": ece,
    }


def test_daily_slate_objective_outranks_small_log_loss_advantage():
    market = _metrics(
        daily_pass=0.10,
        minimum=0.20,
        mean=0.55,
        overall=0.55,
        log_loss=0.690,
        brier=0.245,
        ece=0.04,
    )
    daily_better = _metrics(
        daily_pass=0.20,
        minimum=0.25,
        mean=0.59,
        overall=0.58,
        log_loss=0.695,
        brier=0.247,
        ece=0.05,
    )
    logloss_better = _metrics(
        daily_pass=0.10,
        minimum=0.20,
        mean=0.55,
        overall=0.55,
        log_loss=0.680,
        brier=0.240,
        ece=0.03,
    )
    assert objective.daily_objective_key(daily_better, market) < objective.daily_objective_key(logloss_better, market)


def test_calibration_ineligible_candidate_is_ranked_after_safe_candidate():
    market = _metrics(
        daily_pass=0.10,
        minimum=0.20,
        mean=0.55,
        overall=0.55,
        log_loss=0.690,
        brier=0.245,
        ece=0.04,
    )
    safe = _metrics(
        daily_pass=0.15,
        minimum=0.20,
        mean=0.57,
        overall=0.56,
        log_loss=0.695,
        brier=0.247,
        ece=0.05,
    )
    unsafe = _metrics(
        daily_pass=0.40,
        minimum=0.40,
        mean=0.70,
        overall=0.68,
        log_loss=0.760,
        brier=0.290,
        ece=0.15,
    )
    assert objective.daily_objective_key(safe, market) < objective.daily_objective_key(unsafe, market)


def test_install_is_shadow_only_and_idempotent():
    class Model:
        VERSION = "old"

        @staticmethod
        def _config_key(metrics, market):
            return (999.0,)

    first = objective.install(Model)
    second = objective.install(Model)
    assert first is second is Model
    assert Model.VERSION == objective.VERSION
    assert Model.SUPERVISED_SELECTION_OBJECTIVE["productionAuthorityChanged"] is False
    assert Model.SUPERVISED_SELECTION_OBJECTIVE["untouchedAuditUsedForSelection"] is False
    assert Model.SUPERVISED_SELECTION_OBJECTIVE["selectionGuardVersion"] == (
        objective.selection_guard.VERSION
    )
    assert Model.SUPERVISED_SELECTION_OBJECTIVE["bbsProviderHorizonFoldPolicy"] == (
        "require_two_evaluable_training_and_validation_folds"
    )
    assert Model.SUPERVISED_SELECTION_OBJECTIVE["bbsUnsupportedFoldsCountAsPassing"] is False
