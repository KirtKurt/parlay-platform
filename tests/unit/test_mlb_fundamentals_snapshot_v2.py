from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import inqsi_pull_history as history
import mlb_advanced_context as advanced_context
import mlb_fundamentals_snapshot_v2 as snapshot_v2
import mlb_ml_clean_cohort_v1 as clean_cohort


SOURCE_AT = "2026-07-21T20:00:00+00:00"
RETRIEVED_AT = "2026-07-21T20:00:10+00:00"
PERSISTED_AT = "2026-07-21T20:00:20+00:00"
LOCK_AT = "2026-07-21T21:00:00+00:00"


def _provenance(dataset: str = "test"):
    return {
        "provider": "fixture-provider",
        "endpoint": "https://example.invalid/data",
        "dataset": dataset,
        "retrievedAtUtc": RETRIEVED_AT,
        "sourceEffectiveAtUtc": SOURCE_AT,
        "payloadFingerprint": "fixture-payload-fingerprint",
    }


def _row():
    return {
        "gameId": "official:123",
        "officialGamePk": 123,
        "providerEventId": "provider-abc",
        "slateDateEt": "2026-07-21",
        "commenceTime": "2026-07-21T21:45:00+00:00",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictionSourcePullAt": SOURCE_AT,
        "predictionSourcePullId": "pull-1",
        "advanced_context": {
            "confirmed_probable_pitchers": {
                "source_status": "CONNECTED",
                "home_probable_pitcher": "Home Starter",
                "away_probable_pitcher": "Away Starter",
                "home_pitcher_id": 1,
                "away_pitcher_id": 2,
                "game_pk": 123,
                "sourceProvenance": _provenance("schedule probable pitchers"),
            },
            "fip_xfip": {
                "source_status": "CONNECTED",
                "home_starter_fip": 3.2,
                "away_starter_fip": 4.1,
                "home_starter_xfip": 3.4,
                "away_starter_xfip": 4.0,
                "home_starter_composite": 0.61,
                "away_starter_composite": 0.44,
                "sourceProvenance": _provenance("starter quality"),
            },
            "wrc_plus": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED", "note": "no licensed feed"},
            "starter_handedness_splits": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED", "note": "no licensed feed"},
            "bullpen_fatigue": {
                "source_status": "PARTIAL",
                "home_bullpen_composite": 0.72,
                "away_bullpen_composite": 0.52,
                "sourceProvenance": _provenance("bullpen usage"),
            },
            "confirmed_lineups": {
                "source_status": "CONNECTED",
                "home_lineup_confirmed": True,
                "away_lineup_confirmed": True,
                "home_lineup_wrc_plus": 112.0,
                "away_lineup_wrc_plus": 97.0,
                "sourceProvenance": _provenance("confirmed lineups"),
            },
            "weather_wind_roof": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED", "note": "no weather feed"},
            "ballpark_factors": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED", "note": "no park factors"},
            "injuries_late_scratches_news": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED", "note": "no injury feed"},
            "travel_rest": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED", "note": "no travel feed"},
            "closing_line_value": {
                "source_status": "CONNECTED",
                "clv_probability_delta": 0.99,
                "beats_close": True,
                "sourceProvenance": _provenance("forbidden postgame field"),
            },
        },
    }


def _complete_row():
    row = _row()
    for output_name, context_name, fields in snapshot_v2.GROUP_SPECS:
        source = {
            "source_status": "CONNECTED",
            "sourceProvenance": _provenance(context_name),
        }
        required = set(snapshot_v2.REQUIRED_VALUE_KEYS[output_name])
        for output_key, input_key in fields:
            if output_key in required:
                source[input_key] = 1
        row["advanced_context"][context_name] = source
    return row


def test_v2_keeps_missing_values_null_and_excludes_closing_line():
    snapshot = snapshot_v2.build(_row(), captured_at_utc="2026-07-21T20:00:15+00:00")

    assert snapshot["groups"]["offense_quality"]["values"]["homeWrcPlus"] is None
    assert snapshot["groups"]["starter_quality"]["values"]["homeComposite"] == 0.61
    assert snapshot["groups"]["bullpen_availability"]["status"] == "PARTIAL"
    assert "closing_line_value" not in snapshot["groups"]
    assert snapshot["missingValuesAreNull"] is True
    assert snapshot_v2.validate(snapshot) == []


def test_connected_group_without_retrieval_time_is_rejected_as_missing():
    row = _row()
    row["advanced_context"]["confirmed_lineups"].pop("sourceProvenance")

    snapshot = snapshot_v2.build(row)

    assert snapshot["groups"]["confirmed_lineups"]["status"] == "INVALID_MISSING_RETRIEVAL_TIME"
    assert "confirmed_lineups" in snapshot["missingGroups"]


def test_connected_group_without_source_payload_proof_is_rejected_as_missing():
    row = _complete_row()
    row["advanced_context"]["fip_xfip"]["sourceProvenance"].pop(
        "payloadFingerprint"
    )

    snapshot = snapshot_v2.build(row)

    assert snapshot["groups"]["starter_quality"]["status"] == (
        "INVALID_MISSING_PAYLOAD_FINGERPRINT"
    )
    assert "starter_quality" in snapshot["missingGroups"]
    assert snapshot["trainingEligibleAtCapture"] is False
    assert snapshot_v2.validate(snapshot) == []


def test_incomplete_snapshot_cannot_self_declare_training_eligible():
    snapshot = snapshot_v2.build(_row())
    tampered = deepcopy(snapshot)
    tampered["pregameComplete"] = True
    tampered["trainingEligibleAtCapture"] = True
    tampered["trainingExclusionReasons"] = []
    tampered["fingerprint"] = snapshot_v2.fingerprint_for_snapshot(tampered)

    errors = snapshot_v2.validate(tampered)

    assert "fundamentals_v2_pregame_complete_flag_mismatch" in errors
    assert "fundamentals_v2_training_eligibility_flag_mismatch" in errors
    assert "fundamentals_v2_training_exclusion_summary_mismatch" in errors


def test_exact_snapshot_tamper_fails_validation():
    snapshot = snapshot_v2.build(_row())
    tampered = deepcopy(snapshot)
    tampered["groups"]["starter_quality"]["values"]["homeComposite"] = 0.99

    assert "fundamentals_v2_fingerprint_mismatch" in snapshot_v2.validate(tampered)


def test_fingerprint_is_stable_after_dynamodb_float_to_decimal_round_trip():
    snapshot = snapshot_v2.build(_row())
    ddb_read = deepcopy(snapshot)
    ddb_read["groups"]["starter_quality"]["values"]["homeComposite"] = Decimal("0.61")
    ddb_read["groups"]["starter_quality"]["values"]["awayComposite"] = Decimal("0.44")
    ddb_read["groups"]["starter_quality"]["values"]["homeFip"] = Decimal("3.2")
    ddb_read["groups"]["starter_quality"]["values"]["awayFip"] = Decimal("4.1")
    ddb_read["groups"]["starter_quality"]["values"]["homeXfip"] = Decimal("3.4")
    ddb_read["groups"]["starter_quality"]["values"]["awayXfip"] = Decimal("4.0")
    ddb_read["groups"]["bullpen_availability"]["values"]["homeComposite"] = Decimal("0.72")
    ddb_read["groups"]["bullpen_availability"]["values"]["awayComposite"] = Decimal("0.52")
    ddb_read["groups"]["confirmed_lineups"]["values"]["homeWrcPlus"] = Decimal("112.0")
    ddb_read["groups"]["confirmed_lineups"]["values"]["awayWrcPlus"] = Decimal("97.0")
    ddb_read["completenessRatio"] = Decimal(str(snapshot["completenessRatio"]))

    assert snapshot_v2.fingerprint_for_snapshot(ddb_read) == snapshot["fingerprint"]


def test_snapshot_is_lock_safe_only_when_all_evidence_precedes_persistence_and_lock():
    snapshot = snapshot_v2.build(
        _complete_row(), captured_at_utc="2026-07-21T20:00:15+00:00"
    )

    ok, reasons = snapshot_v2.validate_snapshot(
        snapshot,
        prediction_time_utc=PERSISTED_AT,
        lock_time_utc=LOCK_AT,
    )
    assert ok is True, reasons
    assert reasons == []

    ok, reasons = snapshot_v2.validate_snapshot(
        snapshot,
        prediction_time_utc="2026-07-21T20:00:05+00:00",
        lock_time_utc=LOCK_AT,
    )
    assert ok is False
    assert reasons == ["fundamentals_v2_evidence_not_at_or_before_persisted_prediction_and_lock"]


def test_existing_tampered_snapshot_is_not_rebuilt_or_silently_accepted():
    row = _row()
    row["fundamentalsSnapshotV2"] = snapshot_v2.build(row)
    row["fundamentalsSnapshotV2"]["groups"]["starter_quality"]["values"]["homeComposite"] = 1.0

    snapshot_v2.enhance_row(row)

    assert row["trainingEligible"] is False
    assert "fundamentals_v2_fingerprint_mismatch" in row["trainingExclusionReasons"]


def test_nested_dynamodb_conversion_preserves_signed_null_missingness():
    snapshot = snapshot_v2.build(_row())

    stored = history.ddb_safe({"data": {"fundamentalsSnapshotV2": snapshot}})
    restored = stored["data"]["fundamentalsSnapshotV2"]

    assert restored["groups"]["offense_quality"]["values"]["homeWrcPlus"] is None
    assert restored["groups"]["offense_quality"]["provider"] is None
    assert snapshot_v2.validate(restored) == []
    assert snapshot_v2.fingerprint_for_snapshot(restored) == snapshot["fingerprint"]


def test_same_team_doubleheader_requires_exact_official_game_pk(monkeypatch):
    def game(game_pk):
        return {
            "gamePk": game_pk,
            "teams": {
                "away": {"team": {"name": "Away Club"}},
                "home": {"team": {"name": "Home Club"}},
            },
        }

    monkeypatch.setattr(
        advanced_context,
        "_statsapi_schedule",
        lambda slate: {
            "payload": {"dates": [{"games": [game(123), game(124)]}]}
        },
    )

    assert advanced_context._match_statsapi_game(
        "2026-07-21", "Home Club", "Away Club", 124
    )["gamePk"] == 124
    assert advanced_context._match_statsapi_game(
        "2026-07-21", "Home Club", "Away Club", 999
    ) is None
    assert advanced_context._match_statsapi_game(
        "2026-07-21", "Home Club", "Away Club"
    ) is None


def test_t15_context_change_cannot_rewrite_existing_t45_snapshot():
    row = _row()
    row["createdAt"] = PERSISTED_AT
    row["lockedAtUtc"] = LOCK_AT
    snapshot_v2.enhance_row(row)
    frozen_snapshot = deepcopy(row["fundamentalsSnapshotV2"])
    frozen_ref = deepcopy(row["fundamentalsSnapshotV2Ref"])

    row["advanced_context"]["confirmed_lineups"]["away_lineup_confirmed"] = False
    row["advanced_context"]["injuries_late_scratches_news"] = {
        "source_status": "CONNECTED",
        "late_scratch_flags": ["Away Starter scratched at T-15"],
        "sourceProvenance": {
            **_provenance("late playability only"),
            "retrievedAtUtc": "2026-07-21T20:45:00+00:00",
        },
    }
    snapshot_v2.enhance_row(row)

    assert row["fundamentalsSnapshotV2"] == frozen_snapshot
    assert row["fundamentalsSnapshotV2Ref"] == frozen_ref
    assert row["fundamentalsSnapshotV2"]["latePlayabilityMayRewriteSnapshotOrVector"] is False


def test_t45_vector_freeze_never_builds_a_missing_v2_snapshot(monkeypatch):
    row = _complete_row()
    row.update(
        {
            "createdAt": PERSISTED_AT,
            "lockedAtUtc": LOCK_AT,
            "predictedSide": "home",
            "predictedWinner": "Home Club",
            "teamWinProbabilityPct": 55.0,
            "signals": {
                "home": {"prob": 0.55, "americanOdds": -120},
                "away": {"prob": 0.45, "americanOdds": 110},
            },
        }
    )

    def forbidden_retrieval(_row):
        raise AssertionError("T-45 attempted to build newer fundamentals evidence")

    monkeypatch.setattr(snapshot_v2, "enhance_row", forbidden_retrieval)

    vector = clean_cohort.freeze_feature_snapshot(row)

    assert "fundamentalsSnapshotV2" not in row
    assert vector["fundamentalsSnapshotV2Version"] is None
    assert vector["fundamentalsSnapshotV2Fingerprint"] is None
    assert vector["fundamentalsSnapshotV2AtOrBeforeLock"] is False


def test_frozen_vector_cryptographically_binds_fundamentals_v2_snapshot(monkeypatch):
    monkeypatch.setattr(
        snapshot_v2,
        "_utc_now",
        lambda: "2026-07-21T20:00:15+00:00",
    )
    row = _complete_row()
    row.update(
        {
            "createdAt": PERSISTED_AT,
            "lockedAtUtc": LOCK_AT,
            "predictedSide": "home",
            "predictedWinner": "Home Club",
            "teamWinProbabilityPct": 55.0,
            "signals": {
                "home": {"prob": 0.55, "americanOdds": -120},
                "away": {"prob": 0.45, "americanOdds": 110},
            },
        }
    )
    snapshot_v2.enhance_row(row)

    vector = clean_cohort.freeze_feature_snapshot(row)

    assert vector["fundamentalsSnapshotV2Version"] == snapshot_v2.VERSION
    assert vector["fundamentalsSnapshotV2Fingerprint"] == row[
        "fundamentalsSnapshotV2"
    ]["fingerprint"]
    assert vector["fundamentalsSnapshotV2Ref"] == row["fundamentalsSnapshotV2Ref"]
    assert vector["fundamentalsSnapshotV2AtOrBeforeLock"] is True
    assert clean_cohort.fingerprint_for_vector(vector) == vector["fingerprint"]

    tampered = deepcopy(vector)
    tampered["fundamentalsSnapshotV2Fingerprint"] = "tampered"
    assert clean_cohort.fingerprint_for_vector(tampered) != vector["fingerprint"]
