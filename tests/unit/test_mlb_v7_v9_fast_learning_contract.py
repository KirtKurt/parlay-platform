import importlib
import os
import sys


def test_fast_shadow_learning_preserves_canonical_audit(monkeypatch):
    monkeypatch.setenv("MLB_HISTORICAL_END_DATE", "2026-12-31")
    monkeypatch.setenv("MLB_HISTORICAL_FRESH_AUDIT_INCREMENT_GAMES", "200")

    for name in (
        "mlb_historical_optimizer_v7_recovery_entrypoint",
        "mlb_historical_optimizer_entrypoint",
        "mlb_historical_optimizer_handler",
        "mlb_historical_v7_learning_cadence_v1",
    ):
        sys.modules.pop(name, None)

    runtime = importlib.import_module("mlb_historical_optimizer_v7_recovery_entrypoint")

    assert os.environ["MLB_V7_SHADOW_REFIT_INCREMENT_GAMES"] == "20"
    assert os.environ["MLB_V7_LIGHTWEIGHT_INCREMENT_GAMES"] == "10"
    assert runtime.learning_cadence.SHADOW_REFIT_INCREMENT_GAMES == 20
    assert runtime.learning_cadence.LIGHTWEIGHT_EVALUATION_INCREMENT_GAMES == 10
    assert runtime.base.optimizer_handler.MAX_NETWORK_REQUESTS == 50
    assert runtime.base.optimizer_handler.FRESH_AUDIT_INCREMENT_GAMES >= 200
    assert runtime.base.optimizer_handler.policy_runtime.MIN_DAILY_ACCURACY == 0.80


def test_status_contract_exposes_accelerated_cadence(monkeypatch):
    monkeypatch.setenv("MLB_HISTORICAL_END_DATE", "2026-12-31")
    runtime = importlib.import_module("mlb_historical_optimizer_v7_recovery_entrypoint")
    value = runtime._with_shadow_contract({})
    contract = value["supervisedShadow"]

    assert contract["shadowRefitIncrementGames"] == 20
    assert contract["lightweightSelectiveEvaluationIncrementGames"] == 10
    assert contract["maximumHistoricalRequestsPerRun"] == 50
    assert contract["canonicalFreshAuditIncrementGames"] >= 200
    assert contract["promotionDailyAccuracyRequirement"] == 0.80
    assert contract["shadowRefitsMayPromote"] is False
