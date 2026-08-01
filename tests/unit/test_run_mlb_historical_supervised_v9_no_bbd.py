from __future__ import annotations

import json
import sys

import scripts.run_mlb_historical_supervised_v9_no_bbd as runner


def _previous(feature_count: int = 100, fingerprint: str = "old"):
    return {
        "state": {"eligibleGameCount": 4000},
        "datasetFingerprint": "games",
        "lastShadowFitEligibleGameCount": 4000,
        "lastShadowFitDatasetFingerprint": "games",
        "lastLightweightEvaluationEligibleGameCount": 4000,
        "lastLightweightEvaluationDatasetFingerprint": "games",
        "lastShadowFitFeatureEligibleGameCount": feature_count,
        "lastShadowFitFeatureFingerprint": fingerprint,
        "lastLightweightFeatureEligibleGameCount": feature_count,
        "lastLightweightFeatureFingerprint": fingerprint,
    }


def test_ten_new_feature_rows_trigger_lightweight_evaluation(monkeypatch):
    monkeypatch.setenv("MLB_V7_FEATURE_REFIT_INCREMENT_ROWS", "50")
    monkeypatch.setenv("MLB_V7_FEATURE_LIGHTWEIGHT_INCREMENT_ROWS", "10")
    runner._CONTEXT_PROOF = {
        "eligibleFeatureGameCount": 110,
        "featureFingerprint": "new",
    }
    decision = runner._feature_decision(
        _previous(),
        current_count=4000,
        fingerprint="games",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["shouldLightweight"] is True
    assert decision["shouldRefit"] is False
    assert decision["featureCadenceTriggeredLightweightEvaluation"] is True


def test_fifty_new_feature_rows_trigger_full_refit(monkeypatch):
    monkeypatch.setenv("MLB_V7_FEATURE_REFIT_INCREMENT_ROWS", "50")
    runner._CONTEXT_PROOF = {
        "eligibleFeatureGameCount": 150,
        "featureFingerprint": "new",
    }
    decision = runner._feature_decision(
        _previous(),
        current_count=4000,
        fingerprint="games",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["shouldRefit"] is True
    assert decision["shouldLightweight"] is True
    assert decision["featureCadenceTriggeredRefit"] is True


def test_no_feature_or_game_change_does_not_refit():
    runner._CONTEXT_PROOF = {
        "eligibleFeatureGameCount": 100,
        "featureFingerprint": "old",
    }
    decision = runner._feature_decision(
        _previous(),
        current_count=4000,
        fingerprint="games",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["shouldRefit"] is False
    assert decision["shouldLightweight"] is False


def test_install_cadence_patches_guarded_runner_namespace():
    runner._install_cadence()
    guarded_namespace = runner.guarded_runner.original.cadence_state
    assert guarded_namespace.decide_cadence is runner._feature_decision
    assert guarded_namespace.report_anchor_fields is runner._feature_anchor_fields


def test_main_postprocesses_original_output_after_guard_rewrites_argv(
    monkeypatch, tmp_path
):
    output = tmp_path / "report.json"
    rewritten = tmp_path / "temporary.json"
    monkeypatch.setattr(sys, "argv", ["runner", "--output", str(output)])
    monkeypatch.setattr(runner, "_install_record_bridge", lambda: None)
    monkeypatch.setattr(runner, "_install_cadence", lambda: None)
    runner._CONTEXT_PROOF = {
        "eligibleFeatureGameCount": 17,
        "featureFingerprint": "features",
        "providerCallsMade": 0,
        "liveBbdApiRequired": False,
    }

    def guarded_main():
        sys.argv[sys.argv.index("--output") + 1] = str(rewritten)
        output.write_text(
            json.dumps(
                {
                    "ok": True,
                    "shadowRefitPerformed": False,
                    "productionAuthorityChanged": False,
                }
            )
        )
        return 0

    monkeypatch.setattr(runner.guarded_runner, "main", guarded_main)
    assert runner.main() == 0
    value = json.loads(output.read_text())
    assert value["liveBbdApiAvailable"] is False
    assert value["liveBbdApiRequired"] is False
    assert value["providerCallsMade"] == 0
    assert value["contextBridge"]["eligibleFeatureGameCount"] == 17
    assert value["stalledStage"] == "WAITING_FOR_NEW_ELIGIBLE_GAMES_OR_FEATURE_ROWS"
    assert value["cadenceWaitIsOperationalFailure"] is False
