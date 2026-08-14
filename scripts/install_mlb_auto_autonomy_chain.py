#!/usr/bin/env python3
"""Install the complete MLB AUTO autonomy chain into canonical source.

This migration is intentionally source-idempotent. It wires gap-tolerant exact
slate continuity, explicit missingness training, gated automatic promotion, the
V2 live inference consumer, and the bounded Bedrock hypothesis layer without
weakening immutable locks, chronological testing, calibration, or wagering
safety.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> tuple[Path, str]:
    target = ROOT / path
    return target, target.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"migration marker missing: {label}")


def _insert_after(text: str, marker: str, addition: str, token: str) -> str:
    if token in text:
        return text
    if marker not in text:
        raise RuntimeError(f"migration marker missing: {token}")
    return text.replace(marker, marker + addition, 1)


def _insert_before(text: str, marker: str, addition: str, token: str) -> str:
    if token in text:
        return text
    if marker not in text:
        raise RuntimeError(f"migration marker missing: {token}")
    return text.replace(marker, addition + marker, 1)


def _patch_trainer_compat() -> None:
    path, text = _read("hello_world/mlb_ml_aws_training_v1_compat.py")
    text = _insert_after(
        text,
        "import mlb_prospective_trainer_read_repair as prospective_trainer_read_repair\n",
        "import mlb_ml_autonomy_chain_v1 as mlb_ml_autonomy_chain_v1\n",
        "import mlb_ml_autonomy_chain_v1",
    )
    text = _insert_after(
        text,
        "prospective_trainer_read_repair.install()\n",
        "mlb_ml_autonomy_chain_v1.install(canonical)\n",
        "mlb_ml_autonomy_chain_v1.install(canonical)",
    )
    text = text.replace(
        "uniquely named Lambda entrypoint installs three narrow normalizations:",
        "uniquely named Lambda entrypoint installs the canonical continuity, missingness, and autonomy normalizations:",
    )
    _write(path, text)


def _patch_autonomy_chain() -> None:
    path, text = _read("hello_world/mlb_ml_autonomy_chain_v1.py")
    old = '''    exact_errors = _reasons(
        row.get("exactVectorValidationErrors")
        or (row.get("mlFeatureFreeze") or {}).get("exactVectorValidationErrors")
    )
    return bool(
        row.get("lockedPrediction") is True
        and row.get("exactVectorVerified") is True
        and not exact_errors
        and isinstance(vector, Mapping)
        and vector.get("fingerprint")
    )
'''
    new = '''    freeze = row.get("mlFeatureFreeze") or {}
    authority = row.get("canonicalLockAuthority") or {}
    exact_errors = _reasons(
        row.get("exactVectorValidationErrors")
        or freeze.get("exactVectorValidationErrors")
        or authority.get("exactVectorValidationErrors")
    )
    exact_verified = bool(
        row.get("exactVectorVerified") is True
        or freeze.get("exactVectorVerified") is True
        or authority.get("exactLockVectorValidated") is True
    )
    fingerprint = (
        vector.get("fingerprint")
        or freeze.get("frozenFeatureVectorFingerprint")
        or authority.get("frozenFeatureVectorFingerprint")
    )
    return bool(
        row.get("lockedPrediction") is True
        and exact_verified
        and not exact_errors
        and isinstance(vector, Mapping)
        and fingerprint
    )
'''
    text = _replace_once(text, old, new, "missingness exact-vector aliases")

    marker = "    original_status = canonical.TrainingService.status\n"
    block = '''    original_save_status = canonical.TrainingService._save_run_status
    if not getattr(original_save_status, "_mlb_autonomy_runtime_authority_v1", False):

        @functools.wraps(original_save_status)
        def save_run_status(self: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
            value = copy.deepcopy(payload or {})
            try:
                champion = self.store.load_champion() or {}
            except Exception:
                champion = {}
            runtime_active = bool(
                champion.get("runtimeAuthorityActivated") is True
                and champion.get("stableChampion") is True
                and champion.get("shadowOnly") is False
                and (
                    champion.get("directionAuthorityEnabled") is True
                    or champion.get("playabilityAuthorityEnabled") is True
                )
            )
            value["liveInferenceAuthority"] = runtime_active
            value["runtimeAuthorityChanged"] = bool(
                runtime_active
                and (
                    (value.get("promotion") or {}).get("champion")
                    or value.get("championChanged") is True
                )
            )
            promotion = value.get("promotion")
            if isinstance(promotion, dict) and runtime_active:
                promotion = copy.deepcopy(promotion)
                promotion["runtimeAuthorityActivated"] = True
                promotion["shadowChampionApproved"] = True
                value["promotion"] = promotion
            return original_save_status(self, value)

        setattr(save_run_status, "_mlb_autonomy_runtime_authority_v1", True)
        canonical.TrainingService._save_run_status = save_run_status

'''
    text = _insert_before(
        text,
        marker,
        block,
        "_mlb_autonomy_runtime_authority_v1",
    )
    _write(path, text)


def _patch_inference_consumer() -> None:
    path, text = _read("hello_world/mlb_ml_v2_inference_consumer_v1.py")
    marker = "\ndef _sync_direction(\n"
    block = '''
def load_active_champion() -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Return the exact validated active champion and a public-safe status."""
    return _default_champion_loader()

'''
    text = _insert_before(
        text,
        marker,
        block,
        "def load_active_champion()",
    )
    _write(path, text)


def _patch_runtime_installer() -> None:
    path, text = _read("hello_world/mlb_ml_runtime_install_v3.py")
    text = text.replace(
        "MLB-ML-RUNTIME-INSTALL-v4.4-ranked-winner-v15.10-",
        "MLB-ML-RUNTIME-INSTALL-v5.0-mlb-auto-v2-gated-runtime-",
    )
    text = _insert_after(
        text,
        "        import mlb_ranked_primary_v15_10\n",
        "        import mlb_ml_v2_inference_consumer_v1\n",
        "import mlb_ml_v2_inference_consumer_v1",
    )
    marker = "        mlb_ranked_primary_v15_10.apply_direction(engine)\n"
    addition = '''        # Install the incumbent/historical selection authority before V2.
        # The V2 consumer is therefore the final direction authority only when
        # an exact, gated active V2 champion exists; otherwise it is inert.
        mlb_ranked_primary_v15_10.apply_selection_authority(engine)
        mlb_ml_v2_inference_consumer_v1.apply_direction(engine)
        v2_consumer_contract = mlb_ml_v2_inference_consumer_v1.contract_status()
        v2_champion, v2_champion_status = (
            mlb_ml_v2_inference_consumer_v1.load_active_champion()
        )
        status["steps"]["v2InferenceConsumerInstalled"] = bool(
            v2_consumer_contract.get("installed") is True
            and getattr(
                engine,
                "_INQSI_MLB_V2_INFERENCE_CONSUMER_V1",
                False,
            )
        )
        status["v2InferenceConsumer"] = v2_consumer_contract
        status["v2ChampionStatus"] = v2_champion_status
        status["v2ChampionActive"] = bool(v2_champion)
'''
    text = _insert_after(
        text,
        marker,
        addition,
        "v2_consumer_contract = mlb_ml_v2_inference_consumer_v1.contract_status()",
    )
    old = '''        status["steps"]["v2ShadowManualFirst"] = not automatic_promotion_enabled
'''
    new = '''        status["steps"]["v2GatedAutomaticPromotionContractInstalled"] = True
        status["v2AutomaticPromotionEnabled"] = automatic_promotion_enabled
        status["v2ShadowManualFirst"] = False
        status["firstPromotionRequiresManualReview"] = False
'''
    text = _replace_once(text, old, new, "runtime automatic promotion status")
    marker = '        status["winnerPickRequiredForEveryValidEvent"] = True\n'
    addition = '''        if v2_champion:
            status["productionAuthoritySource"] = "mlb_ml_v2_active_champion"
            status["productionAuthorityLifecycleState"] = "V2_GATED_ACTIVE"
            status["precisionHitRateEvidencePassed"] = bool(
                (v2_champion.get("promotionGate") or {}).get(
                    "promotionEligible"
                )
                is True
            )
'''
    text = _insert_before(
        text,
        marker,
        addition,
        '"V2_GATED_ACTIVE"',
    )
    text = text.replace(
        '"The historical whole-slate optimizer is installed as the outermost "',
        '"The V2 gated champion consumer and historical whole-slate optimizer are installed. "',
    )
    _write(path, text)


def _patch_main_template() -> None:
    path, text = _read("template.yaml")
    text = text.replace(
        "        INQSI_MLB_ML_AUTO_PROMOTE: 'false'",
        "        INQSI_MLB_ML_AUTO_PROMOTE: 'true'",
    )
    text = _insert_after(
        text,
        "        INQSI_MLB_ML_AUTO_PROMOTE: 'true'\n",
        "        INQSI_MLB_V2_INFERENCE_ENABLED: 'true'\n",
        "INQSI_MLB_V2_INFERENCE_ENABLED: 'true'",
    )
    _write(path, text)


def _patch_historical_template() -> None:
    path, text = _read("mlb_historical_optimizer/template.yaml")
    text = _insert_after(
        text,
        "        MLB_HISTORICAL_POLICY_ENABLED: 'true'\n",
        "        MLB_AUTO_LLM_HYPOTHESIS_ENABLED: 'true'\n        MLB_AUTO_LLM_MODEL_ID: 'amazon.nova-lite-v1:0'\n",
        "MLB_AUTO_LLM_MODEL_ID:",
    )
    marker = "      Events:\n"
    block = '''        - Statement:
            - Sid: InvokeBoundedMlbHypothesisModel
              Effect: Allow
              Action:
                - bedrock:InvokeModel
              Resource:
                - !Sub arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/*
                - !Sub arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/*
'''
    text = _insert_before(
        text,
        marker,
        block,
        "InvokeBoundedMlbHypothesisModel",
    )
    _write(path, text)


def _patch_historical_handler() -> None:
    path, text = _read("hello_world/mlb_historical_optimizer_handler.py")
    text = _insert_after(
        text,
        "import mlb_historical_policy_v1 as policy_runtime\n",
        "import mlb_auto_llm_hypothesis_v1 as auto_llm\n",
        "import mlb_auto_llm_hypothesis_v1 as auto_llm",
    )
    marker = '''    result = optimizer.search(
        records,
        search_config,
        untouched_holdout_dates=fresh_dates,
    )
'''
    block = '''    try:
        llm_hypothesis_research = auto_llm.run_shadow_cycle(
            records,
            research_summary={
                "optimizationRound": round_number,
                "eligibleGameCount": int(state.get("eligibleGameCount") or 0),
                "freshAuditCollectedDayCount": int(
                    state.get("freshAuditCollectedDayCount") or 0
                ),
                "freshAuditCollectedGameCount": int(
                    state.get("freshAuditCollectedGameCount") or 0
                ),
                "baseSearchStatus": result.get("status"),
                "basePromotionPassed": bool(
                    (result.get("promotionGate") or {}).get("passed") is True
                ),
                "baseCandidatePolicyDigest": (
                    (result.get("candidate") or {}).get("policyDigest")
                ),
            },
            model_id=os.environ.get("MLB_AUTO_LLM_MODEL_ID"),
        )
    except Exception as exc:
        llm_hypothesis_research = {
            "ok": False,
            "version": auto_llm.VERSION,
            "status": "HYPOTHESIS_RESEARCH_FAILED_CLOSED",
            "errorType": type(exc).__name__,
            "productionAuthority": False,
            "productionWeightMutation": False,
            "winnerSelectionMutation": False,
            "automaticWagerAllowed": False,
        }
    result = copy.deepcopy(result)
    result["llmHypothesisResearch"] = llm_hypothesis_research
'''
    text = _insert_after(
        text,
        marker,
        block,
        'result["llmHypothesisResearch"]',
    )
    _write(path, text)


def _patch_schedule_invariants() -> None:
    path, text = _read("scripts/verify_mlb_schedule_invariants.py")
    marker = "if '\"days_ahead\":0' not in text and '\"days_ahead\": 0' not in text:\n"
    block = '''if "INQSI_MLB_ML_AUTO_PROMOTE: 'true'" not in text:
    violations.append('gated automatic MLB promotion is not enabled')
if "INQSI_MLB_V2_INFERENCE_ENABLED: 'true'" not in text:
    violations.append('MLB V2 inference consumer is not enabled')
'''
    text = _insert_before(
        text,
        marker,
        block,
        "gated automatic MLB promotion is not enabled",
    )
    _write(path, text)


def _patch_deploy_contract() -> None:
    path, text = _read("scripts/stabilize_mlb_deploy_source.py")
    marker = '        "verify_mlb_trainer_deploy_response.py",\n'
    addition = '''        "mlb_ml_autonomy_chain_v1.py",
        "mlb_ml_v2_inference_consumer_v1.py",
        "mlb_auto_llm_hypothesis_v1.py",
        "INQSI_MLB_V2_INFERENCE_ENABLED",
        "first_promotion_still_requires_manual_review",
'''
    text = _insert_after(
        text,
        marker,
        addition,
        '"mlb_ml_autonomy_chain_v1.py"',
    )
    _write(path, text)


def _verify() -> None:
    required = {
        "hello_world/mlb_ml_aws_training_v1_compat.py": [
            "mlb_ml_autonomy_chain_v1.install(canonical)",
        ],
        "hello_world/mlb_ml_runtime_install_v3.py": [
            "mlb_ml_v2_inference_consumer_v1.apply_direction(engine)",
            "v2GatedAutomaticPromotionContractInstalled",
        ],
        "template.yaml": [
            "INQSI_MLB_ML_AUTO_PROMOTE: 'true'",
            "INQSI_MLB_V2_INFERENCE_ENABLED: 'true'",
        ],
        "hello_world/mlb_historical_optimizer_handler.py": [
            'result["llmHypothesisResearch"]',
        ],
        "mlb_historical_optimizer/template.yaml": [
            "MLB_AUTO_LLM_MODEL_ID:",
            "InvokeBoundedMlbHypothesisModel",
        ],
    }
    missing = []
    for file_name, tokens in required.items():
        text = (ROOT / file_name).read_text(encoding="utf-8")
        missing.extend(
            f"{file_name}:{token}" for token in tokens if token not in text
        )
    if missing:
        raise RuntimeError("MLB AUTO install incomplete: " + ", ".join(missing))


def main() -> None:
    _patch_trainer_compat()
    _patch_autonomy_chain()
    _patch_inference_consumer()
    _patch_runtime_installer()
    _patch_main_template()
    _patch_historical_template()
    _patch_historical_handler()
    _patch_schedule_invariants()
    _patch_deploy_contract()
    _verify()
    print(
        "Installed MLB AUTO gap-tolerant continuity, missingness training, "
        "gated automatic promotion, V2 inference, and bounded LLM hypothesis research."
    )


if __name__ == "__main__":
    main()
