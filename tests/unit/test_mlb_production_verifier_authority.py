from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

import mlb_ml_clean_cohort_v1 as cohort  # noqa: E402
import mlb_production_verifier as verifier  # noqa: E402


NOW = datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)
SLATE = "2026-07-22"
GAME_IDS = ["mlb_statsapi:880001", "mlb_statsapi:880002"]
AUTHORITY_FINGERPRINT = "official-schedule-authority-fixture"


def _game_status(
    game_id: str,
    lock_at: datetime,
    *,
    outcome_recorded: bool = False,
) -> dict:
    return {
        "gameIdentity": f"provider:{game_id}",
        "gameId": game_id,
        "scheduledLockAtUtc": lock_at.isoformat(),
        "lockStatus": "LOCKED_CANONICAL" if outcome_recorded else "PENDING",
        "lockOutcomeRecorded": outcome_recorded,
        "lockedPrediction": outcome_recorded,
    }


def _locked_row(game_id: str, lock_at: datetime) -> dict:
    source_at = lock_at - timedelta(minutes=4)
    vector = {
        "version": cohort.FEATURE_SNAPSHOT_VERSION,
        "fingerprintVersion": cohort.FINGERPRINT_VERSION,
        "gameId": game_id,
        "slateDateEt": SLATE,
        "commenceTime": (lock_at + timedelta(minutes=45)).isoformat(),
        "homeTeam": f"Home {game_id}",
        "awayTeam": f"Away {game_id}",
        "predictedWinner": f"Home {game_id}",
        "predictedSide": "home",
        "sourcePullAtUtc": source_at.isoformat(),
        "lockAtUtc": lock_at.isoformat(),
        "selectedAmericanOdds": -115,
        "selectedPriceBook": "Fixture Book",
        "selectedPriceSource": "real_book",
        "immutableSource": "locked_prediction_row_pre_game_features",
        "derivedOnceFromImmutableLockedRow": True,
        "temporalFeatureVersion": "fixture-temporal-v1",
        "temporalSourcePullAtUtc": source_at.isoformat(),
        "temporalFeaturesAtOrBeforeLock": True,
        "missingnessFeatureVersion": "fixture-missingness-v1",
        "fundamentalsSnapshotVersion": "fixture-fundamentals-v1",
        "fundamentalsSnapshotAsOfUtc": source_at.isoformat(),
        "fundamentalMasksAtOrBeforeLock": True,
        "features": {"homeMarketProb": 0.55, "awayMarketProb": 0.45},
        "labels": {"homeWon": None, "pickCorrect": None},
    }
    vector["fingerprint"] = cohort.fingerprint_for_vector(vector)
    return {
        "gameId": game_id,
        "lockedPrediction": True,
        "lockedCardAudit": {
            "lockedFlag": True,
            "lockAtUtc": lock_at.isoformat(),
        },
        "frozenFeatureVector": vector,
        "createdAt": lock_at.isoformat(),
    }


def _install_healthy_sources(monkeypatch):
    pulls = [{"pulled_at": (NOW - timedelta(minutes=5)).isoformat()}]
    roster = {
        "games": [{"game_id": game_id} for game_id in GAME_IDS],
        "fullSlateGameCount": len(GAME_IDS),
        "officialScheduleGameCount": len(GAME_IDS),
        "officialScheduleBacked": True,
        "immutableReadbackVerified": True,
        "fullAuthorityFingerprint": "full-roster-authority-fixture",
        "officialScheduleAuthorityFingerprint": AUTHORITY_FINGERPRINT,
    }
    predictions = {
        "ok": True,
        "gameCount": len(GAME_IDS),
        "count": len(GAME_IDS),
        "allGamesPredicted": True,
        "predictions": [{"gameId": game_id} for game_id in GAME_IDS],
    }
    lock_status = {
        "ok": True,
        "gameCount": len(GAME_IDS),
        "manifestGameCount": len(GAME_IDS),
        "verifiedFullSlateGameCount": len(GAME_IDS),
        "officialScheduleGameCount": len(GAME_IDS),
        "officialScheduleBacked": True,
        "officialScheduleAuthorityFingerprint": AUTHORITY_FINGERPRINT,
        "locked": False,
        "lockStatusComplete": False,
        "dailyCardComplete": False,
        "perGameStatus": [
            _game_status(GAME_IDS[0], NOW + timedelta(hours=1)),
            _game_status(GAME_IDS[1], NOW + timedelta(hours=2)),
        ],
    }
    stored_rows: list[dict] = []

    monkeypatch.setattr(verifier, "_now_utc", lambda: NOW)
    monkeypatch.setattr(verifier.history, "query_pulls", lambda *args: pulls)
    monkeypatch.setattr(
        verifier.history,
        "verified_full_slate_manifest",
        lambda *args: roster,
    )
    monkeypatch.setattr(
        verifier.mlb_game_winner_engine,
        "predict_all",
        lambda *args, **kwargs: predictions,
    )
    monkeypatch.setattr(
        verifier.mlb_daily_pick_lock,
        "_status_payload",
        lambda *args, **kwargs: lock_status,
    )
    monkeypatch.setattr(
        verifier,
        "_query_stored_predictions",
        lambda *args, **kwargs: stored_rows,
    )
    return roster, predictions, lock_status, stored_rows


def test_current_r3_vector_uses_the_canonical_v3_fingerprint_and_tamper_fails():
    row = _locked_row(GAME_IDS[0], NOW - timedelta(minutes=1))
    vector = row["frozenFeatureVector"]

    canonical_matches = (
        vector["fingerprint"] == cohort.fingerprint_for_vector(vector)
    )
    validation = verifier._vector_validation(row)
    verifier_matches = validation["ok"] is True

    assert canonical_matches is True
    assert verifier_matches is True
    assert validation["canonicalFingerprintMatches"] is True
    assert validation["fingerprintVersion"] == cohort.FINGERPRINT_VERSION

    tampered = copy.deepcopy(row)
    tampered["frozenFeatureVector"]["predictedWinner"] = "Away tampered"
    invalid = verifier._vector_validation(tampered)
    assert invalid["ok"] is False
    assert invalid["canonicalFingerprintMatches"] is False
    assert "fingerprint_mismatch" in invalid["reasons"]


def test_current_vector_cannot_downgrade_to_the_legacy_unversioned_hash():
    row = _locked_row(GAME_IDS[0], NOW - timedelta(minutes=1))
    vector = row["frozenFeatureVector"]
    vector.pop("fingerprintVersion")
    vector["fingerprint"] = cohort.fingerprint_for_vector(vector)

    invalid = verifier._vector_validation(row)

    assert invalid["canonicalFingerprintMatches"] is True
    assert invalid["ok"] is False
    assert "missing_fingerprint_version" in invalid["reasons"]


def test_healthy_prelock_sources_have_exact_authoritative_identity_sets(monkeypatch):
    _install_healthy_sources(monkeypatch)

    result = verifier._verification_payload(SLATE, "lock", "unit")

    assert result["ok"] is True
    assert result["blockers"] == []
    assert result["sourceAuthority"]["identitySetsEqual"] is True
    assert result["lockedRowIntegrity"]["storedGameIdentities"] == []
    assert result["lockedRowIntegrity"]["authoritySafe"] is True


def test_prediction_and_lock_error_payloads_fail_even_when_counts_look_complete(
    monkeypatch,
):
    _, predictions, lock_status, _ = _install_healthy_sources(monkeypatch)
    predictions["ok"] = False
    lock_status["ok"] = False

    result = verifier._verification_payload(SLATE, "lock", "unit")

    assert result["ok"] is False
    assert "PREDICTION_ENGINE_FAILED" in result["blockers"]
    assert "LOCK_STATUS_FAILED" in result["blockers"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda roster: roster.update(officialScheduleBacked=False),
        lambda roster: roster.update(officialScheduleGameCount=1),
        lambda roster: roster.update(fullSlateGameCount=1),
        lambda roster: roster.update(immutableReadbackVerified=False),
        lambda roster: roster.update(fullAuthorityFingerprint=""),
        lambda roster: roster.update(
            games=[{"game_id": GAME_IDS[0]}, {"game_id": GAME_IDS[0]}]
        ),
    ],
)
def test_unbacked_or_count_invalid_official_schedule_fails_closed(
    monkeypatch, mutate
):
    roster, _, _, _ = _install_healthy_sources(monkeypatch)
    mutate(roster)

    result = verifier._verification_payload(SLATE, "lock", "unit")

    assert result["ok"] is False
    assert "OFFICIAL_SCHEDULE_AUTHORITY_INVALID" in result["blockers"]
    assert result["sourceAuthority"]["identitySetsEqual"] is False


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"gameId": GAME_IDS[0]}, {"gameId": GAME_IDS[0]}],
        [{"gameId": GAME_IDS[0]}, {"gameId": "mlb_statsapi:wrong"}],
        [{"gameId": GAME_IDS[0]}, {}],
    ],
)
def test_prediction_rows_require_exact_unique_official_membership(
    monkeypatch, rows
):
    _, predictions, _, _ = _install_healthy_sources(monkeypatch)
    predictions["predictions"] = rows

    result = verifier._verification_payload(SLATE, "lock", "unit")

    assert result["ok"] is False
    assert "PREDICTION_ROSTER_MEMBERSHIP_MISMATCH" in result["blockers"]
    assert result["sourceAuthority"]["identitySetsEqual"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        {"officialScheduleBacked": False},
        {"gameCount": 1},
        {"manifestGameCount": 1},
        {"verifiedFullSlateGameCount": 1},
        {"officialScheduleGameCount": 1},
        {"officialScheduleAuthorityFingerprint": "wrong"},
    ],
)
def test_lock_status_must_bind_the_same_official_authority(
    monkeypatch, mutation
):
    _, _, lock_status, _ = _install_healthy_sources(monkeypatch)
    lock_status.update(mutation)

    result = verifier._verification_payload(SLATE, "lock", "unit")

    assert result["ok"] is False
    assert "LOCK_STATUS_ROSTER_AUTHORITY_MISMATCH" in result["blockers"]
    assert result["sourceAuthority"]["identitySetsEqual"] is False


@pytest.mark.parametrize(
    "statuses",
    [
        [],
        [
            _game_status(GAME_IDS[0], NOW + timedelta(hours=1)),
            _game_status(GAME_IDS[0], NOW + timedelta(hours=2)),
        ],
        [
            _game_status(GAME_IDS[0], NOW + timedelta(hours=1)),
            _game_status("mlb_statsapi:wrong", NOW + timedelta(hours=2)),
        ],
    ],
)
def test_per_game_status_requires_exact_unique_official_membership(
    monkeypatch, statuses
):
    _, _, lock_status, _ = _install_healthy_sources(monkeypatch)
    lock_status["perGameStatus"] = statuses

    result = verifier._verification_payload(SLATE, "lock", "unit")

    assert result["ok"] is False
    assert "PER_GAME_LOCK_STATUS_MISSING_OR_INVALID" in result["blockers"]
    assert "PER_GAME_LOCK_ROSTER_MEMBERSHIP_MISMATCH" in result["blockers"]
    assert result["sourceAuthority"]["identitySetsEqual"] is False


def test_each_due_game_requires_its_own_canonical_vector_not_a_count_substitute(
    monkeypatch,
):
    _, _, lock_status, stored = _install_healthy_sources(monkeypatch)
    due_at = NOW - timedelta(minutes=1)
    lock_status["perGameStatus"] = [
        _game_status(GAME_IDS[0], due_at, outcome_recorded=True),
        _game_status(GAME_IDS[1], NOW + timedelta(hours=1)),
    ]
    stored.append(_locked_row(GAME_IDS[1], NOW + timedelta(hours=1)))

    wrong_identity = verifier._verification_payload(SLATE, "lock", "unit")

    assert wrong_identity["ok"] is False
    assert wrong_identity["lockedRowIntegrity"]["rawStoredRowCount"] == 1
    assert wrong_identity["lockedRowIntegrity"]["missingDueGameIdentities"] == [
        GAME_IDS[0]
    ]
    assert "LOCKED_ROWS_MISSING_VALID_FROZEN_FINGERPRINTS" in wrong_identity[
        "blockers"
    ]

    stored[:] = [_locked_row(GAME_IDS[0], due_at)]
    exact_due_identity = verifier._verification_payload(SLATE, "lock", "unit")
    assert exact_due_identity["ok"] is True
    assert exact_due_identity["lockedRowIntegrity"]["storedGameIdentities"] == [
        GAME_IDS[0]
    ]


def test_duplicate_or_unexpected_canonical_lock_identity_fails_before_final_cutoff(
    monkeypatch,
):
    _, _, _, stored = _install_healthy_sources(monkeypatch)
    lock_at = NOW + timedelta(hours=1)
    stored.extend(
        [
            _locked_row(GAME_IDS[0], lock_at),
            _locked_row(GAME_IDS[0], lock_at),
        ]
    )

    duplicate = verifier._verification_payload(SLATE, "lock", "unit")
    assert duplicate["ok"] is False
    assert duplicate["lockedRowIntegrity"]["duplicateGameIdentities"] == [
        GAME_IDS[0]
    ]
    assert "CANONICAL_LOCK_ROSTER_MEMBERSHIP_MISMATCH" in duplicate["blockers"]

    stored[:] = [_locked_row("mlb_statsapi:unexpected", lock_at)]
    unexpected = verifier._verification_payload(SLATE, "lock", "unit")
    assert unexpected["ok"] is False
    assert unexpected["lockedRowIntegrity"]["unexpectedGameIdentities"] == [
        "mlb_statsapi:unexpected"
    ]
    assert "CANONICAL_LOCK_ROSTER_MEMBERSHIP_MISMATCH" in unexpected[
        "blockers"
    ]

    malformed = _locked_row(GAME_IDS[0], lock_at)
    malformed.pop("gameId")
    malformed["frozenFeatureVector"].pop("gameId")
    stored[:] = [malformed]
    missing_identity = verifier._verification_payload(SLATE, "lock", "unit")
    assert missing_identity["ok"] is False
    assert missing_identity["lockedRowIntegrity"]["missingIdentityRowCount"] == 1
    assert "CANONICAL_LOCK_ROSTER_MEMBERSHIP_MISMATCH" in missing_identity[
        "blockers"
    ]


def test_final_cutoff_requires_exact_full_official_canonical_set(monkeypatch):
    _, _, lock_status, stored = _install_healthy_sources(monkeypatch)
    first_lock = NOW - timedelta(hours=2)
    second_lock = NOW - timedelta(minutes=1)
    lock_status["perGameStatus"] = [
        _game_status(GAME_IDS[0], first_lock, outcome_recorded=True),
        _game_status(GAME_IDS[1], second_lock, outcome_recorded=True),
    ]
    stored.append(_locked_row(GAME_IDS[0], first_lock))

    incomplete = verifier._verification_payload(SLATE, "lock", "unit")
    assert incomplete["ok"] is False
    assert incomplete["lockedRowIntegrity"]["coverageComplete"] is False

    stored.append(_locked_row(GAME_IDS[1], second_lock))
    complete = verifier._verification_payload(SLATE, "lock", "unit")
    assert complete["ok"] is True
    assert complete["lockedRowIntegrity"]["coverageComplete"] is True
