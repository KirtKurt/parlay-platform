from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"


def _load_api(runtime: dict, *, predictions=None):
    module_name = "mlb_v3_read_api_historical_contract_test"
    path = HELLO / "mlb_v3_read_api.py"
    engine = SimpleNamespace(
        MLB_ML_RUNTIME_INSTALL_V3=runtime,
        MLB_RANKED_WINNER_VERSION=runtime.get("rankedWinnerVersion", "ranked-v15.10"),
        MLB_RANKED_WINNER_POLICY_VERSION=runtime.get("rankedWinnerPolicyVersion", "ranked-policy"),
        MODEL_VERSION="diagnostic-engine-model",
        ENGINE="diagnostic-engine",
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


def test_read_api_reports_historical_model_and_no_fallback_after_cutover():
    api = _load_api(_runtime(active=True), predictions=[{"selectedTeam": "A"}])
    model = api._model_body()
    assert model["ok"] is True
    assert model["model_version"] == api.HISTORICAL_MODEL_VERSION
    assert model["historicalProductionCutoverActive"] is True
    assert model["productionAuthoritySource"] == "mlb_historical_daily_champion_only"
    assert model["incumbentProductionAuthorityDestroyed"] is True
    assert model["legacyFallbackAllowed"] is False
    assert model["automaticLegacyRestoreAllowed"] is False
    assert model["automaticWagerAllowed"] is False
    assert model["predictionOnlyWagerSafetyInstalled"] is True
    assert model["rowLevelAutomaticWagerAllowed"] is False

    response = api.lambda_handler(
        {"path": "/v1/mlb/predictions", "queryStringParameters": {"date": "2026-07-24"}},
        None,
    )
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["model_version"] == api.HISTORICAL_MODEL_VERSION
    assert body["historicalProductionCutoverActive"] is True
    assert body["incumbentProductionAuthorityDestroyed"] is True
    assert body["legacyFallbackAllowed"] is False
    assert body["automaticWagerAllowed"] is False
    assert body["predictionOnlyWagerSafetyInstalled"] is True
    assert body["rowLevelAutomaticWagerAllowed"] is False


def test_read_api_keeps_incumbent_only_in_coherent_pre_cutover_state():
    api = _load_api(_runtime(active=False))
    model = api._model_body()
    assert model["ok"] is True
    assert model["model_version"] == api.MODEL_VERSION
    assert model["historicalDailyChampionActive"] is False
    assert model["historicalProductionCutoverActive"] is False
    assert model["incumbentRole"] == "active_until_historical_gate"
    assert model["apiRuntimeVersion"] == "MLB-V3-READ-API-v6-ranked-winner-v15.10"
    assert model["productionAuthoritySource"] == "mlb_ranked_winner_v15_10_active_ensemble"
    assert model["historicalApiExtensionVersion"] == api.HISTORICAL_API_EXTENSION_VERSION


def test_read_api_fails_closed_when_historical_authority_state_is_incoherent():
    api = _load_api(_runtime(active=False, coherent=False))
    model = api._model_body()
    assert model["ok"] is False
    response = api.lambda_handler({"path": "/v1/mlb/model/version"}, None)
    assert response["statusCode"] == 503
