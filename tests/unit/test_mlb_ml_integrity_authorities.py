from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_canonical_pull_patch as canonical_pull_patch
import mlb_ml_clean_cohort_hardening_v1 as hardening
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_current_lock_authority_v1 as current_authority
import pull_dedupe_guard


SLATE = "2026-07-21"


def _game():
    return {
        "game_id": "game-1",
        "game_key": "away|home",
        "home_team": "Home Club",
        "away_team": "Away Club",
        "commence_time": "2026-07-21T23:00:00+00:00",
        "books": {"fanduel": {"ml": {"home": -110, "away": 100}}},
    }


def test_pull_slot_integrity_is_bound_into_vector_and_contamination_quarantines():
    row = {
        "gameId": "game-1",
        "slateDateEt": SLATE,
        "commenceTime": "2026-07-21T23:00:00+00:00",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "americanOdds": -110,
        "priceBook": "fanduel",
        "priceSource": "real_book",
        "score": 55.0,
        "teamWinProbabilityPct": 58.0,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v2",
        "lockedPrediction": True,
        "status": "GRADED",
        "winner": "Home Club",
        "correct": True,
        "slateCoverage": {"coverageComplete": True},
        "lockedCardAudit": {
            "lockedFlag": True,
            "lockAtUtc": "2026-07-21T22:15:00+00:00",
            "explicitSourceAtUtc": "2026-07-21T22:14:00+00:00",
            "preventsLateRows": True,
        },
        "pullHistoryIntegrity": {
            "version": "INQSI-PULL-HISTORY-INTEGRITY-v1-canonical-quarter-hour",
            "canonicalizationVersion": "INQSI-CANONICAL-PULL-SLOT-v1-earliest-integrity-valid",
            "slotMinutes": 15,
            "uniqueSlotCount": 24,
            "canonicalSlotFingerprint": "canonical-slot-fingerprint",
            "rawPullCount": 24,
            "duplicatePullCount": 0,
            "invalidPullCount": 0,
            "contaminatedSlotCount": 0,
            "duplicateContaminated": False,
            "slotStartsUtc": ["2026-07-21T22:00:00+00:00"],
        },
        "predictionSourceCanonicalSlot": {
            "version": "INQSI-CANONICAL-PULL-SLOT-v1-earliest-integrity-valid",
            "slotMinutes": 15,
            "slotStartUtc": "2026-07-21T22:00:00+00:00",
            "canonical": True,
            "canonicalPullId": "pull-24",
            "canonicalPulledAtUtc": "2026-07-21T22:14:00+00:00",
            "canonicalPullFingerprint": "pull-24-fingerprint",
            "rawPullCount": 1,
            "validPullCount": 1,
            "invalidPullCount": 0,
            "duplicatePullCount": 0,
            "contaminated": False,
        },
        "homeSignal": {"probLatest": 0.58, "americanOdds": -110, "temporalFeatures": {}},
        "awaySignal": {"probLatest": 0.42, "americanOdds": 100, "temporalFeatures": {}},
        "fundamentalsSnapshot": {},
    }
    vector = cohort.freeze_feature_snapshot(row)

    assert vector["pullHistoryIntegrity"]["uniqueSlotCount"] == 24
    assert vector["pullHistoryIntegrity"]["canonicalSlotFingerprint"] == "canonical-slot-fingerprint"
    assert vector["pullHistoryIntegrity"]["duplicatePullCount"] == 0
    assert vector["predictionSourceCanonicalSlot"]["canonicalPullFingerprint"] == "pull-24-fingerprint"
    assert vector["fingerprint"] == cohort.fingerprint_for_vector(vector)
    tampered = copy.deepcopy(vector)
    tampered["pullHistoryIntegrity"]["canonicalSlotFingerprint"] = "tampered"
    assert cohort.fingerprint_for_vector(tampered) != vector["fingerprint"]

    contaminated = copy.deepcopy(row)
    contaminated["pullHistoryIntegrity"].update({
        "rawPullCount": 25,
        "duplicatePullCount": 1,
        "contaminatedSlotCount": 1,
        "duplicateContaminated": True,
    })
    contaminated["predictionSourceCanonicalSlot"].update({
        "rawPullCount": 2,
        "duplicatePullCount": 1,
        "contaminated": True,
    })
    contaminated["frozenFeatureVector"] = cohort.freeze_feature_snapshot(contaminated)
    contaminated["mlFeatureFreeze"] = {
        "trainingEligible": True,
        "completeSlateCoverage": True,
    }
    hardening.apply(cohort)
    ok, reasons = cohort.eligibility(contaminated)

    assert ok is False
    assert "frozen_vector_duplicate_pull_slot_contamination" in reasons
    assert "frozen_vector_duplicate_pull_observation_count_nonzero" in reasons
    assert "frozen_vector_prediction_source_slot_contaminated" in reasons


def _canonical_authority(source_sk: str):
    return {
        "verified": True,
        "consistentRead": True,
        "immutableLocked": True,
        "stageAuthorityVerified": True,
        "persistedStageAuthorityValidated": True,
        "officialAuditEligible": True,
        "exactLockVectorValidated": True,
        "learningEligible": True,
        "recordType": current_authority.CANONICAL_RECORD_TYPE,
        "sourcePk": f"GAME_WINNERS#mlb#{SLATE}",
        "sourceSk": source_sk,
    }


def test_current_lock_revalidation_overrides_stale_embedded_approval():
    source_sk = "LOCKED#GAME#2026-07-21T23:00:00+00:00#game-1"
    historical = {
        "status": "GRADED",
        "slateDateEt": SLATE,
        "id": "game-1",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "winner": "Away Club",
        "correct": False,
        "predictedWinner": "Stale Winner",
        "canonicalLockAuthority": _canonical_authority(source_sk),
    }
    current = {
        "slateDateEt": SLATE,
        "gameId": "game-1",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "frozenFeatureVector": {
            "lockAtUtc": "2026-07-21T22:15:00+00:00",
            "sourcePullAtUtc": "2026-07-21T22:14:00+00:00",
        },
        "canonicalLockAuthority": _canonical_authority(source_sk),
    }
    passed_module = SimpleNamespace(_query_predictions_for_slate=lambda _slate: [current])
    passed = current_authority.revalidate([historical], passed_module)

    assert passed["revalidatedRowCount"] == 1
    assert passed["rows"][0]["predictedWinner"] == "Home Club"
    assert passed["rows"][0]["winner"] == "Away Club"
    assert passed["rows"][0]["currentCanonicalLockRevalidation"]["verified"] is True

    rejected_module = SimpleNamespace(_query_predictions_for_slate=lambda _slate: [])
    rejected = current_authority.revalidate([historical], rejected_module)
    row = rejected["rows"][0]

    assert rejected["rejectedRowCount"] == 1
    assert row["status"] == "INVALID_CANONICAL_LOCK"
    assert row["trainingEligible"] is False
    assert row["canonicalLockAuthority"]["verified"] is False
    assert "current_canonical_lock_not_found_or_rejected" in row["currentCanonicalLockRevalidation"]["rejectionReasons"]


def test_current_lock_revalidation_rejects_same_team_doubleheader_cross_label():
    game_one_sk = "LOCKED#GAME#2026-07-21T23:00:00+00:00#game-1"
    historical_game_two = {
        "status": "GRADED",
        "slateDateEt": SLATE,
        "id": "game-2",
        "gameId": "game-2",
        "officialGamePk": 2002,
        "commenceTime": "2026-07-22T02:00:00+00:00",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "winner": "Away Club",
        "correct": False,
        # Simulate stale/tampered embedded approval pointing at Game 1.
        "canonicalLockAuthority": _canonical_authority(game_one_sk),
    }
    current_game_one = {
        "slateDateEt": SLATE,
        "gameId": "game-1",
        "officialGamePk": 2001,
        "commenceTime": "2026-07-21T23:00:00+00:00",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "frozenFeatureVector": {
            "lockAtUtc": "2026-07-21T22:15:00+00:00",
            "sourcePullAtUtc": "2026-07-21T22:14:00+00:00",
        },
        "canonicalLockAuthority": _canonical_authority(game_one_sk),
    }
    module = SimpleNamespace(
        _query_predictions_for_slate=lambda _slate: [current_game_one]
    )

    result = current_authority.revalidate([historical_game_two], module)
    row = result["rows"][0]

    assert result["revalidatedRowCount"] == 0
    assert result["rejectedRowCount"] == 1
    assert row["status"] == "INVALID_CANONICAL_LOCK"
    assert "current_official_game_pk_mismatch" in row[
        "currentCanonicalLockRevalidation"
    ]["rejectionReasons"]


def test_native_scheduler_writer_disables_legacy_second_bridge_write():
    calls = []
    module = SimpleNamespace(
        _store_canonical_pull_history=lambda **_kwargs: calls.append("native"),
        _store_snapshot_item=lambda **_kwargs: {"ok": True},
    )

    canonical_pull_patch.apply(module)
    result = module._store_snapshot_item(
        t="HOT",
        slate_date=SLATE,
        game_date=SLATE,
        asof="2026-07-21T16:15:00+00:00",
        run="scheduled",
        compact={"games": [_game()]},
        date_isolated=False,
        pk="SPORT#mlb",
    )

    assert result == {"ok": True}
    assert calls == []
    assert module._inqsi_canonical_pull_native_writer is True


def test_native_atomic_slot_history_disables_legacy_marker_wrapper():
    calls = []

    def store_pull(body):
        calls.append(body)
        return {"ok": True}

    module = SimpleNamespace(
        PULL_SLOT_VERSION="native-slot-v1",
        PULL_HISTORY_INTEGRITY_VERSION="native-history-v1",
        canonicalize_pull_slots=lambda pulls: pulls,
        store_pull=store_pull,
    )

    pull_dedupe_guard.apply(module)
    result = module.store_pull({"sport": "mlb"})

    assert result == {"ok": True}
    assert calls == [{"sport": "mlb"}]
    assert module._inqsi_native_atomic_pull_slot_writer is True
    assert module.PULL_DEDUPE_VERSION == "native-slot-v1"
