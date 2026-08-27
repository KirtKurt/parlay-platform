from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-mlb-auto-prospective.yml"


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_prospective_deploy_invokes_main_runtime_decimal_odds_smoke() -> None:
    source = _source()

    assert "Prove deployed strict decimal-odds decision contract" in source
    assert "--logical-resource-id MLBAutoLLMFunction" in source
    assert (
        "deployment_decimal_odds_decision_smoke" in source
    )
    assert "MLB-AUTO-DEPLOYMENT-DECIMAL-ODDS-SMOKE-v1" in source
    assert "/tmp/mlb-auto-decimal-odds-smoke.json" in source
    assert "/tmp/mlb-auto-decimal-odds-smoke-meta.json" in source


def test_decimal_odds_smoke_fails_deploy_on_function_or_contract_error() -> None:
    source = _source()

    assert "assert meta.get('FunctionError') is None" in source
    assert "DEPLOYMENT_DECIMAL_ODDS_DECISION_VERIFIED" in source
    assert "body.get('decisionAuthority') == 'BEDROCK_LLM'" in source
    assert "body.get('winner') == body.get('marketFavorite')" in source
    assert "body.get('marketFavoritePrice') == 1.411" in source
    assert "body.get('otherTeamPrice') == 1.70" in source
    assert "body.get('marketFavoritePrice') < body.get('otherTeamPrice')" in source
    assert "body.get('mlFallbackAttempted') is False" in source
    assert "body.get('persistenceAttempted') is False" in source
    assert "body.get('cardMutationAttempted') is False" in source
    assert "body.get('historyMutationAttempted') is False" in source
    assert "body.get('writeGuardArmed') is True" in source


def test_prospective_deploy_preserves_accuracy_and_valid_yaml() -> None:
    source = _source()
    document = yaml.load(source, Loader=yaml.BaseLoader)

    assert isinstance(document, dict)
    assert "TargetDailyAccuracy='0.80'" in source
    assert "AWS_ML_RANKED_ENSEMBLE" in source
    assert "INQSI-MLB-v5.0-ranked-winner-v15.10-active-ensemble" in source
