from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_ml_canonical_continuity_v3 as continuity
import mlb_ml_deployment_identity_v1 as identity
import mlb_ml_llm_hypothesis_v1 as llm
import mlb_ml_v2_inference_consumer as consumer


def _row(game_pk: str, *, slate_date: str = "2026-08-05"):
    commence = datetime(2026, 8, 5, 23, 0, tzinfo=timezone.utc)
    lock_at = commence - timedelta(minutes=45)
    source_at = lock_at - timedelta(seconds=10)
    return {
        "officialGamePk": game_pk,
        "slateDateEt": slate_date,
        "commenceTime": commence.isoformat(),
        "lockedAtUtc": lock_at.isoformat(),
        "predictionSourcePullAt": source_at.isoformat(),
        "lockedPrediction": True,
        "officialPrediction": True,
        "lockedAmericanOdds": -115,
        "actualWinner": "Home",
        "settled": True,
        "homeTeam": "Home",
        "awayTeam": "Away",
        "frozenFeatureVector": {
            "officialGamePk": game_pk,
            "gameId": game_pk,
            "lockAtUtc": lock_at.isoformat(),
            "sourcePullAtUtc": source_at.isoformat(),
            "fingerprint": "a" * 64,
            "features": {"selected_probability": 0.57, "movement_180m": 0.01},
        },
    }


def _authority(slate_date: str, game_pks):
    return {
        "slateDateEt": slate_date,
        "slateFinalized": True,
        "officialGamePks": list(game_pks),
        "officialGameCount": len(game_pks),
    }


def test_continuity_quarantines_one_day_and_accepts_later_exact_slate():
    authorities = {
        "2026-08-04": None,
        "2026-08-05": _authority("2026-08-05", ["2", "3"]),
    }
    rows = {
        "2026-08-04": [],
        "2026-08-05": [_row("2"), _row("3")],
    }
    proof = continuity.scan_independent_slates(
        start_date_et="2026-08-04",
        end_date_et="2026-08-05",
        authority_loader=authorities.get,
        row_loader=lambda value: rows[value],
    )
    assert proof["ok"] is True
    assert proof["unresolvedSlateStopsLaterEvaluation"] is False
    assert proof["quarantinedSlateDates"] == ["2026-08-04"]
    assert proof["acceptedSlateDates"] == ["2026-08-05"]
    assert proof["acceptedRowCount"] == 2


def test_exact_slate_rejects_missing_extra_or_duplicate_eligible_rows():
    result = continuity.evaluate_exact_slate(
        slate_date_et="2026-08-05",
        authority=_authority("2026-08-05", ["2", "3"]),
        rows=[_row("2"), _row("2"), _row("4")],
    )
    assert result.accepted is False
    assert "3" in result.missing_game_pks
    assert "4" in result.extra_game_pks
    assert "2" in result.quarantined_game_pks


def test_training_envelope_requires_t45_features_odds_and_outcome():
    row = _row("2")
    envelope = continuity.build_training_envelope(row)
    assert envelope["eligible"] is True
    assert envelope["officialGamePk"] == "2"
    assert len(envelope["fingerprint"]) == 64

    broken = dict(row)
    broken.pop("lockedAmericanOdds")
    broken["settled"] = False
    broken.pop("actualWinner")
    errors = continuity.training_envelope_errors(broken)
    assert "missing_selected_side_locked_odds" in errors
    assert "missing_final_settlement" in errors
    assert "missing_outcome_winner" in errors


def test_deployment_identity_requires_one_sha_and_template_hash(monkeypatch):
    monkeypatch.setenv("INQSI_DEPLOY_GIT_SHA", "a" * 40)
    monkeypatch.setenv("INQSI_DEPLOY_TEMPLATE_SHA256", "b" * 64)
    monkeypatch.setenv("INQSI_DEPLOY_RUN_ID", "123")
    current = identity.current_identity()
    assert current["valid"] is True
    assert identity.matches_current(current) is True
    proof = identity.component_proof(
        training=current,
        selection_capture=current,
        live_inference=current,
    )
    assert proof["ok"] is True


def test_v2_consumer_preserves_prediction_without_valid_champion(monkeypatch):
    monkeypatch.setattr(consumer, "load_active_champion", lambda **_: (None, {"ok": False, "installed": True}))
    payload = {"predictions": [{"predictedWinner": "A"}]}
    result = consumer.apply_to_payload(payload)
    assert result["predictions"][0]["predictedWinner"] == "A"
    assert result["mlbV2InferenceConsumerStatus"]["installed"] is True


def test_v2_consumer_applies_only_gate_promoted_direction_model(monkeypatch):
    monkeypatch.setenv("INQSI_DEPLOY_GIT_SHA", "a" * 40)
    monkeypatch.setenv("INQSI_DEPLOY_TEMPLATE_SHA256", "b" * 64)
    champion = {
        "promotionPassed": True,
        "directionAuthorityEnabled": True,
        "liveInferenceAuthority": True,
        "immutable": True,
        "deploymentIdentity": identity.current_identity(),
        "outcomeModel": {
            "intercept": 0.0,
            "coefficients": {"selected_probability": 4.0},
        },
    }
    assert consumer.champion_errors(champion) == []
    row = {
        "homeTeam": "Home",
        "awayTeam": "Away",
        "predictedWinner": "Away",
        "frozenFeatureVector": {"features": {"selected_probability": 0.6}},
    }
    result = consumer.apply_to_prediction(row, champion)
    assert result["predictedWinner"] == "Home"
    assert result["mlbV2InferenceConsumer"]["applied"] is True


class _Bedrock:
    def converse(self, **kwargs):
        hypotheses = [
            {
                "name": "Reversal exhaustion after persistent agreement",
                "rationale": "Test whether repeated reversals lose meaning when agreement remains persistent.",
                "regimePredicates": [{"feature": "book_agreement_rate", "operator": "gte", "value": 0.8}],
                "interactions": [
                    {
                        "features": ["reversal_count_180m", "book_agreement_rate"],
                        "operator": "and",
                    }
                ],
                "timingWindows": ["180m", "60m"],
                "modelFamilies": ["elastic_net_logistic", "gradient_boosted_trees"],
            }
        ]
        return {"output": {"message": {"content": [{"text": json.dumps(hypotheses)}]}}}


def test_llm_hypothesis_layer_is_real_bedrock_bounded_and_shadow_only(monkeypatch):
    monkeypatch.setenv("MLB_LLM_HYPOTHESIS_ENABLED", "true")
    report = llm.generate_hypotheses(
        {"weakPatterns": ["high reversal count"]},
        client=_Bedrock(),
        model_id="test-model",
    )
    assert report["ok"] is True
    assert report["hypothesisCount"] == 1
    hypothesis = report["hypotheses"][0]
    assert hypothesis["productionAuthority"] is False
    assert hypothesis["requiresWalkForwardValidation"] is True
    assert hypothesis["eligibleForCandidate"] is False


def test_llm_rejects_outcome_leakage_and_unapproved_features():
    errors = llm.hypothesis_errors(
        {
            "name": "bad",
            "rationale": "bad",
            "regimePredicates": [],
            "interactions": [
                {"features": ["actual_winner"], "operator": "and"}
            ],
            "timingWindows": [],
            "modelFamilies": ["logistic"],
        }
    )
    assert any("feature_not_allowed" in value for value in errors)
    assert any("forbidden_feature" in value for value in errors)


def test_source_contract_declares_autonomy_without_tennis_or_soccer_changes():
    template = (ROOT / "template.yaml").read_text(encoding="utf-8")
    trainer = (HELLO / "mlb_ml_aws_training_v1.py").read_text(encoding="utf-8")
    experiment = (HELLO / "mlb_ml_experiment_v2.py").read_text(encoding="utf-8")
    runtime = (HELLO / "mlb_ml_runtime_install_v3.py").read_text(encoding="utf-8")
    assert "MLBLLMHypothesisFunction" in template
    assert "MLB_LLM_HYPOTHESIS_ENABLED: 'true'" in template
    assert "INQSI_MLB_ML_AUTO_PROMOTE: 'true'" in template
    assert "MLB_AUTO_CONTINUITY_V3_INSTALLED = True" in trainer
    assert "LEARNING_CONTINUES_BELOW_AUTHORITY_TARGET = True" in experiment
    assert "_MLB_AUTO_V2_ORIGINAL_INSTALL" in runtime
    installer = (ROOT / "scripts" / "install_mlb_auto_autonomy_chain.py").read_text(encoding="utf-8")
    assert "tennis" not in installer.lower()
    assert "soccer" not in installer.lower()
