from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
EXPECTED_API_VERSION = "MLB-V3-READ-API-v7-exact-persisted-prelock-public-read"
EXPECTED_AUTHORITY_CONTRACT_VERSION = "MLB-AUTO-R7-QUALIFIED-CHAMPION-ONLY-v1"


def _load_api(runtime: dict, *, predictions=None):
    module_name = "mlb_v3_read_api_historical_contract_test"
    path = HELLO / "mlb_v3_read_api.py"
    engine = SimpleNamespace(
        MLB_ML_RUNTIME_INSTALL_V3=runtime,
        MLB_RANKED_WINNER_VERSION=runtime.get("rankedWinnerVersion", "ranked-v15.10"),
        MLB_RANKED_WINNER_POLICY_VERSION=runtime.get("rankedWinnerPolicyVersion", "ranked-policy"),
        MODEL_VERSION="diagnostic-engine-model",
        ENGINE="diagnostic-engine",
        history=SimpleNamespace(PULLS=None),
        read_persisted_predictions=lambda *args, **kwargs: {
            "predictions": list(predictions or []),
            "count": len(predictions or []),
        },
    )
    originals = {
        name: sys.modules.get(name)
        for name in (
            "mlb_ml_runtime_install_v3",
            "mlb_game_winner_engine",
            "mlb_ml_optimization_v3",
        )
    }
    try:
        sys.modules["mlb_ml_runtime_install_v3"] = SimpleNamespace(
            install=lambda: runtime
        )
        sys.modules["mlb_game_winner_engine"] = engine
        sys.modules["mlb_ml_optimization_v3"] = SimpleNamespace(
            VERSION="optimizer-diagnostic"
        )
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in originals.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _runtime(*, active: bool, coherent: bool = True) -> dict:
    """Legacy/historical runtime fixture kept to prove it cannot regain authority."""
    return {
        "ok": coherent,
        "version": "runtime",
        "historicalRuntimeExtensionVersion": "historical-runtime",
        "steps": {
            "rankedWinnerV15_10SelectionInstalled": True,
            "historicalAuthorityStateCoherent": coherent,
        },
        "rankedWinnerVersion": "ranked-v15.10",
        "rankedWinnerPolicyVersion": "ranked-policy",
        "historicalDailyChampionOutermostAuthorityInstalled": True,
        "historicalDailyChampionActive": active,
        "historicalDailyPolicyVersion": "historical-policy-v1.3",
        "historicalDailyChampionLoadStatus": {
            "status": "ACTIVE" if active else "ABSENT"
        },
        "historicalProductionCutoverActive": active,
        "historicalProductionCutoverStatus": {
            "status": "ACTIVE" if active else "ABSENT"
        },
        "historicalProductionCutoverVersion": "cutover-v2",
        "predictionOnlyWagerSafetyInstalled": True,
        "rowLevelAutomaticWagerAllowed": False,
        "productionAuthoritySource": (
            "mlb_historical_daily_champion_only"
            if active
            else "mlb_ranked_winner_v15_10_active_ensemble"
        ),
    }


def _assert_r7_fail_closed(model: dict) -> None:
    assert model["ok"] is False
    assert model["status"] == "NO_QUALIFIED_CHAMPION"
    assert model["error"] == "NO_QUALIFIED_CHAMPION"
    assert model["publicationClosed"] is True
    assert model["productionSelectionAllowed"] is False
    assert model["model_version"] is None
    assert model["primaryAlgorithm"] is None
    assert model["primaryAlgorithmActive"] is False
    assert model["soleProductionAlgorithm"] is None
    assert model["requestedAuthority"] == "AWS_ML_PROSPECTIVE_R7"
    assert model["qualifiedChampionRequired"] is True
    assert model["qualifiedChampionPresent"] is False
    assert model["r7ChampionQualified"] is False
    assert model["r7DeploymentIdentity"] is None
    assert model["legacyFallbackAllowed"] is False
    assert model["automaticLegacyRestoreAllowed"] is False
    assert model["legacyRecommendationAuthority"] is False
    assert model["retiredAuthoritySuppressed"] is True
    assert model["retiredV15_10Eligible"] is False
    assert model["automaticWagerAllowed"] is False
    assert model["rowLevelAutomaticWagerAllowed"] is False
    assert model["parlaysEnabled"] is False
    assert model["readOnly"] is True
    assert model["apiRuntimeVersion"] == EXPECTED_API_VERSION
    assert model["authorityContractVersion"] == EXPECTED_AUTHORITY_CONTRACT_VERSION


def test_read_api_never_restores_historical_authority_after_r7_fail_closed_cutover():
    api = _load_api(_runtime(active=True), predictions=[{"selectedTeam": "A"}])
    model = api._model_body()
    _assert_r7_fail_closed(model)

    response = api.lambda_handler(
        {"path": "/v1/mlb/predictions", "queryStringParameters": {"date": "2026-07-24"}},
        None,
    )
    assert response["statusCode"] == 503
    body = json.loads(response["body"])
    _assert_r7_fail_closed(body)
    assert body["winner_predictions"] == []
    assert body["predictions"] == []
    assert body["count"] == 0


def test_read_api_never_restores_retired_v15_10_in_legacy_pre_cutover_fixture():
    api = _load_api(_runtime(active=False))
    model = api._model_body()
    _assert_r7_fail_closed(model)

    response = api.lambda_handler({"path": "/v1/mlb/model/version"}, None)
    assert response["statusCode"] == 503
    body = json.loads(response["body"])
    _assert_r7_fail_closed(body)


def test_read_api_stays_fail_closed_when_legacy_historical_state_is_incoherent():
    api = _load_api(_runtime(active=False, coherent=False))
    model = api._model_body()
    _assert_r7_fail_closed(model)
    response = api.lambda_handler({"path": "/v1/mlb/model/version"}, None)
    assert response["statusCode"] == 503
