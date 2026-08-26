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


def test_repository_has_one_automatic_unified_mlb_training_owner():
    result = ownership.verify(Path("."))
    assert result["ok"] is True, result["errors"]
    assert result["automaticTrainerOwner"] == "AWS_EVENTBRIDGE_SCHEDULE"
    assert result["deploymentInvokesTraining"] is False
    assert result["githubScheduledRecoveryEnabled"] is False
    assert result["immutablePredictionRewriteAllowed"] is False
    assert result["postStartPredictionCreationAllowed"] is False
    assert result["automaticPromotionEnabled"] is False
    assert result["productionAuthorityChanged"] is False
    assert result["otherSportChanged"] is False
