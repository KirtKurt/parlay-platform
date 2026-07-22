from __future__ import annotations

from pathlib import Path


TENNIS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TENNIS_ROOT.parent


def test_tennis_source_has_no_mlb_or_root_runtime_imports():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((TENNIS_ROOT / "src").glob("*.py"))
    ).lower()

    assert "import mlb" not in source
    assert "from mlb" not in source
    assert "hello_world" not in source


def test_tennis_sam_is_a_separate_eventbridge_only_stack():
    template = (TENNIS_ROOT / "template.yaml").read_text(encoding="utf-8")

    assert "CodeUri: src/" in template
    assert template.count("Type: AWS::Events::Rule") == 1
    assert "TennisPullScheduleRule:" in template
    assert "State: !Ref TennisScheduleState" in template
    assert "Default: DISABLED" in template
    assert "cron(0/15 * * * ? *)" in template
    assert "Type: Api" not in template
    assert "ReservedConcurrentExecutions: 1" in template
    assert "TennisPullDeadLetterQueue" in template
    assert "DestinationConfig:" in template
    assert "SQSSendMessagePolicy:" in template
    assert "Input:" not in template
    assert 'TENNIS_PULL_LEAD_HOURS: "8"' in template
    assert "TENNIS_MODEL_STATE: RULE_BASED_SHADOW" in template
    assert "parlay_platform_snapshots" not in template
    assert "parlay_platform_signals" not in template
    assert "hello_world" not in template
    assert "TennisArchiveBucket:" in template
    assert "VersioningConfiguration:" in template
    assert "s3:PutObject" in template
    assert "s3:DeleteObject" not in template
    assert "TennisCoverageAlarm:" in template
    assert "TennisQuotaReserveAlarm:" in template
    assert "TennisPullDeadLetterAgeAlarm:" in template
    assert "RetentionInDays: 30" in template


def test_root_mlb_application_does_not_reference_tennis_stack():
    root_template = (REPO_ROOT / "template.yaml").read_text(encoding="utf-8")

    assert "TennisPullFunction" not in root_template
    assert "TENNIS_SNAPSHOTS_TABLE" not in root_template
    assert "TENNIS_SIGNALS_TABLE" not in root_template


def test_tennis_only_commits_do_not_trigger_the_root_mlb_deploy():
    deploy_workflow = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )

    trigger_block = deploy_workflow.split("permissions:", 1)[0]
    assert "    paths:" in trigger_block
    assert "paths-ignore:" not in trigger_block
    assert '      - "tennis/**"' not in trigger_block
    assert '      - ".github/workflows/deploy.yml"' not in trigger_block
    assert '      - ".github/workflows/deploy-tennis-canary.yml"' not in trigger_block
    assert "  workflow_dispatch:" in trigger_block


def test_bbs_secret_and_authority_are_confined_to_manual_probe_lambda():
    template = (TENNIS_ROOT / "template.yaml").read_text(encoding="utf-8")
    pull = template.split("  TennisPullFunction:\n", 1)[1].split(
        "  TennisResultsProbeFunction:\n", 1
    )[0]
    probe = template.split("  TennisResultsProbeFunction:\n", 1)[1].split(
        "  TennisPullScheduleRule:\n", 1
    )[0]
    parameter = template.split("  TennisBbsApiKey:\n", 1)[1].split(
        "  TennisScheduleState:\n", 1
    )[0]

    assert "NoEcho: true" in parameter
    assert "Default:" not in parameter
    assert "TENNIS_BBS_API_SECRET_ARN" not in pull
    assert "TennisBbsApiSecret" not in pull
    assert "TENNIS_BBS_API_SECRET_ARN: !Ref TennisBbsApiSecret" in probe
    assert "Resource: !Ref TennisBbsApiSecret" in probe
    assert "TENNIS_ODDS_API_SECRET_ARN" not in probe
    assert "DynamoDB" not in probe
    assert "s3:" not in probe
    assert "SQS" not in probe
    assert "Events:" not in probe
    assert "INQSI_DEPLOY_GIT_SHA" not in probe


def test_pull_secret_is_tennis_specific_and_never_plaintext_environment():
    template = (TENNIS_ROOT / "template.yaml").read_text(encoding="utf-8")
    pull = template.split("  TennisPullFunction:\n", 1)[1].split(
        "  TennisResultsProbeFunction:\n", 1
    )[0]

    assert "TENNIS_ODDS_API_SECRET_ARN: !Ref TennisOddsApiSecret" in pull
    assert "ODDS_API_KEY:" not in pull
    assert "Resource: !Ref TennisOddsApiSecret" in pull
    assert "Bbs" not in pull and "BBS" not in pull
