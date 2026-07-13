#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib
import os
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_clean_cohort_hardening_v1 as hardening
import mlb_ml_clean_cohort_v1 as cohort


def base_row():
    row = {
        "status": "GRADED", "id": "integrity-game", "gameId": "integrity-game",
        "slateDateEt": "2026-07-13", "commenceTime": "2026-07-13T20:00:00Z",
        "homeTeam": "Home", "awayTeam": "Away", "winner": "Home",
        "predictedWinner": "Home", "predictedSide": "home", "correct": True,
        "lockedPrediction": True, "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "probabilitySemanticsFixed": True,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1",
        "teamWinProbabilityPct": 58.0,
        "lockedAmericanOdds": -135,
        "priceBook": "fanduel",
        "priceSource": "real_book",
        "slateCoverage": {"coverageComplete": True},
        "slatePredictionLock": {"locked": True, "lockAtUtc": "2026-07-13T18:00:00Z", "latestScoringPullAt": "2026-07-13T17:55:00Z"},
        "lockedCardAudit": {"lockedFlag": True, "lockAtUtc": "2026-07-13T18:00:00Z", "explicitSourceAtUtc": "2026-07-13T17:55:00Z", "preventsLateRows": True, "providerGameId": "integrity-game"},
        "homeSignal": {"marketConsensusProbability": 0.58, "probLatest": 0.58, "americanOdds": -135, "priceBook": "fanduel", "priceSource": "real_book", "tags": ["BOOK_AGREEMENT"]},
        "awaySignal": {"marketConsensusProbability": 0.42, "probLatest": 0.42, "americanOdds": 120, "priceBook": "fanduel", "priceSource": "real_book", "tags": ["BOOK_AGREEMENT"]},
        "fundamentalsSnapshot": {"completenessRatio": 0.0, "numericValues": {}},
    }
    pregame = copy.deepcopy(row); pregame.pop("winner"); pregame.pop("correct"); pregame.pop("status")
    row["frozenFeatureVector"] = cohort.freeze_feature_snapshot(pregame)
    row["frozenFeatureVectorVersion"] = row["frozenFeatureVector"]["version"]
    return row


def _history_module():
    """Import the production serializer even in the dependency-light local test."""
    try:
        return importlib.import_module("inqsi_pull_history")
    except ModuleNotFoundError as exc:
        if exc.name not in {"boto3", "boto3.dynamodb", "boto3.dynamodb.conditions"}:
            raise
        boto3 = ModuleType("boto3")
        boto3.resource = lambda *_args, **_kwargs: SimpleNamespace(Table=lambda _name: None)
        dynamodb = ModuleType("boto3.dynamodb")
        conditions = ModuleType("boto3.dynamodb.conditions")
        conditions.Key = lambda value: value
        sys.modules["boto3"] = boto3
        sys.modules["boto3.dynamodb"] = dynamodb
        sys.modules["boto3.dynamodb.conditions"] = conditions
        return importlib.import_module("inqsi_pull_history")


def _dynamodb_round_trip(value):
    """Use production ddb_safe and boto3's wire codec when it is installed."""
    # The rolling-audit workflow runs this validator before configuring AWS
    # credentials. A test-only region is enough to construct the resource; this
    # test never reads or writes a live table.
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("AWS_REGION", "us-east-1")
    os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    os.environ.setdefault("SNAPSHOTS_TABLE", "")
    safe = _history_module().ddb_safe(value)
    try:
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
        return TypeDeserializer().deserialize(TypeSerializer().serialize(safe))
    except (ImportError, ModuleNotFoundError):
        return copy.deepcopy(safe)


def main() -> int:
    hardening.apply(cohort)
    clean = base_row()
    ok, reasons = cohort.eligibility(clean)
    assert ok is True, reasons
    assert clean["frozenFeatureVector"]["fingerprintVersion"] == cohort.FINGERPRINT_VERSION

    round_tripped = _dynamodb_round_trip(clean)
    assert isinstance(round_tripped["frozenFeatureVector"]["features"]["homeMarketProb"], Decimal)
    assert round_tripped["frozenFeatureVector"]["labels"] == {"homeWon": None, "pickCorrect": None}
    ok, reasons = cohort.eligibility(round_tripped)
    assert ok is True, reasons
    assert cohort.fingerprint_for_vector(round_tripped["frozenFeatureVector"]) == clean["frozenFeatureVector"]["fingerprint"]
    assert "omitted" not in _history_module().ddb_safe({"omitted": None})

    legacy = copy.deepcopy(clean)
    legacy["frozenFeatureVector"].pop("fingerprintVersion", None)
    legacy["frozenFeatureVector"]["fingerprint"] = cohort.fingerprint_for_vector(legacy["frozenFeatureVector"])
    ok, reasons = cohort.eligibility(_dynamodb_round_trip(legacy))
    assert ok is False and "missing_frozen_vector_fingerprint_version" in reasons

    missing_explicit_labels = copy.deepcopy(clean)
    missing_explicit_labels["frozenFeatureVector"]["labels"] = {}
    ok, reasons = cohort.eligibility(missing_explicit_labels)
    assert ok is False and "frozen_vector_explicit_blank_labels_missing" in reasons

    tampered = copy.deepcopy(clean)
    tampered["frozenFeatureVector"]["features"]["homeMarketProb"] = 0.99
    ok, reasons = cohort.eligibility(tampered)
    assert ok is False and "frozen_vector_fingerprint_mismatch" in reasons

    side_tampered = copy.deepcopy(clean)
    side_tampered["frozenFeatureVector"]["predictedSide"] = "away"
    ok, reasons = cohort.eligibility(side_tampered)
    assert ok is False
    assert "frozen_vector_fingerprint_mismatch" in reasons
    assert "frozen_vector_predicted_side_mismatch" in reasons

    price_tampered = copy.deepcopy(clean)
    price_tampered["frozenFeatureVector"]["selectedAmericanOdds"] = -999
    ok, reasons = cohort.eligibility(price_tampered)
    assert ok is False
    assert "frozen_vector_fingerprint_mismatch" in reasons
    assert "frozen_vector_selected_price_mismatch" in reasons

    source_tampered = copy.deepcopy(clean)
    source_tampered["frozenFeatureVector"]["sourcePullAtUtc"] = "2026-07-13T17:54:00+00:00"
    ok, reasons = cohort.eligibility(source_tampered)
    assert ok is False
    assert "frozen_vector_fingerprint_mismatch" in reasons
    assert "frozen_vector_source_timestamp_mismatch" in reasons

    wrong_game = copy.deepcopy(clean)
    wrong_game["id"] = "different-game"
    wrong_game["gameId"] = "different-game"
    ok, reasons = cohort.eligibility(wrong_game)
    assert ok is False and "frozen_vector_game_identity_mismatch" in reasons

    no_price_source = copy.deepcopy(clean)
    no_price_source.pop("priceBook", None)
    no_price_source.pop("priceSource", None)
    no_price_source["homeSignal"].pop("priceBook", None)
    no_price_source["homeSignal"].pop("priceSource", None)
    ok, reasons = cohort.eligibility(no_price_source)
    assert ok is False and "selected_side_odds_source_not_proven" in reasons

    print("MLB frozen feature integrity verified: DynamoDB Decimal round-trip, bound identity/source/selected-price context, and tamper rejection pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
