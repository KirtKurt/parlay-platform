from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-tennis-canary.yml"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_tennis_canary_has_an_isolated_trigger_stack_region_and_concurrency():
    workflow = _workflow()

    assert "      - codex/tennis-ml-structure" in workflow
    assert "  workflow_dispatch:" in workflow
    assert "group: parlay-platform-tennis-dev-canary" in workflow
    assert "group: parlay-platform-deploy\n" not in workflow
    assert "TENNIS_STACK_NAME: parlay-platform-tennis-dev" in workflow
    assert "AWS_REGION: us-east-1" in workflow
    assert "working-directory: tennis" in workflow


def test_tennis_canary_never_uses_the_mlb_odds_secret_or_stack():
    workflow = _workflow()

    assert "secrets.TENNIS_ODDS_API_KEY" in workflow
    assert "secrets.ODDS_API_KEY" not in workflow
    assert (
        'parameter_overrides+=("TennisOddsApiKey=${TENNIS_ODDS_API_KEY_VALUE}")'
        in workflow
    )
    assert '"TennisBbsApiKey=${BBS_API_KEY_VALUE}"' in workflow
    assert '"TennisScheduleState=DISABLED"' in workflow
    assert '"DeployGitSha=${GITHUB_SHA}"' in workflow
    assert '"DeployTemplateSha256=${DEPLOY_TEMPLATE_SHA256}"' in workflow
    assert "--stack-name parlay-platform-dev" in workflow
    assert "--template-file .aws-sam/build/template.yaml" in workflow
    assert '--stack-name "$TENNIS_STACK_NAME"' in workflow


def test_tennis_canary_inspects_before_execution_and_proves_isolation():
    workflow = _workflow()

    create_index = workflow.index("--no-execute-changeset")
    inspect_index = workflow.index("describe-change-set")
    execute_index = workflow.index("execute-change-set")
    assert create_index < inspect_index < execute_index
    assert (
        "cmp --silent /tmp/mlb-stack-before.sha256 /tmp/mlb-stack-after.sha256"
        in workflow
    )
    assert "if: ${{ always() }}" in workflow
    assert "test -f /tmp/mlb-stack-before.sha256" in workflow
    assert 'test "$rule_state" = "DISABLED"' in workflow
    assert "TennisPullFunctionArn" in workflow
    assert '--payload \'{"sport":"tennis","mode":"canary_health"}\'' in workflow
    assert '"network_calls": 0' in workflow
    assert "TennisResultsProbeFunctionArn" in workflow
    assert (
        '--payload \'{"provider":"bbs","sport":"tennis","action":"probe"}\'' in workflow
    )
    for status in (
        "READY",
        "UNSUPPORTED",
        "RESULT_ROUTE_UNVERIFIED",
        "CONTRACT_INCOMPLETE",
    ):
        assert f'"{status}"' in workflow


def test_tennis_canary_validates_builds_and_tests_before_deploying():
    workflow = _workflow()

    test_index = workflow.index("python -m pytest -q tests")
    validate_index = workflow.index("sam validate --lint --template-file template.yaml")
    build_index = workflow.index("sam build --no-cached --template-file template.yaml")
    deploy_index = workflow.index("sam deploy")
    assert test_index < validate_index < build_index < deploy_index
