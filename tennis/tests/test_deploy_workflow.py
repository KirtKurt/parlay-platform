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


def test_failed_initial_canary_recovery_is_identity_bound_and_empty_only():
    workflow = _workflow()

    assert (
        "Recover only the immediately prior failed disabled tennis canary" in workflow
    )
    assert '"DeployGitSha": "24aaf52651294b2a2d478dd5e49b516ae3149210"' in workflow
    assert (
        "8cd7b8d864c60e29638261f85809879773e6694628d2d7c833e05c7c85e7c2cc" in workflow
    )
    assert '"TennisScheduleState": "DISABLED"' in workflow
    assert "reviewed sqs:CreateQueue denial" in workflow
    assert '"TennisSnapshotsTable": "AWS::DynamoDB::Table"' in workflow
    assert '"TennisBbsApiSecret": "AWS::SecretsManager::Secret"' in workflow
    assert "All five retained resources are exact, tennis-tagged" in workflow
    assert "aws dynamodb list-tags-of-resource" in workflow
    assert "aws s3api get-bucket-tagging" in workflow
    assert "aws secretsmanager describe-secret" in workflow
    assert "Refusing to delete nonempty failed-canary table" in workflow
    assert "Refusing to delete a nonempty failed-canary archive bucket" in workflow
    assert "--recovery-window-in-days 7" in workflow
    assert "--force-delete-without-recovery" not in workflow

    recovery = workflow.split(
        "Recover only the immediately prior failed disabled tennis canary", 1
    )[1].split("Create but do not execute tennis change set", 1)[0]
    assert recovery.index("aws dynamodb delete-table") < recovery.index(
        "aws cloudformation delete-stack"
    )


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
