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


def test_repository_has_one_automatic_unified_mlb_training_owner():
    result = ownership.verify(Path("."))
    assert result["ok"] is True, result["errors"]
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
