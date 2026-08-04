from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "run_mlb_historical_supervised_v9_shadow_feature_aware.py"


def _module():
    bridge = types.ModuleType("mlb_historical_v7_feature_bridge_v1")
    legacy = types.ModuleType("scripts.run_mlb_historical_supervised_v9_shadow")
    cadence = types.ModuleType("scripts.run_mlb_historical_supervised_v9_shadow_cadence")
    cadence_v3 = types.ModuleType("scripts.run_mlb_historical_supervised_v9_shadow_cadence_v3")
    package = types.ModuleType("scripts")
    package.__path__ = []
    package.run_mlb_historical_supervised_v9_shadow = legacy
    package.run_mlb_historical_supervised_v9_shadow_cadence = cadence
    package.run_mlb_historical_supervised_v9_shadow_cadence_v3 = cadence_v3
    old = {name: sys.modules.get(name) for name in (
        bridge.__name__, package.__name__, legacy.__name__, cadence.__name__, cadence_v3.__name__
    )}
    sys.modules[bridge.__name__] = bridge
    sys.modules[package.__name__] = package
    sys.modules[legacy.__name__] = legacy
    sys.modules[cadence.__name__] = cadence
    sys.modules[cadence_v3.__name__] = cadence_v3
    try:
        spec = importlib.util.spec_from_file_location("feature_aware_under_test", MODULE)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in old.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def test_report_exposes_feature_cadence_without_changing_authority():
    module = _module()
    report = module._augment_report(
        {
            "ok": True,
            "shadowRefitPerformed": False,
            "stalledStage": "WAITING_FOR_50_NEW_ELIGIBLE_GAMES",
            "productionAuthorityChanged": False,
        },
        feature_proof={
            "featureCorpus": {
                "materializedFeatureRowCount": 160,
                "fingerprint": "feature-fp",
            },
            "productionAuthorityChanged": False,
        },
        decision={
            "newFeatureRowsSinceLastShadowFit": 10,
            "newFeatureRowsSinceLastLightweightEvaluation": 10,
            "remainingFeatureRowsUntilShadowRefit": 40,
            "remainingFeatureRowsUntilLightweightEvaluation": 0,
            "refitReasons": [],
            "lightweightReasons": ["feature_row_increment_reached"],
        },
        full_feature_increment=50,
        lightweight_feature_increment=10,
    )
    assert report["featureCorpus"]["materializedFeatureRowCount"] == 160
    assert report["newFeatureRowsSinceLastShadowFit"] == 10
    assert report["stalledStage"] == "WAITING_FOR_NEW_GAMES_FEATURE_ROWS_OR_COMPLETE_SLATES"
    assert report["providerCallsMade"] == 0
    assert report["productionAuthorityChanged"] is False


def test_refit_report_keeps_completed_stage():
    module = _module()
    report = module._augment_report(
        {"ok": True, "shadowRefitPerformed": True, "stalledStage": None},
        feature_proof={"featureCorpus": {}},
        decision={"refitReasons": ["feature_row_increment_reached"]},
        full_feature_increment=50,
        lightweight_feature_increment=10,
    )
    assert report["stalledStage"] is None
    assert report["refitReasons"] == ["feature_row_increment_reached"]


def test_atomic_wrapper_imports_feature_aware_runner():
    source = (ROOT / "scripts" / "run_mlb_historical_supervised_v9_shadow_v2.py").read_text()
    assert "run_mlb_historical_supervised_v9_shadow_feature_aware" in source


def test_main_injects_enriched_records_and_feature_cadence(tmp_path, monkeypatch):
    module = _module()
    output = tmp_path / "report.json"

    class Handler:
        def _load_state(self):
            return {"eligibleGameCount": 2}

        def _load_training_records(self, state):
            assert state["eligibleGameCount"] == 2
            return [{"officialGamePk": "1"}, {"officialGamePk": "2"}]

    handler = Handler()
    runtime = types.ModuleType("mlb_historical_optimizer_v7_recovery_entrypoint")
    runtime.base = types.SimpleNamespace(optimizer_handler=handler)
    repairs = types.ModuleType("mlb_historical_v7_priority_repairs_v1")
    repairs.dataset_fingerprint = lambda records: "raw"
    monkeypatch.setitem(sys.modules, runtime.__name__, runtime)
    monkeypatch.setitem(sys.modules, repairs.__name__, repairs)

    enriched = [
        {"officialGamePk": "1", "homeSignal": {"fundamentals": {"starterQuality": 3}}},
        {"officialGamePk": "2", "homeSignal": {"fundamentals": {"starterQuality": 4}}},
    ]
    module.feature_bridge.load_and_apply = lambda records: (
        enriched,
        {
            "featureCorpus": {
                "materializedFeatureRowCount": 50,
                "fingerprint": "features-50",
            },
            "productionAuthorityChanged": False,
        },
    )
    module.feature_bridge.dataset_fingerprint = lambda records, state: "combined"

    def decide(previous, **kwargs):
        return {
            "shouldRefit": True,
            "shouldLightweight": True,
            "newFeatureRowsSinceLastShadowFit": kwargs.get("feature_count"),
            "newFeatureRowsSinceLastLightweightEvaluation": kwargs.get("feature_count"),
            "remainingFeatureRowsUntilShadowRefit": 0,
            "remainingFeatureRowsUntilLightweightEvaluation": 0,
            "refitReasons": ["feature_row_increment_reached"],
            "lightweightReasons": ["full_refit_required"],
        }

    def anchors(decision, **kwargs):
        return {"lastShadowFitFeatureRowCount": kwargs["feature_count"]}

    module.cadence_state.decide_cadence = decide
    module.cadence_state.report_anchor_fields = anchors
    module.cadence_v3.decide_cadence = decide
    module.cadence_v3.report_anchor_fields = anchors

    def legacy_main():
        loaded = handler._load_training_records({"eligibleGameCount": 2})
        assert loaded is enriched
        assert repairs.dataset_fingerprint(loaded) == "combined"
        decision = module.cadence_state.decide_cadence(
            {},
            current_count=2,
            fingerprint="combined",
            full_increment=50,
            lightweight_increment=25,
        )
        anchor = module.cadence_state.report_anchor_fields(
            decision,
            current_count=2,
            fingerprint="combined",
            shadow_refit_performed=True,
            lightweight_performed=True,
        )
        output.write_text(
            __import__("json").dumps(
                {
                    "ok": True,
                    "shadowRefitPerformed": True,
                    "sourceSha": "sha",
                    "runId": "run",
                    "stalledStage": None,
                    **anchor,
                }
            )
        )
        return 0

    module.legacy.main = legacy_main
    monkeypatch.setattr(sys, "argv", ["runner", "--output", str(output)])
    assert module.main() == 0
    value = __import__("json").loads(output.read_text())
    assert value["featureCorpus"]["materializedFeatureRowCount"] == 50
    assert value["lastShadowFitFeatureRowCount"] == 50
    assert value["refitReasons"] == ["feature_row_increment_reached"]
    assert os.environ["MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED"] == "false"
    assert os.environ["MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED"] == "false"
    assert os.environ["MLB_V8_HISTORICAL_CONTEXT_OVERLAY_ENABLED"] == "true"
    assert os.environ["MLB_V8_HISTORICAL_CONTEXT_OVERLAY_REQUIRED"] == "true"
