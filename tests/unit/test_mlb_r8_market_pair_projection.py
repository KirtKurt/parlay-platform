from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_canonical_final_labels_v1 as canonical
import mlb_ml_dual_model_v2 as dual_model


def _unlabeled_row() -> dict:
    features = {
        name: 0.0
        for name in set(dual_model.OUTCOME_FEATURES + dual_model.RELIABILITY_FEATURES)
    }
    return {
        "gameId": "07d39d9ad653030c4c89d9a08c4071f5",
        "slateDateEt": "2026-08-31",
        "commenceTime": "2026-08-31T22:05:00+00:00",
        "homeTeam": "Home",
        "awayTeam": "Away",
        "predictedSide": "home",
        "lockedAmericanOdds": -120,
        "homeMarketDeVigProbability": 0.65005,
        "awayMarketDeVigProbability": 0.34995,
        "marketProbability": 0.65005,
        "marketProbabilitySourceAtUtc": "2026-08-31T21:15:26.597438+00:00",
        "marketProbabilityVersion": (
            "MLB-MARKET-DEVIG-BASELINE-v1-canonical-pull-slot"
        ),
        "marketProbabilityFingerprint": "immutable-market-fingerprint",
        "frozenFeatureVector": {
            "fingerprint": "immutable-vector-fingerprint",
            "features": features,
        },
        "fundamentalsSnapshotV2": {
            "version": dual_model.FUNDAMENTALS_VERSION,
        },
    }


def test_both_canonical_loader_projections_restore_the_exact_lock_fields() -> None:
    source = (ROOT / "hello_world/mlb_canonical_final_labels_v1.py").read_text()
    assert source.count("**_market_probability_projection(locked)") == 2

    locked = _unlabeled_row()
    projected = canonical._market_probability_projection(locked)
    assert projected == {
        field: locked[field]
        for field in canonical.MARKET_PROBABILITY_PROJECTION_FIELDS
    }
    assert projected is not locked


def test_exact_top_level_pair_is_consumed_without_mutating_the_vector() -> None:
    row = _unlabeled_row()
    original_vector = copy.deepcopy(row["frozenFeatureVector"])

    record = dual_model.record_from_unlabeled_lock(row)

    assert record["marketHomeProbability"] == 0.65005
    assert record["marketAwayProbability"] == 0.34995
    assert row["frozenFeatureVector"] == original_vector
    assert "homeMarketDeVigProbability" not in row["frozenFeatureVector"]
    assert "awayMarketDeVigProbability" not in row["frozenFeatureVector"]


def test_projection_never_reconstructs_a_missing_pair_member() -> None:
    row = _unlabeled_row()
    row.pop("awayMarketDeVigProbability")

    projected = canonical._market_probability_projection(row)

    assert "awayMarketDeVigProbability" not in projected
    with pytest.raises(
        ValueError,
        match="same-time de-vigged market probability pair is required",
    ):
        dual_model.record_from_unlabeled_lock(row)
