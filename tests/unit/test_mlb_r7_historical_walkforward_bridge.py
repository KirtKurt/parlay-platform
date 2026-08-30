from __future__ import annotations

import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_historical_daily_optimizer_v1 as historical_optimizer
import mlb_r7_historical_walkforward_bridge as bridge


FEATURE_VERSION = (
    "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v2-lock-safe-temporal-missingness"
)


def _signal(side: str, probability: float, delta: float):
    return {
        "side": side,
        "fairProbability": probability,
        "delta": delta,
        "bookDivergence": 0.01,
        "reversalCount": 0,
        "americanOdds": -120 if probability >= 0.5 else 110,
        "tags": ["BOOK_AGREEMENT"],
        "temporalFeatures": {
            "horizons": {
                "60m": {
                    "velocityPpHr": 1.25 if side == "home" else -1.25,
                },
                "180m": {"volatilityPpPerPull": 0.004},
                "full": {"reversalCount": 0, "coverageRatio": 1.0},
            }
        },
    }


def _record(winner: str = "Home"):
    commence = datetime(2025, 4, 2, 23, 0, tzinfo=timezone.utc)
    lock_at = commence - timedelta(minutes=45)
    return {
        "version": bridge.DATASET_VERSION,
        "slateDateEt": "2025-04-02",
        "officialGamePk": "123",
        "homeTeam": "Home",
        "awayTeam": "Away",
        "commenceTime": commence.isoformat(),
        "winner": winner,
        "homeWon": winner == "Home",
        "homeSignal": _signal("home", 0.56, 0.02),
        "awaySignal": _signal("away", 0.44, -0.02),
        "requestedSlotCount": 4,
        "observedHomePullCount": 4,
        "observedAwayPullCount": 4,
        "predictionLockAtUtc": lock_at.isoformat(),
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
    }


def _dataset(record):
    lock_at = datetime.fromisoformat(record["predictionLockAtUtc"])
    audit = [
        {
            "providerTimestampUtc": (
                lock_at - timedelta(minutes=45 - 15 * index)
            ).isoformat(),
            "matchedOfficialGames": 1,
            "acceptedBeforePerGameLock": 1,
        }
        for index in range(4)
    ]
    dataset = {
        "version": bridge.DATASET_VERSION,
        "slateDateEt": record["slateDateEt"],
        "officialGameCount": 1,
        "eligibleGameCount": 1,
        "exactSlateCoverage": 1.0,
        "completeSlate": True,
        "records": [record],
        "exclusions": [],
        "snapshotAudit": audit,
        "postLockDataExcluded": True,
        "gameSpecificLockClipping": True,
    }
    dataset["fingerprint"] = historical_optimizer.dataset_fingerprint(
        dataset["records"]
    )
    return dataset


def _artifact():
    return {
        "bucket": "historical",
        "key": "mlb/historical-daily-v1/slates/2025-04-02.json",
        "versionId": "version-1",
        "sha256": "a" * 64,
    }


def test_feature_material_is_label_blind_and_label_is_joined_afterward():
    home_record = _record("Home")
    away_record = _record("Away")
    home_row = bridge.materialize_record(
        home_record,
        dataset=_dataset(home_record),
        artifact=_artifact(),
        feature_version=FEATURE_VERSION,
    )
    away_row = bridge.materialize_record(
        away_record,
        dataset=_dataset(away_record),
        artifact=_artifact(),
        feature_version=FEATURE_VERSION,
    )

    assert home_row["featureSnapshot"] == away_row["featureSnapshot"]
    assert home_row["predictedSide"] == away_row["predictedSide"] == "home"
    assert home_row["correct"] is True
    assert away_row["correct"] is False
    assert home_row["selectionUsedOutcomes"] is False
    assert home_row["historicalAdmissionAttestation"][
        "featureMaterializedBeforeLabelJoin"
    ] is True


def test_historical_attestation_is_valid_but_has_no_live_authority():
    record = _record()
    row = bridge.materialize_record(
        record,
        dataset=_dataset(record),
        artifact=_artifact(),
        feature_version=FEATURE_VERSION,
    )
    manifest = {"featureVectorVersion": FEATURE_VERSION}

    assert bridge.validate_historical_record(row, manifest) == (True, [])
    assert row["prospectiveAuthority"] is False
    assert row["liveInferenceAuthority"] is False
    assert row["productionAuthority"] is False

    tampered = copy.deepcopy(row)
    tampered["productionAuthority"] = True
    ok, reasons = bridge.validate_historical_record(tampered, manifest)
    assert ok is False
    assert "productionAuthority_must_be_false" in reasons


def test_feature_or_selection_tampering_fails_closed():
    record = _record()
    row = bridge.materialize_record(
        record,
        dataset=_dataset(record),
        artifact=_artifact(),
        feature_version=FEATURE_VERSION,
    )
    manifest = {"featureVectorVersion": FEATURE_VERSION}

    row["featureSnapshot"]["features"]["selectedScore"] = 99.0
    row["featureSnapshot"]["selectionUsedOutcomes"] = True
    ok, reasons = bridge.validate_historical_record(row, manifest)
    assert ok is False
    assert "historical_feature_fingerprint_mismatch" in reasons
    assert "historical_feature_selection_leakage" in reasons

