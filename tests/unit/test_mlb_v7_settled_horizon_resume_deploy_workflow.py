from pathlib import Path


WORKFLOW = Path(".github/workflows/mlb-v7-settled-horizon-resume-deploy.yml")


def test_v7_settled_horizon_resume_deploy_is_isolated_and_gated():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "group: mlb-historical-optimizer-stack" in source
    assert "group: parlay-platform-deploy" not in source
    assert "cancel-in-progress: false" in source
    assert "[deploy-v7-settled-horizon-resume]" in source
    assert "stack-name \"$HISTORICAL_STACK_NAME\"" in source
    assert "parlay-platform-dev" not in source
    assert "template-file mlb_historical_optimizer/template.yaml" in source
    assert "template-file .aws-sam-v7-settled-horizon/template.yaml" in source
    assert "mlb_historical_optimizer_v7_recovery_entrypoint.lambda_handler" in source
    assert "INQSI_DEPLOY_GIT_SHA" in source


def test_v7_settled_horizon_resume_deploy_preserves_live_parameters():
    source = WORKFLOW.read_text(encoding="utf-8")

    for key in (
        "TargetSnapshotsTable",
        "HistoricalStartDate",
        "HistoricalEndDate",
        "HistoricalTargetGames",
        "HistoricalMaxCredits",
        "HistoricalRequestsPerRun",
        "HistoricalMaxCandidates",
        "HistoricalMaxOptimizationRounds",
        "HistoricalFreshAuditIncrementGames",
        "HistoricalRangeExtensionAuthorized",
    ):
        assert key in source
    assert "missing existing historical parameters" in source
    assert "OddsApiKey=${ODDS_API_KEY_VALUE}" in source
    assert "DeployGitSha=${GITHUB_SHA}" in source


def test_v7_settled_horizon_resume_deploy_fails_closed_at_safe_horizon():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "ZoneInfo('America/New_York')" in source
    assert "timedelta(days=1)" in source
    assert "settled horizon advanced but V7 remained parked" in source
    assert "V7 crossed the safe settled horizon early" in source
    assert "productionAuthorityChanged" in source
    assert "MLB-HISTORICAL-INCREMENTAL-RANGE-EXTENSION-v2-waiting-resume" in source
    assert '"mode":"orchestrate"' in source
