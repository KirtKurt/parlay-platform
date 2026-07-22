from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "template.yaml").read_text(encoding="utf-8")
WRITER = (ROOT / "hello_world" / "mlb_manual_pull.py").read_text(encoding="utf-8")


def _resource_block(text: str, logical_id: str, next_logical_id: str) -> str:
    start = text.index(f"  {logical_id}:")
    end = text.index(f"  {next_logical_id}:", start)
    return text[start:end]


def test_audited_pull_has_full_slate_runtime_capacity():
    block = _resource_block(
        TEMPLATE,
        "MLBAuditedPullFunction",
        "MLBSignalApiFunction",
    )
    assert "Timeout: 600" in block
    assert "MemorySize: 2048" in block
    assert "Timeout: 120" not in block


def test_durable_winner_storage_precedes_optional_diagnostics():
    canonical = WRITER.index("canonical = _store_canonical_pull_history")
    winner = WRITER.index("game_winner_prediction_results.append", canonical)
    movement = WRITER.index("hot_movement_feature_results.append", canonical)
    audit = WRITER.index("_record_snapshot_audit_safe", canonical)
    hot_sides = WRITER.index("_build_read_only_hot_sides", canonical)
    shadow = WRITER.index("_capture_bbs_shadow_safe", canonical)

    assert canonical < winner < movement < audit < hot_sides < shadow


def test_scheduled_writer_skips_redundant_read_only_hot_side_build():
    marker = "scheduled_ingestion_prioritizes_durable_game_winner_persistence"
    assert marker in WRITER
    marker_at = WRITER.index(marker)
    conditional_at = WRITER.rfind(
        'if event.get("httpMethod") or event.get("requestContext"):',
        0,
        marker_at,
    )
    hot_side_at = WRITER.rfind("_build_read_only_hot_sides", 0, marker_at)
    assert conditional_at >= 0
    assert hot_side_at > conditional_at
