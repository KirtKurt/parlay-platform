from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / ".github/workflows/deploy.yml"
TEST = ROOT / "tests/unit/test_mlb_deploy_no_champion_smoke.py"
WORKFLOW = ROOT / ".github/workflows/apply-mlb-deploy-no-champion-smoke-fix-once.yml"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    text = replace_once(
        text,
        """          from scripts.mlb_deploy_http_probe import fetch_json_object
          from scripts.mlb_deploy_cutoff_smoke_policy import historical_lifecycle_acceptance
""",
        """          from scripts.mlb_deploy_http_probe import fetch_json_object
          from scripts.mlb_deploy_cutoff_smoke_policy import historical_lifecycle_acceptance
          from scripts.verify_mlb_authority_response import (
              fetch_json as fetch_authority_json,
              verify_payload as verify_authority_payload,
          )
""",
        label="authority verifier import",
    )
    text = replace_once(
        text,
        """          while True:
              predictions = fetch(
                  prediction_url,
                  deadline=prediction_deadline,
                  max_attempts=1,
              )
              winner_rows = [
""",
        """          while True:
              remaining = prediction_deadline - time.monotonic()
              if remaining <= 0:
                  raise SystemExit('No valid public authority or prediction lifecycle appeared within 20 minutes')
              prediction_http_status, prediction_body = fetch_authority_json(
                  prediction_url,
                  max(1, min(45, remaining)),
              )
              authority_report = verify_authority_payload(
                  prediction_http_status,
                  prediction_body,
              )
              if prediction_http_status == 503:
                  if (
                      authority_report.get('ok') is not True
                      or authority_report.get('state') != 'NO_QUALIFIED_CHAMPION'
                      or authority_report.get('publicationClosed') is not True
                  ):
                      raise SystemExit('Public MLB predictions 503 does not satisfy the no-qualified-champion contract')
                  status_winner_rows = [
                      row
                      for row in status_rows
                      if isinstance(row, dict)
                      and row.get('predictedWinner') not in (None, '')
                  ]
                  if (
                      len(status_winner_rows) != game_count
                      or any(row.get('lockedPrediction') is not True for row in status_winner_rows)
                  ):
                      raise SystemExit('Publication is closed but immutable lock lifecycle is incomplete')
                  predictions = {
                      'ok': False,
                      'sport': 'mlb',
                      'gameCount': game_count,
                      'lockedPredictionCount': locked_predictions,
                      'officialPredictionCount': locked_predictions,
                      'lockedStatusCount': locked_statuses,
                      'noPredictionDataCount': terminal_no_data,
                      'lockStatusComplete': payload.get('lockStatusComplete'),
                      'canonicalPredictionComplete': payload.get('canonicalPredictionComplete'),
                      'operationalDefect': payload.get('operationalDefect'),
                      'predictions': status_rows,
                      'publicationClosed': True,
                      'publicAuthorityState': 'NO_QUALIFIED_CHAMPION',
                      'authorityContractVersion': authority_report.get('authorityContractVersion'),
                  }
                  historical_no_late_backfill = True
                  print(json.dumps({
                      'diagnostic': 'publication_closed_without_qualified_champion',
                      'publicAuthorityState': 'NO_QUALIFIED_CHAMPION',
                      'immutableLockedWinnerCount': len(status_winner_rows),
                      'lateBackfillPerformed': False,
                  }, indent=2))
                  break
              if prediction_http_status != 200 or authority_report.get('ok') is not True:
                  raise SystemExit('Public MLB predictions authority response is invalid')
              predictions = prediction_body
              winner_rows = [
""",
        label="predictions authority loop",
    )
    DEPLOY.write_text(text, encoding="utf-8")
    TEST.write_text(
        '''from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")


def test_deploy_smoke_accepts_only_strict_fail_closed_no_champion_state():
    assert "verify_authority_payload" in WORKFLOW
    assert "prediction_http_status == 503" in WORKFLOW
    assert "authority_report.get('state') != 'NO_QUALIFIED_CHAMPION'" in WORKFLOW
    assert "authority_report.get('publicationClosed') is not True" in WORKFLOW
    assert "len(status_winner_rows) != game_count" in WORKFLOW
    assert "any(row.get('lockedPrediction') is not True" in WORKFLOW
    assert "publicAuthorityState': 'NO_QUALIFIED_CHAMPION'" in WORKFLOW
    assert "historical_no_late_backfill = True" in WORKFLOW


def test_deploy_smoke_does_not_open_publication_or_relax_lock_rules():
    assert "'publicationClosed': True" in WORKFLOW
    assert "lateBackfillPerformed': False" in WORKFLOW
    assert "lockMinutesBeforeEachGame') != 45" in WORKFLOW
    assert "AUTOMATIC_WAGER" not in WORKFLOW.split("publication_closed_without_qualified_champion", 1)[1][:800]
''',
        encoding="utf-8",
    )
    for path in (Path(__file__), WORKFLOW):
        if path.exists():
            path.unlink()


if __name__ == "__main__":
    main()
