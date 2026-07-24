from __future__ import annotations

from datetime import date, timedelta

import pytest

from hello_world.mlb_historical_policy_v1 import (
    CUTOVER_MODE,
    FAIL_CLOSED_MODE,
    INCUMBENT_MODE,
    HistoricalPolicy,
    build_cutover_records,
    chronological_split,
    evaluate_promotion_gate,
    resolve_production_authority,
    score_daily_slates,
)


def _dated_rows(counts):
    rows = []
    game_number = 0
    start = date(2024, 4, 1)
    for day_offset, count in enumerate(counts):
        slate_date = (start + timedelta(days=day_offset)).isoformat()
        for _ in range(count):
            game_number += 1
            rows.append({"slate_date": slate_date, "game_id": f"g{game_number}"})
    return rows


def test_policy_fixes_required_objective():
    policy = HistoricalPolicy()
    policy.validate()
    assert policy.min_train_games == 1000
    assert policy.min_validation_games == 200
    assert policy.min_audit_games == 200
    assert policy.pull_start_local == "01:00"
    assert policy.snapshot_minutes == 15
    assert policy.lock_minutes_before_commence == 45
    assert policy.min_daily_accuracy == 0.80
    assert policy.target_daily_accuracy == 0.90
    assert policy.required_coverage == 1.0


def test_chronological_split_uses_whole_dates_and_1000_200_200_minima():
    # 100 dates x 10 games, followed by 20 x 10, followed by 20 x 10.
    split = chronological_split(_dated_rows([10] * 140))
    assert split.train_games == 1000
    assert split.validation_games == 200
    assert split.audit_games == 200
    assert len(split.validation_dates) == 20
    assert len(split.audit_dates) == 20
    assert max(split.train_dates) < min(split.validation_dates)
    assert max(split.validation_dates) < min(split.audit_dates)


def test_split_rejects_short_48_game_diagnostic_sample():
    with pytest.raises(ValueError, match="1000"):
        chronological_split(_dated_rows([8] * 6))


def _slate(correct: int, total: int = 10):
    outcomes = []
    predictions = []
    for index in range(total):
        game_id = f"g{index}"
        winner = f"winner-{index}"
        outcomes.append(
            {"slate_date": "2025-06-01", "game_id": game_id, "winner": winner}
        )
        predictions.append(
            {
                "slate_date": "2025-06-01",
                "game_id": game_id,
                "pick": winner if index < correct else f"loser-{index}",
            }
        )
    return predictions, outcomes


def test_daily_goal_is_across_complete_slate_not_per_game_confidence():
    predictions, outcomes = _slate(8)
    result = score_daily_slates(predictions, outcomes)[0]
    assert result.official_games == 10
    assert result.correct_games == 8
    assert result.accuracy == 0.80
    assert result.passed is True

    predictions, outcomes = _slate(7)
    result = score_daily_slates(predictions, outcomes)[0]
    assert result.accuracy == 0.70
    assert result.passed is False


def test_missing_game_fails_even_when_remaining_picks_are_correct():
    predictions, outcomes = _slate(10)
    predictions.pop()
    result = score_daily_slates(predictions, outcomes)[0]
    assert result.accuracy == 0.90
    assert result.coverage == 0.90
    assert result.passed is False
    assert result.missing_game_ids == ("g9",)


def _passing_report():
    daily = [
        {
            "slate_date": f"2025-07-{day:02d}",
            "coverage": 1.0,
            "accuracy": 0.80,
            "missing_game_ids": [],
            "extra_game_ids": [],
            "duplicate_game_ids": [],
        }
        for day in range(1, 15)
    ]
    return {
        "sample_counts": {
            "train": 1000,
            "validation": 200,
            "audit": 200,
            "validation_days": 14,
            "audit_days": 14,
        },
        "chronology": {
            "whole_date_partitions": True,
            "strictly_ordered": True,
            "audit_opened_after_selection": True,
        },
        "provenance": {
            "starts_at_0100_et": True,
            "cadence_15_minutes": True,
            "t_minus_45_clipped": True,
            "settled_official_labels": True,
            "no_future_features": True,
        },
        "validation_daily": daily,
        "audit_daily": daily,
        "metrics": {
            "train_accuracy": 0.84,
            "validation_accuracy": 0.82,
            "audit_accuracy": 0.81,
            "validation_brier": 0.20,
            "audit_brier": 0.21,
            "market_validation_brier": 0.21,
            "market_audit_brier": 0.22,
            "validation_log_loss": 0.61,
            "audit_log_loss": 0.62,
            "market_validation_log_loss": 0.62,
            "market_audit_log_loss": 0.63,
        },
        "artifact": {
            "sha256": "a" * 64,
            "sha256_validated": True,
            "immutable": True,
        },
    }


def test_promotion_requires_every_validation_and_audit_date_at_80_percent():
    report = _passing_report()
    assert evaluate_promotion_gate(report).approved is True

    report["audit_daily"][5] = {
        **report["audit_daily"][5],
        "accuracy": 0.79,
    }
    decision = evaluate_promotion_gate(report)
    assert decision.approved is False
    assert any("below_0.80" in blocker for blocker in decision.blockers)


def test_overfit_and_market_regression_are_hard_blockers():
    report = _passing_report()
    report["metrics"]["train_accuracy"] = 0.97
    report["metrics"]["validation_accuracy"] = 0.82
    report["metrics"]["audit_brier"] = 0.25
    decision = evaluate_promotion_gate(report)
    assert decision.approved is False
    assert "train_validation_divergence_exceeds_limit" in decision.blockers
    assert "audit_brier_regressed_vs_market" in decision.blockers


def test_cutover_is_fail_closed_and_never_restores_legacy_automatically():
    assert (
        resolve_production_authority(
            cutover_record=None, champion_record=None, artifact_sha256=None
        )
        == INCUMBENT_MODE
    )
    records = build_cutover_records(
        experiment_id="exp-1",
        artifact_sha256="b" * 64,
        gate_report_sha256="c" * 64,
    )
    assert records["cutover"]["legacy_selection_authority"] is False
    assert records["cutover"]["legacy_fallback_allowed"] is False
    assert records["cutover"]["automatic_legacy_restore_allowed"] is False
    assert records["cutover"]["automatic_wager_allowed"] is False
    assert (
        resolve_production_authority(
            cutover_record=records["cutover"],
            champion_record=None,
            artifact_sha256="b" * 64,
        )
        == FAIL_CLOSED_MODE
    )
    assert (
        resolve_production_authority(
            cutover_record=records["cutover"],
            champion_record=records["champion"],
            artifact_sha256="b" * 64,
        )
        == CUTOVER_MODE
    )
    assert (
        resolve_production_authority(
            cutover_record=records["cutover"],
            champion_record=records["champion"],
            artifact_sha256="wrong",
        )
        == FAIL_CLOSED_MODE
    )
