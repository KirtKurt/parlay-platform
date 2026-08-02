"""Fail-closed deployment contract for the August 2 MLB prospective reset."""

from pathlib import Path


def _resource(text: str, name: str, next_name: str) -> str:
    start = text.index(f"  {name}:\n")
    end = text.index(f"\n  {next_name}:\n", start)
    return text[start:end]


def test_results_scheduler_has_full_slate_capacity():
    text = Path("template.yaml").read_text()
    block = _resource(text, "MLBResultsSchedulerFunction", "MLBMLTrainingFunction")
    assert "Handler: mlb_results_scheduler.lambda_handler" in block
    assert "Timeout: 600" in block
    assert "MemorySize: 2048" in block
    assert "MaximumRetryAttempts: 0" in block


def test_r6_starts_after_permanently_incomplete_slates():
    text = Path("template.yaml").read_text()
    assert "MLB_ML_EXPERIMENT_ID: 'mlb-v2-2026-08-02-future-prospective-r6'" in text
    assert "MLB_ML_RELEASE_CONTRACT_ID: 'mlb-v2-2026-08-02-future-prospective-r6'" in text
    assert "MLB_ML_RELEASE_CUTOFF_UTC: '2026-08-02T04:00:00+00:00'" in text
    assert "INQSI_MLB_ML_AUTO_PROMOTE: 'false'" in text
    assert "mlb-v2-2026-07-29-future-prospective-r5" not in text
    assert "2026-07-29T04:00:00+00:00" not in text


def test_existing_lock_and_promotion_safety_is_unchanged():
    text = Path("template.yaml").read_text()
    assert "Handler: mlb_daily_pick_lock_protected.lambda_handler" in text
    assert "Schedule: rate(1 minute)" in text
    assert "MLB_REQUIRE_ALL_GAMES_FOR_LOCK: 'true'" in text
    assert "INQSI_MLB_ML_AUTO_PROMOTE: 'false'" in text
