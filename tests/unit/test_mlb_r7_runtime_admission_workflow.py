from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/repair-mlb-r7-runtime-admission-now.yml"


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_r7_runtime_admission_trigger_isolated_from_main_pushes() -> None:
    value = yaml.load(_source(), Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    assert value["name"] == "Repair MLB R7 runtime admission now"
    assert value["permissions"] == {"contents": "read"}
    push = value["on"]["push"]
    assert push["branches"] == ["repair/run-mlb-r7-runtime-admission-20260825"]
    assert push["paths"] == [".github/r7-runtime-admission-trigger"]
    assert "main" not in push["branches"]


def test_r7_runtime_admission_never_deploys_or_resolves_another_sport() -> None:
    source = _source()
    lowered = source.lower()
    forbidden = (
        "sam deploy",
        "sam build",
        "template.yaml",
        "contents: write",
        "git push",
        "soccerschedulerfunction",
        "soccerappapifunction",
        "soccerresultsauditfunction",
        "soccertrainerfunction",
        "tennis",
        "nfl",
    )
    for token in forbidden:
        assert token not in lowered
    assert source.count("--logical-resource-id MLBMLTrainingFunction") == 1
    assert "onlyMlbTrainerResolved': True" in source
    assert "sharedRootStackDeployedByThisWorkflow': False" in source
    assert "otherSportChanged': False" in source


def test_r7_runtime_admission_uses_lease_safe_deployed_trainer() -> None:
    source = _source()
    assert source.count("--retry-execution-lease") == 2
    assert source.count("scripts/invoke_mlb_trainer_with_retry.py") >= 4
    assert "mlb_ml_aws_training_v1_compat.lambda_handler" in source
    assert "MLB_ML_EXPERIMENT_ID" in source
    assert "INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED" in source
    assert "INQSI_MLB_ML_AUTO_PROMOTE" in source


def test_r7_runtime_admission_keeps_all_evidence_gates_closed() -> None:
    source = _source()
    required = (
        "accepted >= 18",
        "train_rows >= 18",
        "{'2026-08-03', '2026-08-24'} <= finalized_dates",
        "first_slate.get('achieved') is True",
        "int(counts.get('integrityClean', 0) or 0) >= 18",
        "not continuity.get('blockedSlateDate')",
        "processed >= yesterday",
        "exact_identity_match",
        "health.get('deploymentIdentityMatches') is True",
        "selection_health.get('deploymentIdentityMatches') is True",
        "latest.get('liveInferenceAuthority') is not True",
        "latest.get('productionAuthorityChanged') is not True",
        "latest.get('automaticPromotionEnabled') is not True",
        "latest.get('championChanged') is not True",
        "historicalGapRelaxed': False",
        "unresolvedDatesFabricated': False",
        "immutablePredictionRewriteAllowed': False",
        "postStartPredictionCreationAllowed': False",
        "directTableWrite': False",
    )
    for token in required:
        assert token in source


def test_r7_runtime_admission_does_not_require_a_champion_or_fake_model() -> None:
    source = _source()
    assert "modelTrained') is True" not in source
    assert "championChanged') is True" not in source
    assert "V15.10" not in source
    assert "automaticPromotionEnabled': False" not in source
