from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPOSITORY_ROOT / ".github" / "workflows"
CANONICAL_REPORT = "runtime_reports/mlb_supervised_shadow_v2_latest.json"
SHARED_CONCURRENCY_GROUP = "group: mlb-supervised-shadow-v2"
GUARD_SCRIPT = "scripts/guard_mlb_v8_shadow_report.py"
EXPECTED_PUBLISHERS = {
    "mlb-supervised-shadow-v2.yml",
    "mlb-supervised-shadow-v2-recurring.yml",
    "mlb-supervised-v2-immediate-eval-once.yml",
    "mlb-v9-event-id-direct-recovery-once.yml",
}


def _canonical_report_publishers() -> dict[str, str]:
    publishers: dict[str, str] = {}
    for path in sorted(WORKFLOW_ROOT.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if CANONICAL_REPORT not in text:
            continue
        if "git push origin HEAD:main" not in text:
            continue
        owns_canonical_path = (
            f"REPORT_PATH: {CANONICAL_REPORT}" in text
            or f"SHADOW_REPORT: {CANONICAL_REPORT}" in text
        )
        if owns_canonical_path:
            publishers[path.name] = text
    return publishers


def test_canonical_v8_report_publisher_inventory_is_explicit() -> None:
    assert set(_canonical_report_publishers()) == EXPECTED_PUBLISHERS


def test_every_canonical_v8_report_publisher_is_serialized_and_monotonic() -> None:
    for name, text in _canonical_report_publishers().items():
        assert SHARED_CONCURRENCY_GROUP in text, name
        assert "cancel-in-progress: false" in text, name
        assert GUARD_SCRIPT in text, name
        assert "--incoming" in text, name
        assert "--destination" in text, name


def test_no_canonical_v8_report_publisher_blindly_copies_shadow_evidence() -> None:
    prohibited = {
        'cp /tmp/mlb-supervised-shadow-v2.json "$REPORT_PATH"',
        'cp /tmp/mlb-supervised-v2-immediate-report.json "$REPORT_PATH"',
        'cp /tmp/mlb-v9-shadow-report.json "$SHADOW_REPORT"',
    }
    for name, text in _canonical_report_publishers().items():
        for command in prohibited:
            assert command not in text, f"{name}: {command}"
