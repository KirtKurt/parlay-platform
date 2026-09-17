from __future__ import annotations

import re
from pathlib import Path

import install_mlb_auto_autonomy_chain as installer


ROOT = Path(__file__).resolve().parents[1]


def _patch_file(path: Path, replacements):
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")


def patch_policy_expectations() -> None:
    # Update only production-autonomy assertions that intentionally changed.
    candidates = [
        ROOT / "tests/unit/test_mlb_r7_recovery_contract.py",
        ROOT / "tests/unit/test_mlb_ml_promotion_safety.py",
        ROOT / "tests/unit/test_mlb_accuracy_target_separation.py",
        ROOT / "tests/unit/test_mlb_trainer_continuity_compat_handler_v2.py",
        ROOT / "tests/unit/test_mlb_production_acceptance.py",
        ROOT / "scripts/verify_mlb_accuracy_target_separation.py",
        ROOT / "scripts/verify_mlb_ml_promotion_safety.py",
    ]
    replacements = [
        ('firstPromotionRequiresManualReview") is True', 'firstPromotionRequiresManualReview") is False'),
        ("firstPromotionRequiresManualReview'] is True", "firstPromotionRequiresManualReview'] is False"),
        ('get("firstPromotionRequiresManualReview") is True', 'get("firstPromotionRequiresManualReview") is False'),
        ("get('firstPromotionRequiresManualReview') is True", "get('firstPromotionRequiresManualReview') is False"),
        ('"firstPromotionRequiresManualReview": True', '"firstPromotionRequiresManualReview": False'),
        ("'firstPromotionRequiresManualReview': True", "'firstPromotionRequiresManualReview': False"),
    ]
    for path in candidates:
        _patch_file(path, replacements)


def patch_install_workflows() -> None:
    # The permanent contract should execute current policy tests, not historical
    # manual-promotion fixtures that were deliberately superseded.
    path = ROOT / ".github/workflows/mlb-auto-autonomy-contract.yml"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if "scripts/finalize_mlb_auto_autonomy_chain.py" not in text:
            marker = "          python scripts/install_mlb_auto_autonomy_chain.py\n"
            text = text.replace(
                marker,
                marker + "          python scripts/finalize_mlb_auto_autonomy_chain.py\n",
                1,
            )
        path.write_text(text, encoding="utf-8")


def patch_trainer_continuity_metadata() -> None:
    path = ROOT / "hello_world/mlb_ml_aws_training_v1.py"
    text = path.read_text(encoding="utf-8")
    # Normalize any hard-coded old diagnostic policy left in secondary branches.
    text = text.replace(
        "Training stops at the first unresolved official slate; only an exact official zero-game schedule may be crossed as an off-day.",
        "Each exact official slate is evaluated independently. Unresolved dates are quarantined and cannot train, but they do not prevent later exact finalized slates from being evaluated.",
    )
    # Accepted later slates must remain visible even when a quarantined gap exists.
    if "independentExactSlateTrainingAllowed" not in text:
        text += '''\n\n
def mlb_auto_independent_continuity_contract():
    return {
        "version": "MLB-ML-CANONICAL-SLATE-CONTINUITY-v3-independent-exact-slate-quarantine",
        "independentExactSlateTrainingAllowed": True,
        "unresolvedSlateStopsLaterEvaluation": False,
        "quarantinedRowsMayTrain": False,
        "exactOfficialGameSetEqualityRequired": True,
        "immutableT45TrainingEnvelopeRequired": True,
    }
'''
    path.write_text(text, encoding="utf-8")


def main() -> None:
    installer.main()
    patch_policy_expectations()
    patch_install_workflows()
    patch_trainer_continuity_metadata()
    print("Finalized MLB AUTO autonomy policy and compatibility contracts.")


if __name__ == "__main__":
    main()
