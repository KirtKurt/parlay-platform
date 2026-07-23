#!/usr/bin/env python3
"""Apply the one-time MLB r4 prospective cohort boundary reset.

The r3 cohort remains immutable diagnostic evidence. It began on the July 22
slate that never acquired authoritative pregame locks or write-once labels after
the persistence outage, so strict continuity can never advance through it.
"""
from __future__ import annotations

from pathlib import Path


OLD_ID = "mlb-v2-2026-07-22-future-prospective-r3"
NEW_ID = "mlb-v2-2026-07-24-future-prospective-r4"
OLD_CUTOFF = "2026-07-22T04:00:00+00:00"
NEW_CUTOFF = "2026-07-24T04:00:00+00:00"

ID_PATHS = (
    "hello_world/mlb_ml_experiment_v2.py",
    "scripts/run_mlb_ml_v3_audit_report.py",
    "scripts/stabilize_mlb_deploy_source.py",
    "scripts/verify_mlb_deploy_identity.py",
    "scripts/verify_mlb_ml_installation_1_5.py",
    "scripts/verify_mlb_ml_optimization_v3.py",
    "scripts/verify_mlb_ml_training_readiness.py",
    "scripts/verify_mlb_release_activation_predeploy.py",
    "scripts/verify_mlb_schedule_invariants.py",
    "scripts/verify_mlb_trainer_deploy_response.py",
    "template.yaml",
    "tests/unit/test_mlb_deploy_identity.py",
    "tests/unit/test_run_mlb_ml_v3_audit_report.py",
)

CUTOFF_PATHS = (
    "docs/MLB_ML_PRODUCTION_MILESTONES.md",
    "hello_world/mlb_ml_experiment_v2.py",
    "scripts/stabilize_mlb_deploy_source.py",
    "scripts/verify_mlb_deploy_identity.py",
    "scripts/verify_mlb_ml_installation_1_5.py",
    "scripts/verify_mlb_ml_optimization_v3.py",
    "scripts/verify_mlb_ml_training_readiness.py",
    "scripts/verify_mlb_release_activation_predeploy.py",
    "scripts/verify_mlb_schedule_invariants.py",
    "scripts/verify_mlb_trainer_deploy_response.py",
    "template.yaml",
    "tests/unit/test_mlb_deploy_identity.py",
    "tests/unit/test_mlb_ml_aws_training_v1.py",
    "tests/unit/test_run_mlb_ml_v3_audit_report.py",
)


def replace_required(path: Path, old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if new in text and old not in text:
        return False
    if old not in text:
        raise SystemExit(f"expected value missing from {path}: {old}")
    path.write_text(text.replace(old, new), encoding="utf-8")
    return True


def main() -> int:
    changed: set[str] = set()
    for raw in ID_PATHS:
        path = Path(raw)
        if replace_required(path, OLD_ID, NEW_ID):
            changed.add(raw)
    for raw in CUTOFF_PATHS:
        path = Path(raw)
        if replace_required(path, OLD_CUTOFF, NEW_CUTOFF):
            changed.add(raw)

    training = Path("hello_world/mlb_ml_aws_training_v1.py")
    text = training.read_text(encoding="utf-8")
    for old, new in {
        "future-prospective r3 experiment ID": "future-prospective r4 experiment ID",
        "future-prospective r3 release contract": "future-prospective r4 release contract",
        "MLB ML r3 release cutoff must begin at the July 22 ET slate boundary": (
            "MLB ML r4 release cutoff must begin at the July 24 ET slate boundary"
        ),
    }.items():
        if new in text and old not in text:
            continue
        if old not in text:
            raise SystemExit(f"expected trainer message missing: {old}")
        text = text.replace(old, new)
    training.write_text(text, encoding="utf-8")
    changed.add(str(training))

    for raw in (
        "scripts/verify_mlb_ml_installation_1_5.py",
        "scripts/verify_mlb_ml_optimization_v3.py",
        "scripts/verify_mlb_ml_training_readiness.py",
    ):
        path = Path(raw)
        text = path.read_text(encoding="utf-8")
        text = text.replace("R3_EXPERIMENT_ID", "R4_EXPERIMENT_ID")
        text = text.replace("R3_RELEASE_CUTOFF_UTC", "R4_RELEASE_CUTOFF_UTC")
        path.write_text(text, encoding="utf-8")
        changed.add(raw)

    docs = Path("docs/MLB_ML_PRODUCTION_MILESTONES.md")
    text = docs.read_text(encoding="utf-8")
    note = (
        "\n> **R4 clean-boundary reset (July 23, 2026):** The r3 cohort remains "
        "immutable diagnostic evidence. Its July 22 opening slate lacked the exact "
        "pregame locks and write-once labels after the production persistence outage, "
        "so it can never satisfy canonical continuity. Production training now begins "
        "with the July 24 ET slate; no July 22 row is backfilled or relabeled.\n"
    )
    if "R4 clean-boundary reset" not in text:
        marker = "# MLB ML Production Milestones\n"
        text = text.replace(marker, marker + note, 1) if marker in text else note + text
        docs.write_text(text, encoding="utf-8")
        changed.add(str(docs))

    contract = Path(".github/workflows/mlb-production-source-contract.yml")
    text = contract.read_text(encoding="utf-8")
    line = "            tests/unit/test_mlb_ml_r4_clean_cutover.py \\\n"
    if line not in text:
        anchor = "            tests/unit/test_mlb_ml_aws_training_v1.py \\\n"
        if anchor not in text:
            raise SystemExit("source-contract insertion anchor missing")
        contract.write_text(text.replace(anchor, anchor + line, 1), encoding="utf-8")
        changed.add(str(contract))

    print("\n".join(sorted(changed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
