#!/usr/bin/env python3
"""Apply the permanent MLB scoring runtime-capacity and priority fix.

The migration is idempotent. It updates the canonical SAM template and moves
persistent winner generation ahead of optional diagnostics in the HOT pull
writer. It never calls AWS. The branch migration workflow executes this file
and commits only the resulting canonical source changes.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template.yaml"
WRITER = ROOT / "hello_world" / "mlb_manual_pull.py"

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
    }


def main() -> None:
    changed = apply()
    print(
        "MLB scoring runtime fix applied: "
        + ", ".join(f"{key}={value}" for key, value in changed.items())
    )


if __name__ == "__main__":
    main()
