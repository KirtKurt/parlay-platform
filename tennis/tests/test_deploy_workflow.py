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
    assert "group: parlay-platform-tennis-canary-dev" in workflow
    assert "group: parlay-platform-deploy\n" not in workflow
    assert "TENNIS_STACK_NAME: parlay-platform-tennis-canary-dev" in workflow
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


def test_tennis_canary_requires_latest_successful_ancestor_mlb_deploy_run():
    workflow = _workflow()

    assert "actions: read" in workflow
    preflight = workflow.split(
        "Require latest successful push-triggered MLB deployment", 1
    )[1].split("Create but do not execute tennis change set", 1)[0]
    assert "GITHUB_TOKEN: ${{ github.token }}" in preflight
    assert "/actions/workflows/deploy.yml/runs" in preflight
    assert "-f branch=main" in preflight
    assert "-f event=push" in preflight
    assert "-f per_page=1" in preflight
    assert '"status": "completed"' in preflight
    assert '"conclusion": "success"' in preflight
    assert '"path": ".github/workflows/deploy.yml"' in preflight
    assert "git fetch --no-tags origin" in preflight
    assert 'git cat-file -e "${run_head_sha}^{commit}"' in preflight
    assert 'git merge-base --is-ancestor "$run_head_sha" origin/main' in preflight
    assert "runtime_reports-only commits" in preflight
    assert 'test "$run_head_sha" = "$main_tip"' not in preflight
    assert "aws lambda invoke" not in preflight
    assert "aws cloudformation" not in preflight

    before = workflow.index("Fingerprint production MLB stack before tennis deployment")
    preflight_index = workflow.index(
        "Require latest successful push-triggered MLB deployment"
    )
    change_set = workflow.index("Create but do not execute tennis change set")
    assert before < preflight_index < change_set


def test_tennis_canary_never_deletes_failed_stack_resources_and_fails_closed():
    workflow = _workflow()

    assert (
        "Recover only the immediately prior failed disabled tennis canary"
        not in workflow
    )
    for operation in (
        "cloudformation delete-stack",
        "dynamodb delete-table",
        "s3api delete-bucket",
        "secretsmanager delete-secret",
        "logs delete-log-group",
    ):
        assert operation not in workflow
    assert (
        "An error occurred (ValidationError) when calling the DescribeStacks operation:"
        in workflow
    )
    assert "Stack with id $TENNIS_STACK_NAME does not exist" in workflow
    assert "Unable to prove the fresh tennis canary stack is absent" in workflow
    assert "Existing tennis canary schedule is not disabled" in workflow
    assert 'os.environ["TENNIS_STACK_NAME"]' in workflow
    assert "2>/dev/null" not in workflow
    assert "|| echo STACK_MISSING" not in workflow


def test_tennis_canary_proves_mlb_runtime_continuity_without_invoking_mlb():
    workflow = _workflow()

    step = workflow.split(
        "Prove MLB scheduled-pull continuity without invoking MLB", 1
    )[1].split("Fingerprint production MLB stack after tennis deployment", 1)[0]
    assert "MLBAuditedPullFunction" in step
    assert "aws logs describe-log-streams" in step
    assert "aws cloudwatch get-metric-statistics" in step
    assert "Invocations Errors Throttles" in step
    assert "aws lambda invoke" not in step
