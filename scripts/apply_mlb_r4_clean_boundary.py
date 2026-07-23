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


def replace_fixture_required(text: str, old: str, new: str, *, name: str) -> str:
    if new in text and old not in text:
        return text
    if old not in text:
        raise SystemExit(f"expected r4 fixture missing ({name}): {old}")
    return text.replace(old, new)


def advance_audit_report_fixtures() -> bool:
    """Keep synthetic post-cutoff rows post-cutoff after the r4 migration.

    The production acceptance tests intentionally exercise one quarantined row
    before the cohort boundary and several exact finalized rows after it. Only
    fixture timestamps and partition keys move; outcome/lock semantics do not.
    """

    path = Path("tests/unit/test_run_mlb_ml_v3_audit_report.py")
    original = path.read_text(encoding="utf-8")
    text = original
    replacements = (
        (
            "SETTLEMENT_NOW = datetime(2026, 7, 23, 3, 0, tzinfo=timezone.utc)",
            "SETTLEMENT_NOW = datetime(2026, 7, 25, 3, 0, tzinfo=timezone.utc)",
            "settlement now",
        ),
        (
            'commence_time: str = "2026-07-22T06:00:00+00:00"',
            'commence_time: str = "2026-07-24T06:00:00+00:00"',
            "default commence time",
        ),
        (
            'lock_at_utc: str = "2026-07-22T05:15:00+00:00"',
            'lock_at_utc: str = "2026-07-24T05:15:00+00:00"',
            "default lock time",
        ),
        (
            '"slateDateEt": "2026-07-22"',
            '"slateDateEt": "2026-07-24"',
            "row slate date",
        ),
        (
            '"sourcePk": "GAME_WINNERS#mlb#2026-07-22"',
            '"sourcePk": "GAME_WINNERS#mlb#2026-07-24"',
            "lock partition",
        ),
        (
            'if slate_date == "2026-07-22":',
            'if slate_date == "2026-07-24":',
            "official loader date",
        ),
        (
            "datetime(2026, 7, 22, 6, 0, tzinfo=timezone.utc)",
            "datetime(2026, 7, 24, 6, 0, tzinfo=timezone.utc)",
            "fallback official start",
        ),
        (
            "def test_post_cutoff_scope_quarantines_pre_r3_carryover_without_masking_it():",
            "def test_post_cutoff_scope_quarantines_pre_r4_carryover_without_masking_it():",
            "pre-cutoff test name",
        ),
        (
            '"commenceTime": "2026-07-22T04:30:00+00:00"',
            '"commenceTime": "2026-07-24T03:30:00+00:00"',
            "explicit pre-r4 quarantine row",
        ),
    )
    for old, new, name in replacements:
        text = replace_fixture_required(text, old, new, name=name)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def advance_trainer_test_fixtures() -> bool:
    """Move only boundary-sensitive trainer fixtures from r3 to r4.

    Generic model, partition and selection tests keep their original dates.
    The activation guard moves to the new exact cutoff, and the continuity test
    now models an incomplete July 24 slate observed after midnight ET July 25.
    """

    path = Path("tests/unit/test_mlb_ml_aws_training_v1.py")
    original = path.read_text(encoding="utf-8")
    text = original
    replacements = (
        (
            "def test_r2_cutoff_rejects_july20_and_every_pre_boundary_lock():",
            "def test_r4_cutoff_rejects_july20_and_every_pre_boundary_lock():",
            "cutoff test name",
        ),
        (
            "def test_new_r3_manifest_records_one_digest_bound_release_activation() -> None:",
            "def test_new_r4_manifest_records_one_digest_bound_release_activation() -> None:",
            "manifest activation test name",
        ),
        (
            "datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)",
            "datetime(2026, 7, 24, 4, 0, tzinfo=timezone.utc)",
            "activation at cutoff",
        ),
        (
            "datetime(2026, 7, 22, 4, 0, 0, 1, tzinfo=timezone.utc)",
            "datetime(2026, 7, 24, 4, 0, 0, 1, tzinfo=timezone.utc)",
            "activation after cutoff",
        ),
        (
            "def test_r3_manifest_cannot_be_initialized_at_or_after_cutoff(",
            "def test_r4_manifest_cannot_be_initialized_at_or_after_cutoff(",
            "cutoff rejection test name",
        ),
        (
            "def test_markerless_pre_cutoff_r3_manifest_is_activated_once_with_cas() -> None:",
            "def test_markerless_pre_cutoff_r4_manifest_is_activated_once_with_cas() -> None:",
            "markerless activation test name",
        ),
    )
    for old, new, name in replacements:
        text = replace_fixture_required(text, old, new, name=name)

    start_marker = (
        "def test_partial_prior_et_slate_cannot_freeze_or_deadlock_manifest(monkeypatch):"
    )
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit("partial prior-slate trainer test missing")
    next_test = text.find("\ndef ", start + len(start_marker))
    if next_test < 0:
        next_test = len(text)
    block = text[start:next_test]
    updated_block = block.replace(
        "after_midnight_et = datetime(2026, 7, 23, 4, 30, tzinfo=timezone.utc)",
        "after_midnight_et = datetime(2026, 7, 25, 4, 30, tzinfo=timezone.utc)",
    ).replace("2026-07-22", "2026-07-24")
    if updated_block == block:
        raise SystemExit("partial prior-slate trainer fixture did not move to r4")
    text = text[:start] + updated_block + text[next_test:]

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


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

    if advance_audit_report_fixtures():
        changed.add("tests/unit/test_run_mlb_ml_v3_audit_report.py")
    if advance_trainer_test_fixtures():
        changed.add("tests/unit/test_mlb_ml_aws_training_v1.py")

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
