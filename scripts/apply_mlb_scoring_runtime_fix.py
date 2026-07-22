#!/usr/bin/env python3
"""Apply the permanent MLB scoring runtime-capacity and priority fix.

The migration is idempotent. It updates the canonical SAM template, moves
persistent winner generation ahead of optional diagnostics in the HOT pull
writer, and installs the same requirements in the mandatory deployment
invariant suite. It never calls AWS.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template.yaml"
WRITER = ROOT / "hello_world" / "mlb_manual_pull.py"
INVARIANTS = ROOT / "scripts" / "verify_mlb_schedule_invariants.py"

OLD_CAPACITY = """  MLBAuditedPullFunction:
    Type: AWS::Serverless::Function
    Properties:
      Timeout: 120
      # A missing canonical evidence slot is material, so ingestion keeps one
"""
NEW_CAPACITY = """  MLBAuditedPullFunction:
    Type: AWS::Serverless::Function
    Properties:
      # The protected writer performs official-roster reconciliation, canonical
      # storage, full-slate scoring, and immutable pre-lock persistence. The old
      # 120-second ceiling repeatedly terminated it after the pull was stored but
      # before winner rows were durable.
      Timeout: 600
      MemorySize: 2048
      # A missing canonical evidence slot is material, so ingestion keeps one
"""

OLD_ORDER = """            audit_results.append({"game_date_et": game_date, **_record_snapshot_audit_safe(game_date=game_date, asof=asof, t=t, run=run, date_compact=date_compact, raw_games=raw)})
            prediction_audit_results.append({"game_date_et": game_date, **_record_no_edge_predictions_safe(game_date=game_date, asof=asof, date_compact=date_compact)})
            hot_movement_feature_results.append({"game_date_et": game_date, **_store_hot_movement_features(game_date=game_date, asof=asof, run=run)})
            hot_side_prediction_results.append({"game_date_et": game_date, **_build_read_only_hot_sides(game_date=game_date)})
            game_winner_prediction_results.append({"game_date_et": game_date, **_build_and_store_game_winners(game_date=game_date)})
"""
NEW_ORDER = """            # Persist the customer-facing winner rows immediately after the
            # canonical pull. Optional reporting must never consume the entire
            # invocation budget before durable scoring completes.
            game_winner_prediction_results.append({"game_date_et": game_date, **_build_and_store_game_winners(game_date=game_date)})
            hot_movement_feature_results.append({"game_date_et": game_date, **_store_hot_movement_features(game_date=game_date, asof=asof, run=run)})
            audit_results.append({"game_date_et": game_date, **_record_snapshot_audit_safe(game_date=game_date, asof=asof, t=t, run=run, date_compact=date_compact, raw_games=raw)})
            prediction_audit_results.append({"game_date_et": game_date, **_record_no_edge_predictions_safe(game_date=game_date, asof=asof, date_compact=date_compact)})
            if event.get("httpMethod") or event.get("requestContext"):
                hot_side_prediction_results.append({"game_date_et": game_date, **_build_read_only_hot_sides(game_date=game_date)})
            else:
                hot_side_prediction_results.append({
                    "game_date_et": game_date,
                    "ok": True,
                    "skipped": True,
                    "reason": "scheduled_ingestion_prioritizes_durable_game_winner_persistence",
                    "readEndpoint": "/v1/predictions/mlb/hot-sides",
                })
"""

OLD_INVARIANTS = """if ingest_resource.count('MaximumRetryAttempts: 1') != 2:
    violations.append('canonical MLB pull must retain exactly one bounded idempotent retry')
if '"days_ahead":0' not in text and '"days_ahead": 0' not in text:
"""
NEW_INVARIANTS = """if ingest_resource.count('MaximumRetryAttempts: 1') != 2:
    violations.append('canonical MLB pull must retain exactly one bounded idempotent retry')
if 'Timeout: 600' not in ingest_resource:
    violations.append('canonical MLB pull timeout must allow full-slate winner persistence')
if 'MemorySize: 2048' not in ingest_resource:
    violations.append('canonical MLB pull memory must provide sufficient scoring CPU capacity')
manual_pull_source = Path('hello_world/mlb_manual_pull.py').read_text()
if 'scheduled_ingestion_prioritizes_durable_game_winner_persistence' not in manual_pull_source:
    violations.append('scheduled MLB pull does not skip redundant read-only hot-side rebuilding')
try:
    canonical_store_position = manual_pull_source.index('canonical = _store_canonical_pull_history')
    winner_store_position = manual_pull_source.index('game_winner_prediction_results.append', canonical_store_position)
    movement_store_position = manual_pull_source.index('hot_movement_feature_results.append', canonical_store_position)
    audit_position = manual_pull_source.index('_record_snapshot_audit_safe', canonical_store_position)
    hot_side_position = manual_pull_source.index('_build_read_only_hot_sides', canonical_store_position)
    shadow_position = manual_pull_source.index('_capture_bbs_shadow_safe', canonical_store_position)
    if not canonical_store_position < winner_store_position < movement_store_position < audit_position < hot_side_position < shadow_position:
        violations.append('durable MLB winner persistence is not prioritized ahead of optional diagnostics')
except ValueError:
    violations.append('MLB scoring priority markers are missing from the canonical pull writer')
if '"days_ahead":0' not in text and '"days_ahead": 0' not in text:
"""


def _replace_once(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return False
    if old not in text:
        raise RuntimeError(f"{label} migration anchor missing in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


def apply() -> dict[str, bool]:
    return {
        "templateCapacityUpdated": _replace_once(
            TEMPLATE,
            OLD_CAPACITY,
            NEW_CAPACITY,
            "MLB audited-pull capacity",
        ),
        "writerPriorityUpdated": _replace_once(
            WRITER,
            OLD_ORDER,
            NEW_ORDER,
            "MLB durable scoring priority",
        ),
        "deploymentInvariantsUpdated": _replace_once(
            INVARIANTS,
            OLD_INVARIANTS,
            NEW_INVARIANTS,
            "MLB scoring runtime deployment invariants",
        ),
    }


def main() -> None:
    changed = apply()
    print(
        "MLB scoring runtime fix applied: "
        + ", ".join(f"{key}={value}" for key, value in changed.items())
    )


if __name__ == "__main__":
    main()
