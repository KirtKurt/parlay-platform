#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_daily_lock_ml_vector_preservation_patch as patch


def fingerprint(vector: dict) -> str:
    source = json.dumps(
        {
            "gameId": vector.get("gameId"),
            "lockAtUtc": vector.get("lockAtUtc"),
            "features": vector.get("features") or {},
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def base_compact(row: dict) -> dict:
    return {
        "gameId": row.get("gameId"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "americanOdds": row.get("americanOdds"),
        "priceBook": row.get("priceBook"),
        "priceSource": row.get("priceSource"),
    }


def valid_row() -> dict:
    vector = {
        "version": patch.EXPECTED_VECTOR_VERSION,
        "createdAtUtc": "2026-07-13T15:00:00+00:00",
        "sourcePullAtUtc": "2026-07-13T14:59:00+00:00",
        "lockAtUtc": "2026-07-13T15:00:00+00:00",
        "gameId": "game-1",
        "slateDateEt": "2026-07-13",
        "commenceTime": "2026-07-13T18:00:00+00:00",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "features": {"homeMarketProb": 0.55, "awayMarketProb": 0.45},
        "labels": {"homeWon": None, "pickCorrect": None},
        "immutableSource": "locked_prediction_row_pre_game_features",
        "derivedOnceFromImmutableLockedRow": True,
    }
    vector["fingerprint"] = fingerprint(vector)
    return {
        "gameId": "game-1",
        "gameIdentity": "provider:game-1",
        "gameKey": "mlb|2026-07-13|away club|home club",
        "slateDateEt": "2026-07-13",
        "commenceTime": "2026-07-13T18:00:00+00:00",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "americanOdds": -115,
        "lockedAmericanOdds": -115,
        "priceBook": "FanDuel",
        "priceSource": "real_book",
        "teamWinProbabilityPct": 55.0,
        "winProbabilityPct": 55.0,
        "winProbabilityMeaning": "estimated_probability_selected_team_wins_game",
        "probabilitySemanticsFixed": True,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1-test",
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "lockedPrediction": True,
        "lockedAtUtc": vector["lockAtUtc"],
        "predictionSourcePullAt": vector["sourcePullAtUtc"],
        "featureVectorFrozenAtLock": True,
        "frozenFeatureVectorVersion": vector["version"],
        "frozenFeatureVector": vector,
        "mlFeatureFreeze": {
            "exactVectorApplied": True,
            "exactVectorCreated": True,
            "completeSlateCoverage": True,
            "trainingEligible": True,
        },
        "slateCoverage": {"coverageComplete": True},
        # These accidental postgame fields must never be copied into the pregame card.
        "winner": "Home Club",
        "correct": True,
        "success": True,
    }


def main() -> int:
    module = SimpleNamespace(_compact_pick=base_compact)
    status = patch.apply(module)
    assert status["ok"] is True
    assert status["failClosed"] is True

    source = valid_row()
    compact = module._compact_pick(source)
    vector = compact["frozenFeatureVector"]

    assert compact["mlLockVectorStorageVerified"] is True
    assert compact["mlLockVectorStorageErrors"] == []
    assert compact["mlLockVectorStorageVersion"] == patch.VERSION
    assert compact["featureVectorFrozenAtLock"] is True
    assert compact["frozenFeatureVectorVersion"] == patch.EXPECTED_VECTOR_VERSION
    assert vector["fingerprint"] == fingerprint(vector)
    assert vector["labels"] == {"homeWon": None, "pickCorrect": None}
    assert compact["lockedAmericanOdds"] == -115
    assert compact["priceBook"] == "FanDuel"
    assert compact["priceSource"] == "real_book"
    assert compact["teamWinProbabilityPct"] == 55.0
    assert compact["predictionSemanticsVersion"].startswith("MLB-OFFICIAL-PREDICTION-SEMANTICS-")
    for forbidden in ("winner", "correct", "success", "homeWon", "pickCorrect"):
        assert forbidden not in compact

    # Deep-copy proof: later mutation of the source row cannot change the write-once card.
    original_probability = vector["features"]["homeMarketProb"]
    source["frozenFeatureVector"]["features"]["homeMarketProb"] = 0.01
    assert compact["frozenFeatureVector"]["features"]["homeMarketProb"] == original_probability

    # Fail closed when the exact vector is missing.
    missing = valid_row()
    missing.pop("frozenFeatureVector")
    missing.pop("frozenFeatureVectorVersion")
    try:
        module._compact_pick(missing)
    except RuntimeError as exc:
        assert "missing_frozen_feature_vector" in str(exc)
    else:
        raise AssertionError("daily lock accepted a pick without the exact frozen vector")

    # Fail closed when a fingerprint does not match the immutable features.
    altered = valid_row()
    altered["frozenFeatureVector"]["features"]["homeMarketProb"] = 0.99
    try:
        module._compact_pick(altered)
    except RuntimeError as exc:
        assert "frozen_vector_fingerprint_mismatch" in str(exc)
    else:
        raise AssertionError("daily lock accepted a mutated frozen vector")

    print(
        "MLB daily lock ML vector preservation verified: exact vector, fingerprint, "
        "timestamps, semantics, selected price, and blank pregame labels survive compaction; "
        "invalid cards fail before storage."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
