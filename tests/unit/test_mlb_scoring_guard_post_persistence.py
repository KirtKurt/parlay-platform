from __future__ import annotations

import copy
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

from hello_world import inqsi_pull_history as history
from hello_world import mlb_fundamentals_scoring_bridge_v1 as bridge
from hello_world import mlb_fundamentals_snapshot_v2 as snapshot_v2


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "mlb_scoring_guard_post_persistence.py"
SPEC = importlib.util.spec_from_file_location(
    "mlb_scoring_guard_post_persistence", MODULE_PATH
)
assert SPEC and SPEC.loader
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)


PERSISTED_AT = "2026-07-22T12:01:00+00:00"
LOCK_AT = "2026-07-22T16:20:00+00:00"
COMMENCE = "2026-07-22T17:05:00+00:00"


def _provenance(dataset: str) -> dict:
    return {
        "provider": "fixture-provider",
        "endpoint": "https://example.invalid/pregame",
        "dataset": dataset,
        "retrievedAtUtc": "2026-07-22T12:00:30+00:00",
        "sourceEffectiveAtUtc": "2026-07-22T11:59:00+00:00",
        "payloadFingerprint": f"fixture-{dataset}",
    }


def _source_context() -> dict:
    context = {
        context_name: {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "reason": "fixture source unavailable before lock",
        }
        for _output, context_name, _fields in snapshot_v2.GROUP_SPECS
    }
    context.update(
        {
            "confirmed_probable_pitchers": {
                "source_status": "CONNECTED",
                "home_probable_pitcher": "Home Starter",
                "away_probable_pitcher": "Away Starter",
                "sourceProvenance": _provenance("probable"),
            },
            "fip_xfip": {
                "source_status": "CONNECTED",
                "home_starter_fip": 3.1,
                "away_starter_fip": 4.2,
                "home_starter_xfip": 3.3,
                "away_starter_xfip": 4.0,
                "home_starter_k_minus_bb_pct": 0.19,
                "away_starter_k_minus_bb_pct": 0.12,
                "sourceProvenance": _provenance("starter"),
            },
            "wrc_plus": {
                "source_status": "CONNECTED",
                "home_team_wrc_plus": 112.0,
                "away_team_wrc_plus": 98.0,
                "sourceProvenance": _provenance("offense"),
            },
            "bullpen_fatigue": {
                "source_status": "CONNECTED",
                "home_reliever_usage_1d_3d_5d": {"oneDay": 18},
                "away_reliever_usage_1d_3d_5d": {"oneDay": 37},
                "home_available_relievers": ["H1", "H2"],
                "away_available_relievers": ["A1"],
                "home_bullpen_fatigue_score": 0.2,
                "away_bullpen_fatigue_score": 0.8,
                "sourceProvenance": _provenance("bullpen"),
            },
            "confirmed_lineups": {
                "source_status": "CONNECTED",
                "home_lineup_confirmed": True,
                "away_lineup_confirmed": True,
                "home_batting_order": ["H1", "H2"],
                "away_batting_order": ["A1", "A2"],
                "home_lineup_strength_delta": 0.2,
                "away_lineup_strength_delta": -0.1,
                "sourceProvenance": _provenance("lineups"),
            },
            "injuries_late_scratches_news": {
                "source_status": "CONNECTED",
                "home_key_injuries": [],
                "away_key_injuries": ["Away regular"],
                "late_scratch_flags": [],
                "pitcher_change_flag": False,
                "sourceProvenance": _provenance("injuries"),
            },
            "travel_rest": {
                "source_status": "CONNECTED",
                "home_rest_days": 2,
                "away_rest_days": 0,
                "sourceProvenance": _provenance("rest"),
            },
        }
    )
    return context


def _live_prediction() -> dict:
    row = {
        "officialGamePk": "1001",
        "gameId": "provider-a",
        "gameIdentity": "provider-a",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "commenceTime": COMMENCE,
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "score": 61.2,
        "confidenceTier": "Solid",
        "predictionSourcePullAt": "2026-07-22T12:00:00+00:00",
        "predictionSourcePullId": "pull-fixture",
        "lockedAtUtc": LOCK_AT,
        "winnerOptimizer": {
            "fundamentalsApplied": False,
            "fundamentalsMode": "FUNDAMENTALS_V2_NOT_ACTIVE_IN_LIVE_SCORING",
        },
        "advanced_context": _source_context(),
    }
    row["fundamentalsSnapshotV2"] = snapshot_v2.build(
        row,
        captured_at_utc="2026-07-22T12:00:00+00:00",
    )
    snapshot_v2.enhance_row(row)
    passive = bridge.evaluate_shadow(row)
    assert passive["evaluated"] is False
    assert passive["validationErrors"] == [bridge.EXPECTED_PASSIVE_PROVENANCE_ERROR]
    row[bridge.SHADOW_FIELD] = passive
    persisted_row = history.ddb_safe(row)
    return {
        "PK": "GAME_WINNERS#mlb#2026-07-22",
        "SK": "GAME#2026-07-22T17:05:00+00:00#provider-a",
        "record_type": SUBJECT.base.PREDICTION_RECORD_TYPE,
        "data": persisted_row,
    }


def _proof(live: dict, *, persisted_at: str = PERSISTED_AT) -> dict:
    data = copy.deepcopy(live["data"])
    return {
        "PK": live["PK"],
        "SK": (
            "PREGAME#GAME#provider-a#PERSISTED#"
            f"{persisted_at}#CREATED#2026-07-22T12:00:45+00:00#fixture"
        ),
        "record_type": SUBJECT.PREGAME_RECORD_TYPE,
        "snapshot_version": SUBJECT.PREGAME_SNAPSHOT_VERSION,
        "prediction_persistence_proof_type": SUBJECT.PERSISTENCE_PROOF_TYPE,
        "prediction_persistence_write_pk": live["PK"],
        "prediction_persistence_write_sk": live["SK"],
        "prediction_payload_fingerprint_version": SUBJECT.PAYLOAD_FINGERPRINT_VERSION,
        "prediction_payload_fingerprint": history.canonical_payload_fingerprint(data),
        "prediction_created_at_utc": "2026-07-22T12:00:45+00:00",
        "prediction_persisted_at_utc": persisted_at,
        "immutable_pregame": True,
        "write_once": True,
        "data": data,
    }


def _report() -> dict:
    return {
        "ok": True,
        "guardPassed": True,
        "summary": {
            "officialGameCount": 1,
            "fundamentalsAppliedCount": 0,
            "fundamentalsNotAppliedOrMissingCount": 1,
            "fundamentalsNeutralOrSourceMissingCount": 0,
            "fundamentalsNotActiveCount": 1,
            "fundamentalsShadowOnlyCount": 0,
            "fundamentalsShadowOnlyNotActiveCount": 1,
            "fundamentalsShadowEvaluatedCount": 0,
            "fundamentalsShadowWouldApplyCount": 0,
            "fundamentalsSourceIncompleteCount": 0,
            "fundamentalsShadowInvalidCount": 0,
        },
        "games": [
            {
                "gameIdentity": "official:1001",
                "officialGamePk": "1001",
                "awayTeam": "Away Club",
                "homeTeam": "Home Club",
                "commenceTime": COMMENCE,
                "fundamentalsState": "NOT_ACTIVE",
                "fundamentalsShadowEvaluated": False,
                "fundamentalsShadowWouldApply": False,
                "fundamentalsShadowAttestationErrors": [],
            }
        ],
        "blockers": [],
        "readOnly": True,
    }


def test_valid_write_once_proof_enables_read_only_post_persistence_shadow() -> None:
    live = _live_prediction()
    proof = _proof(live)
    frozen_live = copy.deepcopy(live)
    frozen_proof = copy.deepcopy(proof)

    result = SUBJECT.enhance_report(
        _report(),
        prediction_items=[live],
        pregame_snapshot_items=[proof],
        observed_at=datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc),
    )

    assert live == frozen_live
    assert proof == frozen_proof
    assert result["guardPassed"] is True
    assert result["summary"]["fundamentalsPostPersistenceProofCount"] == 1
    assert result["summary"]["fundamentalsPostPersistenceProofInvalidCount"] == 0
    assert result["summary"]["fundamentalsPostPersistenceShadowEvaluatedCount"] == 1
    assert result["summary"]["fundamentalsShadowEvaluatedCount"] == 1
    assert result["summary"]["fundamentalsNotActiveCount"] == 0
    assert result["summary"]["fundamentalsShadowOnlyCount"] == 1
    game = result["games"][0]
    assert game["fundamentalsState"] == "SHADOW_ONLY"
    assert game["fundamentalsShadowEvaluated"] is True
    assert game["fundamentalsPostPersistenceShadowOnly"] is True
    assert game["fundamentalsPostPersistenceLiveScoringAuthority"] is False
    assert game["fundamentalsPostPersistenceCanInfluenceLivePick"] is False
    diagnostic = result["postPersistenceShadowDiagnostic"]
    assert diagnostic["readOnly"] is True
    assert diagnostic["mutatedPersistence"] is False
    assert diagnostic["productionAuthorityChanged"] is False


def test_tampered_snapshot_fingerprint_cannot_create_evaluated_shadow() -> None:
    live = _live_prediction()
    proof = _proof(live)
    proof["data"]["predictedWinner"] = "Away Club"

    result = SUBJECT.enhance_report(
        _report(),
        prediction_items=[live],
        pregame_snapshot_items=[proof],
        observed_at=datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc),
    )

    assert result["guardPassed"] is True
    assert result["summary"]["fundamentalsPostPersistenceProofInvalidCount"] == 1
    assert result["summary"]["fundamentalsPostPersistenceShadowEvaluatedCount"] == 0
    assert result["summary"]["fundamentalsShadowEvaluatedCount"] == 0
    assert result["games"][0]["fundamentalsState"] == "NOT_ACTIVE"
    errors = result["games"][0]["fundamentalsPostPersistenceErrors"]
    assert "post_persistence_snapshot_payload_fingerprint_mismatch" in errors
    assert "post_persistence_mutable_row_changed_after_snapshot" in errors


def test_post_cutoff_persistence_proof_cannot_create_evaluated_shadow() -> None:
    live = _live_prediction()
    proof = _proof(live, persisted_at="2026-07-22T16:21:00+00:00")

    result = SUBJECT.enhance_report(
        _report(),
        prediction_items=[live],
        pregame_snapshot_items=[proof],
        observed_at=datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc),
    )

    assert result["summary"]["fundamentalsPostPersistenceProofInvalidCount"] == 1
    assert result["summary"]["fundamentalsPostPersistenceShadowEvaluatedCount"] == 0
    assert (
        "post_persistence_snapshot_persisted_after_cutoff"
        in result["games"][0]["fundamentalsPostPersistenceErrors"]
    )


def test_missing_proof_preserves_existing_passive_guard_state() -> None:
    live = _live_prediction()

    result = SUBJECT.enhance_report(
        _report(),
        prediction_items=[live],
        pregame_snapshot_items=[],
        observed_at=datetime(2026, 7, 22, 18, 0, tzinfo=timezone.utc),
    )

    assert result["guardPassed"] is True
    assert result["summary"]["fundamentalsNotActiveCount"] == 1
    assert result["summary"]["fundamentalsShadowEvaluatedCount"] == 0
    assert result["summary"]["fundamentalsPostPersistenceProofCount"] == 0
    assert result["games"][0]["fundamentalsPostPersistenceProofPresent"] is False
