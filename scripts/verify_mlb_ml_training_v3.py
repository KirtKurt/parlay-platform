#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_frozen_features as frozen
import mlb_ml_training_v3 as training
import mlb_ml_runtime_safety_patch as safety


def locked_row(i: int, correct: bool = True):
    home = f"Home {i}"
    away = f"Away {i}"
    start = f"2026-08-{1 + i // 20:02d}T{12 + (i % 10):02d}:00:00Z"
    row = {
        "id": f"game-{i}", "gameId": f"game-{i}", "slateDateEt": "2026-08-01",
        "commenceTime": start, "homeTeam": home, "awayTeam": away,
        "predictedSide": "home" if i % 2 == 0 else "away",
        "predictedWinner": home if i % 2 == 0 else away,
        "winner": (home if i % 2 == 0 else away) if correct else (away if i % 2 == 0 else home),
        "correct": correct, "status": "GRADED", "teamWinProbabilityPct": 55 + (i % 10) * 0.5,
        "americanOdds": -110 if i % 2 == 0 else 105,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1-locked-official-playable-separate",
        "slatePredictionLock": {"locked": True, "lockAtUtc": "2026-08-01T11:00:00Z", "latestScoringPullAt": "2026-08-01T10:55:00Z"},
        "lockedPrediction": True, "lockedAtUtc": "2026-08-01T11:00:00Z", "predictionSourcePullAt": "2026-08-01T10:55:00Z",
        "tags": ["SLATE_LOCKED", "BOOK_AGREEMENT"],
        "homeSignal": {"team": home, "side": "home", "marketConsensusProbability": 0.56 + (i % 5) * 0.01, "probStart": 0.54, "delta": 0.02, "score": 60 + i % 10, "bookDivergence": 0.01, "reversalCount": i % 2, "americanOdds": -125, "tags": ["BOOK_AGREEMENT"]},
        "awaySignal": {"team": away, "side": "away", "marketConsensusProbability": 0.44 - (i % 5) * 0.01, "probStart": 0.46, "delta": -0.02, "score": 40 - i % 10, "bookDivergence": 0.01, "reversalCount": i % 2, "americanOdds": 115, "tags": ["BOOK_AGREEMENT"]},
        "lockedCardAudit": {"lockedFlag": True, "preventsLateRows": True, "providerGameId": f"game-{i}"},
    }
    return frozen.freeze_row(row, coverage_complete=True)


def main() -> int:
    first = locked_row(1, True)
    frozen_copy = copy.deepcopy(first["frozenOutcomeFeatures"])
    first["homeSignal"]["score"] = 999
    assert first["frozenOutcomeFeatures"] == frozen_copy

    legacy = locked_row(999, True)
    legacy.pop("mlFeatureFreeze", None)
    legacy.pop("frozenOutcomeFeatures", None)
    legacy.pop("frozenReliabilityFeatures", None)
    rows = [locked_row(i, correct=(i % 3 != 0)) for i in range(140)] + [legacy]
    clean, cohort = training.clean_training_rows(rows)
    assert len(clean) == 140
    assert cohort["quarantinedRows"] == 1
    assert "missing_current_frozen_feature_vector" in cohort["exclusionReasons"]

    sample = copy.deepcopy(clean[0])
    sample["predictedWinner"] = sample["awayTeam"]
    sample["winner"] = sample["homeTeam"]
    assert training.outcome_records([sample])[0]["label"] == 1

    split = training.chronological_split(training.outcome_records(clean))
    assert split["train"] and split["validation"] and split["test"]
    assert split["train"][-1]["commenceTime"] <= split["validation"][0]["commenceTime"]
    assert split["validation"][-1]["commenceTime"] <= split["test"][0]["commenceTime"]

    with tempfile.TemporaryDirectory() as temp:
        old = os.getcwd(); os.chdir(temp)
        try:
            manifest = training.train(rows)
            assert manifest["cleanRowCount"] == 140
            assert manifest["automaticPromotion"] is False
            champion = json.load(open(training.RELIABILITY_CHAMPION_PATH, encoding="utf-8"))
            assert champion["productionApproved"] is False
        finally:
            os.chdir(old)

    class Overlay:
        MODEL_PATH = "unused"
    overlay = Overlay()
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "legacy.json"
        path.write_text(json.dumps({"ok": True, "validatedAgainstTarget": True}), encoding="utf-8")
        os.environ["INQSI_MLB_ML_MODEL_PATH"] = str(path)
        safety.apply(overlay)
        assert overlay._load_model() is None

    print("MLB ML v3 clean cohort, frozen features, dual labels, chronological validation, and production safety verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
