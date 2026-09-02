import re
from pathlib import Path

from scripts import verify_unified_mlb_learning_ownership as ownership


def test_trigger_parser_distinguishes_scheduled_and_path_scoped_main_push():
    scheduled = '''name: example
"on":
  schedule:
    - cron: "40 * * * *"
  workflow_dispatch:

jobs: {}
'''
    path_scoped = '''name: example
"on":
  workflow_dispatch:
  push:
    branches: [main]
    paths:
      - ".github/workflows/example.yml"

jobs: {}
'''
    assert ownership._scheduled(ownership._trigger_block(scheduled)) is True
    assert ownership._main_push_enabled(ownership._trigger_block(scheduled)) is False
    assert ownership._scheduled(ownership._trigger_block(path_scoped)) is False
    assert ownership._main_push_enabled(ownership._trigger_block(path_scoped)) is True


def test_transitive_dispatch_verifier_detects_every_automatic_trigger(tmp_path: Path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    trainer = workflows / "trainer.yml"
    trainer.write_text(
        '''name: trainer
"on":
  workflow_dispatch:

concurrency:
  group: unified-mlb-learning
  cancel-in-progress: false

jobs:
  train:
    runs-on: ubuntu-latest
    steps:
      - run: >-
          python scripts/invoke_mlb_trainer_with_retry.py
          --payload '{"sport":"mlb","mode":"scheduled"}'
''',
        encoding="utf-8",
    )
    middle = workflows / "middle.yml"
    middle.write_text(
        '''name: middle
"on":
  workflow_dispatch:

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - run: gh workflow run trainer.yml
''',
        encoding="utf-8",
    )
    scheduled = workflows / "scheduled.yml"
    scheduled.write_text(
        '''name: scheduled
"on":
  schedule:
    - cron: "7 * * * *"

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - run: gh workflow run middle.yml
''',
        encoding="utf-8",
    )
    workflow_run = workflows / "workflow-run.yml"
    workflow_run.write_text(
        '''name: workflow run
"on":
  workflow_run:
    workflows: [upstream]
    types: [completed]

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - run: gh workflow run trainer.yml
''',
        encoding="utf-8",
    )
    push = workflows / "push.yml"
    push.write_text(
        '''name: push
"on":
  push:
    branches: [main]
    paths: [".github/workflows/push.yml"]

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - run: gh workflow run trainer.yml
''',
        encoding="utf-8",
    )

    assert ownership._automatic_training_dispatch_chains(
        workflows.glob("*.yml")
    ) == [
        "push:push.yml->trainer.yml",
        "schedule:scheduled.yml->middle.yml->trainer.yml",
        "workflow_run:workflow-run.yml->trainer.yml",
    ]


def test_training_detector_recognizes_plain_and_shell_escaped_payloads():
    trainer = "python scripts/invoke_mlb_trainer_with_retry.py"
    plain = trainer + """ --payload '{"sport":"mlb","mode":"scheduled"}'"""
    shell_escaped = (
        trainer
        + r''' --payload "{\"sport\":\"mlb\",\"mode\":\"SCHEDULED\"}"'''
    )

    assert ownership._invokes_training(plain) is True
    assert ownership._invokes_training(shell_escaped) is True


def test_bootstrap_is_manual_only_and_never_uploads_raw_lambda_configuration():
    workflow_path = Path(
        ".github/workflows/bootstrap-mlb-historical-live-r8.yml"
    )
    text = workflow_path.read_text(encoding="utf-8")
    trigger = ownership._trigger_block(text)

    assert ownership._workflow_dispatch_enabled(trigger) is True
    assert ownership._automatic_trigger_types(trigger) == []
    assert ownership._invokes_training(text) is True

    resolve_step = text.split(
        "- name: Resolve and attest the exact R8 trainer runtime", 1
    )[1].split("- name: Capture the exact pre-bootstrap R8 baseline", 1)[0]
    lambda_config = resolve_step.split(
        "aws lambda get-function-configuration", 1
    )[1]
    query = lambda_config.split("--query", 1)[1].split("--output json", 1)[0]
    projected_fields = set(re.findall(r"([A-Za-z0-9_]+):", query))
    assert projected_fields == {
        "FunctionName",
        "State",
        "LastUpdateStatus",
        "Handler",
        "Timeout",
        "MLB_ML_EXPERIMENT_ID",
        "MLB_ML_RELEASE_CONTRACT_ID",
        "MLB_ML_RELEASE_CUTOFF_UTC",
        "MLB_R7_HISTORICAL_TRAINING_ENABLED",
        "MLB_R7_HISTORICAL_MAX_ROWS",
    }
    assert "Environment:Environment" not in query
    assert "Variables:Environment.Variables" not in query
    assert "ODDS_API_KEY" not in query
    assert "/tmp/mlb-r8-bootstrap/trainer-config.json" not in text
    assert (
        'RUNTIME_CONFIG_TMP="$(mktemp /tmp/mlb-r8-runtime-config.'
        in resolve_step
    )
    assert 'trap \'rm -f -- "$RUNTIME_CONFIG_TMP"\' EXIT' in resolve_step
    assert '--output json > "$RUNTIME_CONFIG_TMP"' in resolve_step
    assert "path: /tmp/mlb-r8-bootstrap" in text


def test_recurring_cadence_never_uploads_raw_lambda_configuration():
    text = Path(
        ".github/workflows/prove-mlb-r8-recurring-cadence.yml"
    ).read_text(encoding="utf-8")

    assert "get-function-configuration" not in text
    assert "/tmp/mlb-r8-cadence/trainer-config.json" not in text
    assert "path: /tmp/mlb-r8-cadence" in text


def test_deploy_is_verify_only_and_never_invokes_training():
    deploy = Path(".github/workflows/deploy.yml").read_text(encoding="utf-8")

    assert ownership._invokes_training(deploy) is False
    assert "UNIFIED_MLB_LEARNING_OWNER=eventbridge_schedule" in deploy


def test_repository_has_one_automatic_unified_mlb_training_owner():
    result = ownership.verify(Path("."))
    assert result["ok"] is True, result["errors"]
    assert result["recoveryManualOnly"] is True
    assert result["productionAuthorityChanged"] is False
    assert result["automaticTrainerOwner"] == "AWS_EVENTBRIDGE_SCHEDULE"
    assert result["deploymentInvokesTraining"] is False
    assert result["githubScheduledRecoveryEnabled"] is False
    assert result["githubWorkflowRunRecoveryEnabled"] is False
    assert result["recoveryManualOnly"] is True
    assert result["recoveryPushSelfPathOnly"] is False
    assert result["automaticTrainerDispatchChains"] == []
    assert result["immutablePredictionRewriteAllowed"] is False
    assert result["postStartPredictionCreationAllowed"] is False
    assert result["automaticPromotionEnabled"] is False
    assert result["productionAuthorityChanged"] is False
    assert result["otherSportChanged"] is False
