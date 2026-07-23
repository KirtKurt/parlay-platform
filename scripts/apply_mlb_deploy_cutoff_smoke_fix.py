#!/usr/bin/env python3
"""Make the deployed MLB lifecycle smoke safe for historical same-day slates.

The migration changes only the read-only post-deploy smoke. It never calls AWS,
changes prediction logic, or permits a late prediction write.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"
STEP_START = "      - name: Smoke test read-only MLB lock status\n"
STEP_END = "      - name: Verify The Odds API without writing an unscheduled pull\n"

OLD_IMPORT = "          from scripts.mlb_deploy_http_probe import fetch_json_object\n"
NEW_IMPORT = (
    OLD_IMPORT
    + "          from scripts.mlb_deploy_cutoff_smoke_policy import "
    + "historical_lifecycle_acceptance\n"
)

OLD_LOOP = """          prediction_deadline = time.monotonic() + 20 * 60
          while True:
              predictions = fetch(
                  prediction_url,
                  deadline=prediction_deadline,
                  max_attempts=1,
              )
              winner_rows = [
                  row
                  for row in predictions.get('predictions') or []
                  if isinstance(row, dict) and row.get('predictedWinner') not in (None, '')
              ]
              prelock_winner_rows = [
                  row for row in winner_rows if row.get('lockedPrediction') is not True
              ]
              if winner_rows and not prelock_winner_rows:
                  # Immutable locks from an earlier runtime are never rewritten.
                  break
              if prelock_winner_rows and all(
                  row.get('probabilityContractVersion')
                  == 'MLB-PREDICTION-PROBABILITY-CONTRACT-v1-canonical-model-direction'
                  for row in prelock_winner_rows
              ):
                  break
              if time.monotonic() >= prediction_deadline:
                  raise SystemExit('No fresh persisted canonical probability-contract predictions appeared within 20 minutes')
              print('Waiting for the next protected MLB pull to persist the canonical probability contract')
              time.sleep(30)
"""

NEW_LOOP = """          prediction_deadline = time.monotonic() + 20 * 60
          historical_no_late_backfill = False
          while True:
              predictions = fetch(
                  prediction_url,
                  deadline=prediction_deadline,
                  max_attempts=1,
              )
              winner_rows = [
                  row
                  for row in predictions.get('predictions') or []
                  if isinstance(row, dict) and row.get('predictedWinner') not in (None, '')
              ]
              prelock_winner_rows = [
                  row for row in winner_rows if row.get('lockedPrediction') is not True
              ]
              historical_no_late_backfill = historical_lifecycle_acceptance(
                  predictions,
                  status_rows,
                  game_count,
              )
              if historical_no_late_backfill:
                  print(json.dumps({
                      'diagnostic': 'all_tminus45_cutoffs_passed_without_valid_pregame_predictions',
                      'lateBackfillPerformed': False,
                      'lifecycleRowCount': len(predictions.get('predictions') or []),
                  }, indent=2))
                  break
              if winner_rows and not prelock_winner_rows:
                  # Immutable locks from an earlier runtime are never rewritten.
                  break
              if prelock_winner_rows and all(
                  row.get('probabilityContractVersion')
                  == 'MLB-PREDICTION-PROBABILITY-CONTRACT-v1-canonical-model-direction'
                  for row in prelock_winner_rows
              ):
                  break
              if time.monotonic() >= prediction_deadline:
                  raise SystemExit('No fresh persisted canonical probability-contract predictions or complete post-cutoff lifecycle appeared within 20 minutes')
              print('Waiting for the next protected MLB pull to persist the canonical probability contract')
              time.sleep(30)
"""

OLD_DEFECT = """          if predictions.get('operationalDefect') is True:
              raise SystemExit('Prediction lifecycle reports an operational defect after deployment')
"""
NEW_DEFECT = """          if predictions.get('operationalDefect') is True and not historical_no_late_backfill:
              raise SystemExit('Prediction lifecycle reports an operational defect after deployment')
"""


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"MLB deploy smoke migration anchor missing: {label}")
    return text.replace(old, new, 1)


def apply() -> bool:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.find(STEP_START)
    end = text.find(STEP_END, start + len(STEP_START))
    if start < 0 or end < 0:
        raise RuntimeError("MLB deploy smoke step boundaries are missing")
    prefix = text[:start]
    step = text[start:end]
    suffix = text[end:]
    before = step
    step = _replace_once(step, OLD_IMPORT, NEW_IMPORT, "policy import")
    step = _replace_once(step, OLD_LOOP, NEW_LOOP, "prediction wait loop")
    step = _replace_once(step, OLD_DEFECT, NEW_DEFECT, "operational-defect exception")
    changed = step != before
    WORKFLOW.write_text(prefix + step + suffix, encoding="utf-8")
    return changed


def main() -> None:
    changed = apply()
    print(f"MLB deploy cutoff-aware smoke migration applied: changed={changed}")


if __name__ == "__main__":
    main()
