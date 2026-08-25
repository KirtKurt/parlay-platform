from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/repair-mlb-training-continuity-now.yml"


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_r7_continuity_workflow_is_dispatch_only_and_read_only() -> None:
    value = yaml.load(_source(), Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    assert value["name"] == "Repair MLB training continuity safely"
    assert set(value["on"]) == {"workflow_dispatch"}
    assert value["permissions"] == {"contents": "read"}
    assert set(value["jobs"]) == {"reconcile-and-train"}


def test_r7_continuity_workflow_never_deploys_shared_stack_or_other_sport() -> None:
    source = _source()
    lowered = source.lower()
    forbidden = (
        "sam deploy",
        "sam build",
        "template.yaml",
        "contents: write",
        "git push",
        "odds_api_key_value",
        "inqsi_admin_api_token_value",
        "soccerschedulerfunction",
        "soccerappapifunction",
        "soccerresultsauditfunction",
        "soccersignalapifunction",
    )
    for token in forbidden:
        assert token not in lowered
    assert "sharedRootStackDeployedByThisWorkflow'] = False" in source
    assert "otherSportFunctionResolved'] = False" in source


def test_r7_continuity_workflow_resolves_only_mlb_functions() -> None:
    source = _source()
    for logical_id in (
        "MLBDailyPickLockFunction",
        "MLBMLTrainingFunction",
        "MLBV3ReadFunction",
    ):
        assert logical_id in source
    assert "mlb_daily_pick_lock_protected.lambda_handler" in source
    assert "mlb_ml_aws_training_v1_compat.lambda_handler" in source
    assert "mlb_v3_read_api.lambda_handler" in source


def test_r7_continuity_acceptance_remains_fail_closed() -> None:
    source = _source()
    required = (
        "accepted > 0",
        "not continuity.get('blockedSlateDate')",
        "processed >= yesterday",
        "latest.get('liveInferenceAuthority') is not True",
        "latest.get('productionAuthorityChanged') is not True",
        "latest.get('automaticPromotionEnabled') is not True",
        "deploymentIdentityMatches') is True",
        "immutablePredictionRewriteAllowed': False",
        "postStartPredictionCreationAllowed': False",
        "--retry-execution-lease",
    )
    for token in required:
        assert token in source
