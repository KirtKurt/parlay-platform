"""Recover strict-prior 15-day starter form for holdout-free KS1 development rows.

This reader is development-only. Its caller must separate the frozen qualification
holdout before invoking :func:`enrich_frame`. Starter identity comes from the exact
versioned pre-T10 MLB team-context object already bound to each game-table row, and
pitching results come from the exact official-history object bound to the input proof.
No provider request, label-dependent row selection, prediction write, or serving
mutation occurs here.

The ordinary KS1 table already carries 7- and 30-day starter windows but the requested
middle 15-day starter form is not part of the shared serving schema. This module fills
that historical-development gap with official-box FIP and ERA only. It deliberately
does not approximate a 15-day Statcast/xwOBA value from unrelated windows.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date as calendar_date, timedelta
import json
import math

import pandas as pd

from ks1.features import PITCH, counts, pitching, utc
from ks1.historical_feed import feed_team_context
from ks1.historical_individual_bullpen_enrichment import (
    _history,
    _read_exact,
    _receipt,
)

CONTRACT = "KS1-historical-starter-15d-development-enrichment-v1"
WINDOW_DAYS = 15
METRICS = ("fip", "era")
COLUMNS = tuple(
    f"dev_starter15_{side}_{metric}"
    for side in ("home", "away") for metric in METRICS
)
SUPPORTED = {"SUPPORTED_V1_COMPLETE", "SUPPORTED_V1_EXPLICIT_MISSING"}


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _eligible(row):
    return (row.get("lineup_bullpen_context_evidence") == "historical_timecoded_mlb_feed"
            and row.get("historical_lineup_bullpen_context_status") in SUPPORTED)


def _starter_values(history, context, row, side):
    source = context["sides"][side]
    starter_id = str(source.get("probable_pitcher_id") or "")
    if not starter_id:
        raise ValueError("starter15_probable_pitcher_unavailable")

    recorded = row.get(side + "_starter_id")
    if recorded is not None and not pd.isna(recorded) and str(recorded) != starter_id:
        raise ValueError("starter15_pregame_identity_mismatch")

    target = calendar_date.fromisoformat(str(row["date"]))
    cutoff_at = utc(context["as_of"])
    completed = [
        item for item in history.rows
        if item["completed"] < cutoff_at and item["day"] < target
    ]
    eligible = [item for item in completed if item["day"].year == target.year]
    league_pitchers = [item["starters"] for item in eligible]
    league_pitch = {
        "kbb": counts(league_pitchers, PITCH)[0],
        "whip": counts(league_pitchers, ("outs", "hits", "baseOnBalls"))[0],
    }
    appearances = [
        (item, player["stats"])
        for item in completed
        if item["day"] >= target - timedelta(days=WINDOW_DAYS)
        for player in item["players"]
        if player["id"] == starter_id
    ]
    summary = pitching([stats for _, stats in appearances], league_pitch)
    return {metric: _finite(summary.get(metric)) for metric in METRICS}


def enrich_frame(frame: pd.DataFrame, s3, proof, minimum_nonmissing=300):
    """Add official-box 15-day starter FIP/ERA to a holdout-free frame."""
    enriched = frame.copy(deep=True)
    history, official_source, official_games = _history(s3, proof, enriched)
    candidates = [
        (index, row.to_dict()) for index, row in enriched.iterrows()
        if _eligible(row.to_dict())
    ]
    failures = Counter()
    contexts = []
    bindings = []

    def read_context(item):
        index, row = item
        try:
            receipt = _receipt(row.get("historical_lineup_bullpen_context_source"))
            entry = json.loads(_read_exact(s3, receipt))
            context = feed_team_context({**entry, "receipt": {
                "bucket": receipt["bucket"],
                "key": receipt["key"],
                "versionId": receipt["versionId"],
                "sha256": receipt["sha256"],
            }})
            if context is None or str(context["game_id"]) != str(row["game_id"]):
                raise ValueError("starter15_team_context_identity_mismatch")
            if any(str(context["sides"][side]["team_id"]) != str(row[side + "_id"])
                   for side in ("home", "away")):
                raise ValueError("starter15_team_identity_mismatch")
            return index, row, context, {
                "game_id": str(row["game_id"]),
                "bucket": receipt["bucket"],
                "key": receipt["key"],
                "versionId": receipt["versionId"],
                "sha256": receipt["sha256"],
            }, None
        except Exception as exc:
            return index, row, None, None, str(exc) or type(exc).__name__

    with ThreadPoolExecutor(max_workers=16) as pool:
        for index, row, context, binding, error in pool.map(read_context, candidates):
            if context is None:
                failures[error] += 1
                continue
            contexts.append((index, row, context))
            bindings.append(binding)

    recovered = []
    for index, row, context in contexts:
        values = {}
        try:
            for side in ("home", "away"):
                summary = _starter_values(history, context, row, side)
                for metric, value in summary.items():
                    values[f"dev_starter15_{side}_{metric}"] = value
        except Exception as exc:
            failures[str(exc) or type(exc).__name__] += 1
            continue
        recovered.append(values)
        for column, value in values.items():
            enriched.at[index, column] = value

    coverage = {
        column: sum(values.get(column) is not None for values in recovered)
        for column in COLUMNS
    }
    report = {
        "contract": CONTRACT,
        "input_rows": len(frame),
        "eligible_timecoded_rows": len(candidates),
        "exact_team_context_rows_verified": len(contexts),
        "feature_rows_recovered": len(recovered),
        "source_failures": dict(sorted(failures.items())),
        "nonmissing_coverage": coverage,
        "minimum_nonmissing": minimum_nonmissing,
        "minimum_reached_features": sorted(
            column for column, count in coverage.items() if count >= minimum_nonmissing
        ),
        "official_history_source": {
            key: official_source.get(key)
            for key in ("bucket", "key", "versionId", "sha256", "complete_years")
        },
        "official_history_games_loaded": official_games,
        "team_context_binding_count": len(bindings),
        "window_days": WINDOW_DAYS,
        "metrics": list(METRICS),
        "statcast_15d_approximated": False,
        "label_dependent_selection": False,
        "provider_requests": 0,
        "prediction_writes": 0,
        "official_ledger_writes": 0,
    }
    return enriched, report
