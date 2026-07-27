from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "hello_world" / "mlb_historical_v7_learning_cadence_v1.py"
ENTRYPOINT = ROOT / "hello_world" / "mlb_historical_optimizer_v7_recovery_entrypoint.py"
WORKFLOW = ROOT / ".github" / "workflows" / "mlb-historical-supervised-v9-shadow.yml"


def test_shadow_cadence_is_separate_from_canonical_promotion_audit():
    source = PATCH.read_text(encoding="utf-8")
    assert "SHADOW_REFIT_INCREMENT_GAMES = 50" in source
    assert "handler.V7_SHADOW_REFIT_INCREMENT_GAMES" in source
    assert "handler.FRESH_AUDIT_INCREMENT_GAMES =" not in source
    assert "Canonical promotion still requires" in source


def test_challenger_rank_prioritizes_chronological_mean_and_calibration():
    source = PATCH.read_text(encoding="utf-8")
    mean_pos = source.index('metrics.get("meanDailyAccuracy")')
    pass_pos = source.index('metrics.get("dailyPassRate")')
    brier_pos = source.index('metrics.get("brierScore")')
    logloss_pos = source.index('metrics.get("logLoss")')
    assert mean_pos < brier_pos < logloss_pos < pass_pos


def test_recovery_entrypoint_reports_both_cadences_and_no_shadow_authority():
    source = ENTRYPOINT.read_text(encoding="utf-8")
    integrity_pos = source.index("supervised_integrity_v2.install(supervised_v9)")
    supervised_pos = source.index("supervised_v9.install(")
    cadence_pos = source.index("learning_cadence.install(")
    assert integrity_pos < supervised_pos < cadence_pos
    assert '"shadowRefitIncrementGames"' in source
    assert '"canonicalFreshAuditIncrementGames"' in source
    assert '"shadowRefitsMayPromote": False' in source


def test_shadow_workflow_runs_hourly_without_large_config_environment_handoff():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "cron: '17 * * * *'" in source
    assert "CONFIG=\"$config\"" not in source
    assert "/tmp/mlb-historical-lambda-config.json" in source
