from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(".github/workflows/mlb-v8-historical-context-backfill.yml")


def test_workflow_runs_isolated_context_backfill_and_canonical_settled_tick():
    text = WORKFLOW.read_text()

    assert "run_mlb_v8_historical_context_backfill_entrypoint.py" in text
    assert "MLB_V8_HISTORICAL_CONTEXT#V1" in text
    assert '"mode":"orchestrate"' in text
    assert "manualCursorMutation':False" in text
    assert "mlb-supervised-shadow-v2-recurring.yml" in text
    assert "productionAuthorityChanged') is False" in text


def test_workflow_revalidates_unchanged_selection_guard_thresholds():
    text = WORKFLOW.read_text()

    assert "guard.MIN_OOF_ACCURACY_UPLIFT == 0.005" in text
    assert "guard.MIN_OOF_NET_CORRECT == 3" in text
    assert "guard.MAX_WORST_FOLD_ACCURACY_REGRESSION == 0.01" in text
    assert "guard.MIN_POSITIVE_FOLD_RATIO == 2.0 / 3.0" in text
    assert "guard.MIN_FUNDAMENTALS_COVERAGE == 0.50" in text
