#!/usr/bin/env python3
"""Align MLB API/runtime contracts with the gated autonomous V2 design."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _replace(path: Path, old: str, new: str, label: str) -> str:
    text = path.read_text(encoding="utf-8")
    if old in text:
        text = text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
        return text
    if new in text:
        return text
    raise RuntimeError(f"runtime contract marker missing: {label}")


def _patch_accuracy_policy() -> None:
    path = ROOT / "hello_world" / "mlb_accuracy_target_policy_v1.py"
    text = path.read_text(encoding="utf-8")
    replacements = (
        (
            'VERSION = "MLB-ACCURACY-TARGET-POLICY-v4-dashboard-only-v2-manual-first"',
            'VERSION = "MLB-ACCURACY-TARGET-POLICY-v5-dashboard-only-v2-gated-auto"',
            "accuracy policy version",
        ),
        (
            '        "automaticPromotionAfterApplicableGates": False,\n        "firstPromotionRequiresManualReview": True,',
            '        "automaticPromotionAfterApplicableGates": True,\n        "firstPromotionRequiresManualReview": False,\n        "manualReviewCreatesShadowApprovalOnly": False,\n        "learningContinuesBelowAspirationalAccuracy": True,\n        "aspirationalAccuracyBlocksTraining": False,\n        "aspirationalAccuracyBlocksCandidateEvaluation": False,\n        "aspirationalAccuracyBlocksPlayableAuthority": True,',
            "automatic promotion policy",
        ),
        (
            "aspirations only. V2 promotion uses fixed prospective market-skill gates and manual first review.",
            "aspirations only. V2 continuously trains and evaluates challengers below the target; a champion activates automatically only after immutable prospective, calibration, proper-scoring, deployment-identity, and runtime-consumer gates pass. Automatic wagering remains disabled.",
            "policy description",
        ),
    )
    for old, new, label in replacements:
        if old in text:
            text = text.replace(old, new, 1)
        elif new not in text:
            raise RuntimeError(f"runtime contract marker missing: {label}")
    path.write_text(text, encoding="utf-8")


def _patch_read_api() -> None:
    path = ROOT / "hello_world" / "mlb_v3_read_api.py"
    text = path.read_text(encoding="utf-8")
    if 'V2_MODEL_VERSION = "INQSI-MLB-v6.0-auto-v2-gated-champion"' not in text:
        marker = 'HISTORICAL_MODEL_VERSION = "INQSI-MLB-v5.1.1-historical-daily-only-cutover-wager-disabled"\n'
        addition = (
            'V2_MODEL_VERSION = "INQSI-MLB-v6.0-auto-v2-gated-champion"\n'
            'V2_SELECTOR = "MLB-ML-V2-ACTIVE-CHAMPION"\n'
        )
        if marker not in text:
            raise RuntimeError("read API model version marker missing")
        text = text.replace(marker, marker + addition, 1)

    old = '''    authority_coherent = steps.get("historicalAuthorityStateCoherent") is True
    runtime_ready = bool(
        ENGINE_IMPORT_OK
        and runtime.get("ok") is True
        and steps.get("rankedWinnerV15_10SelectionInstalled") is True
        and runtime.get("historicalDailyChampionOutermostAuthorityInstalled") is True
        and authority_coherent
        and ranked_version
    )
    primary = historical_version if historical_active else (ranked_version or MODEL_VERSION)
'''
    new = '''    authority_coherent = steps.get("historicalAuthorityStateCoherent") is True
    v2_consumer = runtime.get("v2InferenceConsumer") or {}
    v2_installed = steps.get("v2InferenceConsumerInstalled") is True
    v2_enabled = v2_consumer.get("enabled") is True
    v2_active = runtime.get("v2ChampionActive") is True
    v2_activation_available = bool(v2_installed and v2_enabled)
    runtime_ready = bool(
        ENGINE_IMPORT_OK
        and runtime.get("ok") is True
        and steps.get("rankedWinnerV15_10SelectionInstalled") is True
        and runtime.get("historicalDailyChampionOutermostAuthorityInstalled") is True
        and authority_coherent
        and v2_installed
        and ranked_version
    )
    primary = (
        V2_SELECTOR
        if v2_active
        else historical_version if historical_active else (ranked_version or MODEL_VERSION)
    )
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise RuntimeError("read API runtime-ready marker missing")

    replacements = (
        (
            '        "model_version": HISTORICAL_MODEL_VERSION if historical_active else MODEL_VERSION,',
            '        "model_version": V2_MODEL_VERSION if v2_active else HISTORICAL_MODEL_VERSION if historical_active else MODEL_VERSION,',
            "read API model selection",
        ),
        (
            '        "precisionHitRateEvidencePassed": historical_active,\n        "dailySlateAccuracyEvidencePassed": historical_active,',
            '        "precisionHitRateEvidencePassed": bool(v2_active or historical_active),\n        "dailySlateAccuracyEvidencePassed": bool(v2_active or historical_active),',
            "read API precision evidence",
        ),
        (
            '        "awsNativeTrainingAuthority": historical_active,',
            '        "awsNativeTrainingAuthority": bool(v2_active or historical_active),',
            "read API trainer authority",
        ),
        (
            '        "firstPromotionRequiresManualReview": not historical_active,\n        "manualReviewCreatesShadowApprovalOnly": not historical_active,\n        "v2InferenceConsumerInstalled": historical_active,\n        "runtimeAuthorityActivationAvailable": historical_active,',
            '        "firstPromotionRequiresManualReview": False,\n        "manualReviewCreatesShadowApprovalOnly": False,\n        "v2InferenceConsumerInstalled": v2_installed,\n        "v2InferenceConsumerEnabled": v2_enabled,\n        "v2ChampionActive": v2_active,\n        "v2ChampionStatus": runtime.get("v2ChampionStatus"),\n        "runtimeAuthorityActivationAvailable": v2_activation_available,\n        "learningContinuesBelowAspirationalAccuracy": True,\n        "aspirationalAccuracyBlocksTraining": False,\n        "aspirationalAccuracyBlocksCandidateEvaluation": False,\n        "aspirationalAccuracyBlocksPlayableAuthority": True,',
            "read API autonomy status",
        ),
    )
    for old_value, new_value, label in replacements:
        if old_value in text:
            text = text.replace(old_value, new_value, 1)
        elif new_value not in text:
            raise RuntimeError(f"runtime contract marker missing: {label}")

    old = '''        "automaticPromotionPolicy": (
            "automatic atomic fail-closed champion plus historical-only cutover after the immutable 1000/200/200 every-day gate passes"
            if historical_active
            else "winner model fixed for release; precision/trade promotion remains disabled"
        ),
'''
    new = '''        "automaticPromotionPolicy": (
            "gated automatic V2 promotion after immutable chronological prospective, calibration, proper-scoring, deployment-identity, and runtime-consumer gates"
            if not historical_active
            else "automatic atomic fail-closed champion plus historical-only cutover after the immutable 1000/200/200 every-day gate passes"
        ),
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise RuntimeError("read API automatic promotion marker missing")

    old = '''        "requiredWinnerPickPolicy": (
            "one winner PICK for every valid MLB game on the complete slate"
            if historical_active
            else "one active-model ranked winner PICK for every valid MLB game"
        ),
'''
    new = '''        "requiredWinnerPickPolicy": (
            "one V2 champion winner PICK for every valid MLB game"
            if v2_active
            else "one winner PICK for every valid MLB game on the complete slate"
            if historical_active
            else "one active-model ranked winner PICK for every valid MLB game"
        ),
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise RuntimeError("read API winner policy marker missing")

    old = '''        "mlDirectionPolicy": (
            "the historical daily champion is the sole outermost direction authority; the prior selector is quarantined and has no automatic fallback path"
            if historical_active
            else "active exported ensemble is sole direction authority until the immutable historical daily gate passes"
        ),
'''
    new = '''        "mlDirectionPolicy": (
            "the exact promoted V2 champion is the outermost direction authority; downstream deterministic signal and integrity gates remain mandatory"
            if v2_active
            else "the historical daily champion is the sole outermost direction authority; the prior selector is quarantined and has no automatic fallback path"
            if historical_active
            else "active exported ensemble remains direction authority until a V2 or historical champion passes its immutable gate"
        ),
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise RuntimeError("read API direction policy marker missing")

    path.write_text(text, encoding="utf-8")


def _patch_cold_import_verifier() -> None:
    path = ROOT / "scripts" / "verify_api_function_mlb_v3_import.py"
    text = path.read_text(encoding="utf-8")
    replacements = (
        (
            '    "MLB-ML-RUNTIME-INSTALL-v4.4-ranked-winner-v15.10-"',
            '    "MLB-ML-RUNTIME-INSTALL-v5.0-mlb-auto-v2-gated-runtime-"',
            "expected runtime version",
        ),
        (
            '            "INQSI_MLB_ML_AUTO_PROMOTE": "false",',
            '            "INQSI_MLB_ML_AUTO_PROMOTE": "true",\n            "INQSI_MLB_V2_INFERENCE_ENABLED": "true",',
            "cold import autonomy environment",
        ),
        (
            'assert body.get("automaticPromotionPolicy") == "winner model fixed for release; precision/trade promotion remains disabled", body\nassert body.get("firstPromotionRequiresManualReview") is True, body\nassert body.get("manualReviewCreatesShadowApprovalOnly") is True, body\nassert body.get("runtimeAuthorityActivationAvailable") is False, body',
            'assert body.get("automaticPromotionPolicy") == "gated automatic V2 promotion after immutable chronological prospective, calibration, proper-scoring, deployment-identity, and runtime-consumer gates", body\nassert body.get("firstPromotionRequiresManualReview") is False, body\nassert body.get("manualReviewCreatesShadowApprovalOnly") is False, body\nassert body.get("v2InferenceConsumerInstalled") is True, body\nassert body.get("v2InferenceConsumerEnabled") is True, body\nassert body.get("runtimeAuthorityActivationAvailable") is True, body\nassert body.get("learningContinuesBelowAspirationalAccuracy") is True, body\nassert body.get("aspirationalAccuracyBlocksTraining") is False, body\nassert body.get("aspirationalAccuracyBlocksCandidateEvaluation") is False, body\nassert body.get("aspirationalAccuracyBlocksPlayableAuthority") is True, body',
            "cold import automatic promotion assertions",
        ),
        (
            '    "v2ShadowManualFirst",',
            '    "v2InferenceConsumerInstalled",\n    "v2GatedAutomaticPromotionContractInstalled",',
            "cold import required runtime steps",
        ),
    )
    for old, new, label in replacements:
        if old in text:
            text = text.replace(old, new, 1)
        elif new not in text:
            raise RuntimeError(f"runtime contract marker missing: {label}")

    marker = 'assert runtime.get("automaticWagerAllowed") is False, runtime\n'
    addition = '''assert runtime.get("v2AutomaticPromotionEnabled") is True, runtime
assert runtime.get("firstPromotionRequiresManualReview") is False, runtime
assert (runtime.get("v2InferenceConsumer") or {}).get("enabled") is True, runtime
assert runtime.get("v2ChampionActive") is False, runtime
'''
    if addition not in text:
        if marker not in text:
            raise RuntimeError("cold import runtime assertion marker missing")
        text = text.replace(marker, marker + addition, 1)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    _patch_accuracy_policy()
    _patch_read_api()
    _patch_cold_import_verifier()
    print("Aligned MLB accuracy, read API, and cold-start contracts with gated autonomous V2.")


if __name__ == "__main__":
    main()
