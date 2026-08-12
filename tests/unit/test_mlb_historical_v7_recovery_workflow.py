from pathlib import Path


WORKFLOW = Path(".github/workflows/mlb-historical-v7-recovery.yml")
TEMPLATE = Path("mlb_historical_optimizer/template.yaml")


def test_v7_deployment_uses_policy_owned_untouched_audit_cadence():
    source = WORKFLOW.read_text()
    template = TEMPLATE.read_text()

    assert "'HistoricalFreshAuditIncrementGames=200'" in source
    assert "'HistoricalFreshAuditIncrementGames=250'" not in source
    assert (
        "'freshAuditIncrement':env.get("
        "'MLB_HISTORICAL_FRESH_AUDIT_INCREMENT_GAMES') == '200'"
    ) in source
    assert (
        "'freshAuditIncrementGames':env.get("
        "'MLB_HISTORICAL_FRESH_AUDIT_INCREMENT_GAMES')"
    ) in source

    parameter = template.split("  HistoricalFreshAuditIncrementGames:", 1)[1].split(
        "  HistoricalRangeExtensionAuthorized:", 1
    )[0]
    assert "Default: 200" in parameter
    assert "MinValue: 200" in parameter


def test_v7_publisher_preserves_proof_then_cleans_before_branch_switch():
    source = WORKFLOW.read_text()
    publish = source.split("      - name: Publish durable deployment evidence", 1)[1]

    preserve = publish.index(
        'cp "$REPORT_PATH" /tmp/mlb-historical-v7-selective-proof.json'
    )
    loop = publish.index("for attempt in 1 2 3 4 5; do")
    pre_reset = publish.index("git reset --hard HEAD", loop)
    pre_clean = publish.index("git clean -fd", pre_reset)
    fetch = publish.index("git fetch --no-tags origin", pre_clean)
    checkout = publish.index(
        "git checkout -B historical-v7-selective-proof", fetch
    )
    reset_to_main = publish.index(
        "git reset --hard refs/remotes/origin/main", checkout
    )
    restore = publish.index(
        'cp /tmp/mlb-historical-v7-selective-proof.json "$REPORT_PATH"',
        reset_to_main,
    )

    assert (
        preserve
        < loop
        < pre_reset
        < pre_clean
        < fetch
        < checkout
        < reset_to_main
        < restore
    )
    assert publish.count("git reset --hard HEAD") == 1
    assert "git push origin HEAD:main && exit 0" in publish
    assert "sleep $((attempt * 3))" in publish


def test_v7_recovery_workflow_runs_its_own_regression_contract():
    source = WORKFLOW.read_text()
    contract = "tests/unit/test_mlb_historical_v7_recovery_workflow.py"

    assert source.count(contract) >= 2
    assert "cancel-in-progress: false" in source
