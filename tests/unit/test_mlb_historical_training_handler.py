from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mlb_historical_daily_optimizer_v15_11.py"
SPEC = importlib.util.spec_from_file_location("mlb_historical_cli_v15_11", SCRIPT)
assert SPEC and SPEC.loader
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


def _schedule():
    return {
        "games": [
            {
                "game_id": "event-1",
                "slate_date": "2025-06-01",
                "commence_time": "2025-06-01T17:05:00Z",
                "home_team": "Home 1",
                "away_team": "Away 1",
            },
            {
                "game_id": "event-2",
                "slate_date": "2025-06-01",
                "commence_time": "2025-06-02T00:10:00Z",
                "home_team": "Home 2",
                "away_team": "Away 2",
            },
        ]
    }


def test_plan_is_zero_call_and_records_the_credit_estimate():
    plan = CLI.build_request_plan(_schedule(), credits_per_request=10)
    assert plan["paid_usage_authorized"] is False
    assert plan["request_count"] == len(plan["requests"])
    assert plan["estimated_credits"] == plan["request_count"] * 10
    assert plan["requests"][0]["requested_at_utc"] == "2025-06-01T05:00:00Z"
    assert plan["plan_sha256"]


def test_backfill_refuses_missing_paid_usage_confirmation_before_http(tmp_path, monkeypatch):
    plan = CLI.build_request_plan(_schedule(), credits_per_request=10)
    called = False

    def forbidden_fetch(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("HTTP must not run")

    monkeypatch.setattr(CLI, "_fetch_json", forbidden_fetch)
    with pytest.raises(PermissionError, match="confirmation"):
        CLI.execute_backfill(
            plan,
            output_dir=tmp_path,
            api_key="test-key",
            confirmation="not authorized",
            max_credits=100000,
        )
    assert called is False


def test_backfill_refuses_credit_estimate_above_explicit_ceiling(tmp_path, monkeypatch):
    plan = CLI.build_request_plan(_schedule(), credits_per_request=10)
    called = False

    def forbidden_fetch(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("HTTP must not run")

    monkeypatch.setattr(CLI, "_fetch_json", forbidden_fetch)
    with pytest.raises(PermissionError, match="exceed"):
        CLI.execute_backfill(
            plan,
            output_dir=tmp_path,
            api_key="test-key",
            confirmation=CLI.PAID_CONFIRMATION,
            max_credits=1,
        )
    assert called is False


def test_promotion_dry_run_still_requires_exact_cutover_phrase():
    report = {
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
        "validation_daily": [
            {
                "slate_date": f"2025-07-{day:02d}",
                "coverage": 1.0,
                "accuracy": 0.80,
                "missing_game_ids": [],
                "extra_game_ids": [],
                "duplicate_game_ids": [],
            }
            for day in range(1, 15)
        ],
        "audit_daily": [
            {
                "slate_date": f"2025-08-{day:02d}",
                "coverage": 1.0,
                "accuracy": 0.80,
                "missing_game_ids": [],
                "extra_game_ids": [],
                "duplicate_game_ids": [],
            }
            for day in range(1, 15)
        ],
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
    with pytest.raises(PermissionError, match="confirmation"):
        CLI.execute_promotion(
            report,
            experiment_id="exp-1",
            confirmation="wrong",
            execute=False,
            table_name=None,
            region="us-east-1",
        )
    result = CLI.execute_promotion(
        report,
        experiment_id="exp-1",
        confirmation=CLI.PROMOTION_CONFIRMATION,
        execute=False,
        table_name=None,
        region="us-east-1",
    )
    assert result["dry_run"] is True
    assert result["records"]["cutover"]["legacy_fallback_allowed"] is False
    assert result["records"]["cutover"]["automatic_wager_allowed"] is False
