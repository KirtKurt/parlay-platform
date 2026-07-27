from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "hello_world" / "mlb_historical_v7_learning_cadence_v1.py"
ENTRYPOINT = ROOT / "hello_world" / "mlb_historical_optimizer_v7_recovery_entrypoint.py"


def test_shadow_cadence_does_not_reduce_canonical_audit():
    source = PATCH.read_text(encoding="utf-8")
    assert "SHADOW_REFIT_INCREMENT_GAMES = 50" in source
    assert "handler.FRESH_AUDIT_INCREMENT_GAMES =" not in source
    assert "cannot be below 200" in source


def test_challenger_rank_prioritizes_chronological_mean_and_calibration():
    source = PATCH.read_text(encoding="utf-8")
    mean_pos = source.index('metrics.get("meanDailyAccuracy")')
    pass_pos = source.index('metrics.get("dailyPassRate")')
    brier_pos = source.index('metrics.get("brierScore")')
    logloss_pos = source.index('metrics.get("logLoss")')
    assert mean_pos < brier_pos < logloss_pos < pass_pos


def test_recovery_entrypoint_installs_repairs_before_supervised_runtime():
    source = ENTRYPOINT.read_text(encoding="utf-8")
    feature_pos = source.index("priority_repairs.install_feature_repairs(supervised_v9)")
    integrity_pos = source.index("supervised_integrity_v2.install(supervised_v9)")
    supervised_pos = source.index("supervised_v9.install(")
    cadence_pos = source.index("learning_cadence.install(")
    assert feature_pos < integrity_pos < supervised_pos < cadence_pos
    assert '"shadowRefitIncrementGames"' in source
    assert '"canonicalFreshAuditIncrementGames"' in source
    assert '"shadowRefitsMayPromote": False' in source
