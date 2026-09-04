from __future__ import annotations

import ast
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import mlb_ml_canonical_continuity_v3 as continuity
import mlb_ml_deployment_identity_v1 as identity
import mlb_ml_experiment_v2 as experiment
import mlb_ml_llm_hypothesis_v1 as llm
import mlb_ml_promotion_policy_v2 as promotion
import mlb_ml_v2_inference_consumer as consumer


def _require(condition, message):
    if not condition:
        raise SystemExit("MLB AUTO autonomy contract failure: " + message)


def _row(game_pk):
    commence = datetime(2026, 8, 5, 23, 0, tzinfo=timezone.utc)
    lock_at = commence - timedelta(minutes=45)
    source_at = lock_at - timedelta(seconds=5)
    return {
        "officialGamePk": str(game_pk),
        "slateDateEt": "2026-08-05",
        "commenceTime": commence.isoformat(),
        "lockedAtUtc": lock_at.isoformat(),
        "predictionSourcePullAt": source_at.isoformat(),
        "lockedPrediction": True,
        "officialPrediction": True,
        "lockedAmericanOdds": -110,
        "actualWinner": "Home",
        "settled": True,
        "homeTeam": "Home",
        "awayTeam": "Away",
        "frozenFeatureVector": {
            "officialGamePk": str(game_pk),
            "fingerprint": "f" * 64,
            "lockAtUtc": lock_at.isoformat(),
            "sourcePullAtUtc": source_at.isoformat(),
            "features": {"selected_probability": 0.58, "movement_180m": 0.01},
        },
    }


def main():
    template = (ROOT / "template.yaml").read_text(encoding="utf-8")
    trainer = (HELLO / "mlb_ml_aws_training_v1.py").read_text(encoding="utf-8")
    compat = (HELLO / "mlb_ml_aws_training_v1_compat.py").read_text(encoding="utf-8")
    runtime = (HELLO / "mlb_ml_runtime_install_v3.py").read_text(encoding="utf-8")
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")

    for path in (
        HELLO / "mlb_ml_canonical_continuity_v3.py",
        HELLO / "mlb_ml_deployment_identity_v1.py",
        HELLO / "mlb_ml_v2_inference_consumer.py",
        HELLO / "mlb_ml_llm_hypothesis_v1.py",
        HELLO / "mlb_ml_aws_training_v1.py",
        HELLO / "mlb_ml_aws_training_v1_compat.py",
        HELLO / "mlb_ml_runtime_install_v3.py",
    ):
        ast.parse(path.read_text(encoding="utf-8"))

    _require("MLB_AUTO_CONTINUITY_V3_INSTALLED = True" in trainer, "continuity v3 not installed")
    _require("unresolvedSlateStopsLaterEvaluation" in trainer, "non-blocking continuity metadata missing")
    _require("_MLB_AUTO_COMPAT_ORIGINAL_HANDLER" in compat, "trainer compatibility wrapper missing")
    _require("_MLB_AUTO_V2_ORIGINAL_INSTALL" in runtime, "V2 consumer runtime install missing")
    _require("INQSI_MLB_ML_AUTO_PROMOTE: 'true'" in template, "automatic promotion env is not enabled")
    _require("MLBLLMHypothesisFunction" in template, "LLM hypothesis Lambda missing")
    _require("bedrock:InvokeModel" in template, "Bedrock invoke permission missing")
    _require("Synchronize MLB training selection and inference identity" in deploy, "postdeploy identity synchronization missing")

    _require(experiment.AUTOMATIC_PROMOTION_ENABLED is True, "experiment auto promotion false")
    _require(experiment.FIRST_PROMOTION_REQUIRES_MANUAL_REVIEW is False, "manual first promotion remains")
    _require(experiment.LEARNING_CONTINUES_BELOW_AUTHORITY_TARGET is True, "learning remains tied to 90 percent")
    contract = promotion.authority_target_contract()
    _require(contract["automaticPromotionEnabled"] is True, "authority contract auto promotion false")
    _require(contract["firstPromotionRequiresManualReview"] is False, "authority contract manual review true")
    _require(contract["accuracyTargetAffectsCandidateTraining"] is False, "90 percent still blocks candidate learning")
    _require(contract["accuracyTargetAffectsProductionAuthorityOnly"] is True, "90 percent not isolated to authority")

    authorities = {
        "2026-08-04": None,
        "2026-08-05": {
            "slateFinalized": True,
            "officialGamePks": ["2"],
            "officialGameCount": 1,
        },
    }
    proof = continuity.scan_independent_slates(
        start_date_et="2026-08-04",
        end_date_et="2026-08-05",
        authority_loader=authorities.get,
        row_loader=lambda date: [] if date == "2026-08-04" else [_row("2")],
    )
    _require(proof["acceptedRowCount"] == 1, "later exact finalized slate was not accepted")
    _require(proof["quarantinedSlateDates"] == ["2026-08-04"], "unresolved slate not quarantined")
    _require(proof["unresolvedSlateStopsLaterEvaluation"] is False, "unresolved slate still stops evaluation")

    envelope = continuity.build_training_envelope(_row("2"))
    _require(envelope["eligible"] is True, "valid immutable T45 row is not training eligible")
    _require(envelope["selectedSideLockedOdds"] == -110, "locked odds missing from envelope")
    _require(envelope["outcomeWinner"] == "Home", "outcome missing from envelope")

    _require(llm.VERSION.startswith("MLB-ML-LLM-HYPOTHESIS"), "LLM hypothesis module missing")
    bad = llm.hypothesis_errors({
        "name": "bad",
        "rationale": "bad",
        "interactions": [{"features": ["actual_winner"], "operator": "and"}],
        "regimePredicates": [],
        "timingWindows": [],
        "modelFamilies": ["logistic"],
    })
    _require(bool(bad), "LLM layer permits outcome leakage")

    no_champion = consumer.champion_errors({})
    _require("no_active_v2_champion" in no_champion, "consumer does not fail closed without champion")

    changed = []
    for prefix in ("tennis/", "soccer/", "tennis_auto/", "soccer_auto/"):
        if prefix in (ROOT / "scripts/install_mlb_auto_autonomy_chain.py").read_text(encoding="utf-8").lower():
            changed.append(prefix)
    _require(not changed, "installer references isolated sport application paths")

    print("MLB AUTO autonomy chain PASS: independent exact-slate quarantine, immutable T45 training envelope, one deployment identity, V2 consumer installation, gated automatic promotion, continuous below-target learning, and bounded Bedrock hypothesis research are installed.")


if __name__ == "__main__":
    main()
