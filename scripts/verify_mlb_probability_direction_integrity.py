#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_runtime_overlay as overlay


def _signals():
    return {
        "homeSignal": {
            "team": "Philadelphia Phillies",
            "side": "home",
            "score": 38.77,
            "probLatest": 0.541594,
            "fairProbability": 0.541594,
            "americanOdds": -118,
            "priceBook": "fanduel",
            "marketSide": "favorite",
            "tags": ["BOOK_AGREEMENT", "FAVORITE", "LOW_PULL_DEPTH"],
        },
        "awaySignal": {
            "team": "New York Mets",
            "side": "away",
            "score": 19.96,
            "probLatest": 0.458406,
            "fairProbability": 0.458406,
            "americanOdds": 108,
            "priceBook": "fanduel",
            "marketSide": "underdog",
            "tags": ["BOOK_AGREEMENT", "UNDERDOG", "LOW_PULL_DEPTH"],
        },
    }


def _base():
    return {
        "homeTeam": "Philadelphia Phillies",
        "awayTeam": "New York Mets",
        "score": 38.77,
        "winProbability": 0.2817,
        "winProbabilityPct": 60.96,
        "calibratedWinProbability": 0.3904,
        "tags": ["BOOK_AGREEMENT", "FAVORITE", "LOW_PULL_DEPTH"],
        **_signals(),
    }


def test_stale_row_probability_does_not_reverse_market_leader():
    row = {**_base(), "predictedSide": "home", "predictedWinner": "Philadelphia Phillies", "opponent": "New York Mets"}
    overlay._normalize_probability_fields(row, None)
    assert row["predictedSide"] == "home"
    assert row["predictedWinner"] == "Philadelphia Phillies"
    assert row["opponent"] == "New York Mets"
    assert row["winProbability"] == 0.541594
    assert row["winProbabilityPct"] == 54.16
    assert row.get("probabilityCorrectionApplied") is not True


def test_below_half_selected_side_flips_every_bound_field():
    # Reproduces the July 16 proof defect: the row said Mets/away but its
    # selected-side market probability was below 50% and its tags/score belonged
    # to Philadelphia. The integrity layer must correct the entire row together.
    row = {**_base(), "predictedSide": "away", "predictedWinner": "New York Mets", "opponent": "New York Mets"}
    overlay._normalize_probability_fields(row, None)
    assert row["probabilityCorrectionApplied"] is True
    assert row["predictedSide"] == "home"
    assert row["predictedWinner"] == "Philadelphia Phillies"
    assert row["opponent"] == "New York Mets"
    assert row["winProbability"] == 0.541594
    assert row["winProbabilityPct"] == 54.16
    assert row["score"] == 38.77
    assert row["americanOdds"] == -118
    assert "FAVORITE" in row["tags"]
    assert "UNDERDOG" not in row["tags"]
    assert "PROBABILITY_DIRECTION_INTEGRITY_CORRECTION" in row["tags"]


def test_side_winner_identity_mismatch_is_repaired():
    row = {**_base(), "predictedSide": "home", "predictedWinner": "New York Mets", "opponent": "Philadelphia Phillies"}
    overlay._normalize_probability_fields(row, None)
    assert row["probabilityCorrectionApplied"] is True
    assert row["probabilityCorrectionReason"] == "predicted_winner_did_not_match_predicted_side"
    assert row["predictedWinner"] == "Philadelphia Phillies"
    assert row["opponent"] == "New York Mets"


def main():
    test_stale_row_probability_does_not_reverse_market_leader()
    test_below_half_selected_side_flips_every_bound_field()
    test_side_winner_identity_mismatch_is_repaired()
    print("PASS: MLB probability, direction, winner, opponent, score, price, and tags remain side-consistent")


if __name__ == "__main__":
    main()
