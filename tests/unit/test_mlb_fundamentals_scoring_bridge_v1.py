from __future__ import annotations

import copy
import importlib
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import inqsi_pull_history as pull_history
import mlb_fundamentals_scoring_bridge_v1 as bridge
import mlb_fundamentals_snapshot_v1 as snapshot_v1
import mlb_fundamentals_snapshot_v2 as snapshot_v2
import mlb_winner_stack_v2 as winner_stack


def _group(values=None, complete=True):
    return {
        "complete": complete,
        "status": "CONNECTED" if complete else "MISSING",
        "values": dict(values or {}),
    }


def _snapshot(*, lineups_complete=True, huge_edge=False):
    starter_home = 1.0 if huge_edge else 3.1
    starter_away = 9.0 if huge_edge else 4.2
    groups = {
        "confirmed_probable_pitchers": _group(
            {"homeName": "Home Starter", "awayName": "Away Starter"}
        ),
        "starter_quality": _group(
            {
                "homeFip": starter_home,
                "awayFip": starter_away,
                "homeComposite": 0.66,
                "awayComposite": 0.41,
            }
        ),
        "offense_quality": _group(
            {"homeWrcPlus": 112.0, "awayWrcPlus": 98.0}
        ),
        "bullpen_availability": _group(
            {
                "homeFatigueScore": 0.20,
                "awayFatigueScore": 0.80,
                "homeComposite": 0.70,
                "awayComposite": 0.45,
            }
        ),
        "confirmed_lineups": _group(
            {
                "homeConfirmed": True,
                "awayConfirmed": True,
                "homeStrengthDelta": 0.20,
                "awayStrengthDelta": -0.10,
                "homeWrcPlus": 110.0,
                "awayWrcPlus": 99.0,
            },
            complete=lineups_complete,
        ),
        "travel_rest": _group({"homeRestDays": 2, "awayRestDays": 0}),
        "injuries_late_scratches": _group(
            {
                "homeKeyInjuries": [],
                "awayKeyInjuries": ["Away regular"],
                "lateScratchFlags": [],
                "pitcherChangeFlag": False,
            }
        ),
        "weather_roof": _group({}, complete=False),
        "ballpark_factors": _group({}, complete=False),
    }
    connected = sorted(name for name, value in groups.items() if value["complete"])
    missing = sorted(name for name, value in groups.items() if not value["complete"])
    return {
        "version": "MLB-FUNDAMENTALS-SNAPSHOT-v2-test",
        "fingerprint": "fixture-fingerprint",
        "groups": groups,
        "connectedGroups": connected,
        "missingGroups": missing,
    }


def _row():
    return {
        "predictedSide": "home",
        "homeSignal": {
            "marketConsensusProbability": 0.58,
            "probLatest": 0.58,
            "score": 58.0,
            "tags": ["BOOK_AGREEMENT"],
        },
        "awaySignal": {
            "marketConsensusProbability": 0.42,
            "probLatest": 0.42,
            "score": 42.0,
            "tags": [],
        },
        "tags": ["BOOK_AGREEMENT"],
    }


def test_partial_safe_bridge_applies_core_fundamentals_when_weather_is_missing(monkeypatch):
    snapshot = _snapshot()
    monkeypatch.setattr(bridge, "_snapshot_for_row", lambda row: (snapshot, ()))

    prepared = bridge.apply_to_row(_row())

    assert prepared["fundamentalsApplied"] is True
    assert prepared["winnerOptimizer"]["fundamentalsApplied"] is True
    assert prepared["fundamentalsLayer"]["weatherMissing"] is True
    assert prepared["fundamentalsLayer"]["weatherAffectsSideEdge"] is False
    assert prepared["homeSignal"]["fundamentalsAdjustment"] > 0
    assert prepared["awaySignal"]["fundamentalsAdjustment"] == (
        -prepared["homeSignal"]["fundamentalsAdjustment"]
    )

    scored = winner_stack.enhance_prediction(prepared)
    fundamentals = scored["winnerStackV2"]["components"]["fundamentals"]
    assert fundamentals["applied"] is True
    assert fundamentals["mode"] == "TIMESTAMPED_FUNDAMENTALS_V2_PARTIAL_SAFE"
    assert scored["winnerStackV2"]["weights"]["fundamentals"] == 0.19


def test_bridge_stays_neutral_when_an_essential_group_is_missing(monkeypatch):
    snapshot = _snapshot(lineups_complete=False)
    monkeypatch.setattr(bridge, "_snapshot_for_row", lambda row: (snapshot, ()))

    prepared = bridge.apply_to_row(_row())

    assert prepared["fundamentalsApplied"] is False
    assert prepared["winnerOptimizer"]["fundamentalsApplied"] is False
    assert prepared["fundamentalsLayer"]["reason"] == "essential_groups_incomplete"
    assert prepared["fundamentalsLayer"]["missingEssentialGroups"] == [
        "confirmed_lineups"
    ]
    assert "fundamentalsAdjustment" not in prepared["homeSignal"]


def test_bridge_stays_neutral_when_snapshot_validation_fails(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_snapshot_for_row",
        lambda row: (_snapshot(), ("fundamentals_v2_fingerprint_mismatch",)),
    )

    prepared = bridge.apply_to_row(_row())

    assert prepared["fundamentalsApplied"] is False
    assert prepared["fundamentalsLayer"]["reason"] == "snapshot_missing_or_invalid"
    assert prepared["fundamentalsLayer"]["validationErrors"] == [
        "fundamentals_v2_fingerprint_mismatch"
    ]


def test_bridge_adjustments_are_bounded_and_symmetric(monkeypatch):
    snapshot = _snapshot(huge_edge=True)
    monkeypatch.setattr(bridge, "_snapshot_for_row", lambda row: (snapshot, ()))

    prepared = bridge.apply_to_row(_row())

    assert prepared["homeSignal"]["fundamentalsAdjustment"] == (
        bridge.MAX_SIDE_ADJUSTMENT
    )
    assert prepared["awaySignal"]["fundamentalsAdjustment"] == (
        -bridge.MAX_SIDE_ADJUSTMENT
    )


def test_winner_stack_install_is_idempotent_and_shadow_only(monkeypatch):
    snapshot = _snapshot()
    monkeypatch.setattr(bridge, "_snapshot_for_row", lambda row: (snapshot, ()))
    calls = []
    production_results = []

    def original(row):
        calls.append(copy.deepcopy(row))
        result = dict(row)
        result.update(
            {
                "score": 63.25,
                "winProbability": 0.6412,
                "actionablePick": True,
            }
        )
        production_results.append(result)
        return result

    module = SimpleNamespace(enhance_prediction=original)
    bridge.install_winner_stack(module)
    first = module.enhance_prediction
    bridge.install_winner_stack(module)

    input_row = _row()
    input_row["fundamentalsSnapshotV2"] = copy.deepcopy(snapshot)
    frozen_input = copy.deepcopy(input_row)
    result = module.enhance_prediction(input_row)

    assert module.enhance_prediction is first
    assert len(calls) == 1
    assert input_row == frozen_input
    assert calls == [frozen_input]
    assert "fundamentalsApplied" not in calls[0]
    assert "fundamentalsAdjustment" not in calls[0]["homeSignal"]
    assert result is not production_results[0]
    assert bridge.SHADOW_FIELD not in production_results[0]
    assert {
        key: value
        for key, value in result.items()
        if key != bridge.SHADOW_FIELD
    } == production_results[0]
    shadow = result[bridge.SHADOW_FIELD]
    assert shadow["evaluated"] is True
    assert shadow["shadowOnly"] is True
    assert shadow["liveScoringAuthority"] is False
    assert shadow["canInfluenceLivePick"] is False
    assert shadow["liveScoringInputUsedShadowCandidate"] is False
    assert shadow["wouldApply"] is True
    assert shadow["boundedHypotheticalAdjustments"]["maxAbsolute"] == (
        bridge.MAX_SIDE_ADJUSTMENT
    )
    assert module.MLB_FUNDAMENTALS_SCORING_BRIDGE_VERSION == bridge.VERSION
    assert module.MLB_FUNDAMENTALS_SCORING_BRIDGE_SHADOW_ONLY is True
    assert (
        module.MLB_FUNDAMENTALS_SCORING_BRIDGE_CAN_INFLUENCE_LIVE_PICK is False
    )
    assert module._INQSI_MLB_FUNDAMENTALS_SCORING_SHADOW_V2_INSTALLED is True


def test_shadow_without_attached_snapshot_is_passive_and_never_evaluates(
    monkeypatch,
):
    monkeypatch.setattr(
        bridge,
        "apply_to_row",
        lambda row: pytest.fail("missing snapshot must not trigger evaluation"),
    )

    shadow = bridge.evaluate_shadow(_row())

    assert shadow["evaluated"] is False
    assert shadow["shadowOnly"] is False
    assert shadow["wouldApply"] is False
    assert shadow["reason"] == "snapshot_v2_not_attached_no_live_fetch"
    assert shadow["liveScoringAuthority"] is False


def test_legacy_live_wrapper_is_rejected_instead_of_falsely_attested():
    module = SimpleNamespace(
        enhance_prediction=lambda row: row,
        _INQSI_MLB_FUNDAMENTALS_SCORING_BRIDGE_V1_INSTALLED=True,
    )

    with pytest.raises(
        RuntimeError,
        match="legacy_live_fundamentals_wrapper_requires_process_restart",
    ):
        bridge.install_winner_stack(module)

    assert not hasattr(
        module,
        "_INQSI_MLB_FUNDAMENTALS_SCORING_SHADOW_V2_INSTALLED",
    )


def test_snapshot_installer_propagates_shadow_bridge_failure(monkeypatch):
    def fail_install(module):
        raise RuntimeError("fixture_shadow_install_failure")

    monkeypatch.setattr(bridge, "install_winner_stack", fail_install)
    engine = SimpleNamespace(predict_all=lambda: {"predictions": []})

    with pytest.raises(RuntimeError, match="fixture_shadow_install_failure"):
        snapshot_v1.apply(engine)

    assert not hasattr(engine, "_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V1_APPLIED")
    assert engine._INQSI_MLB_FUNDAMENTALS_SCORING_SHADOW_V2_INSTALLED is False
    assert engine.MLB_FUNDAMENTALS_SCORING_BRIDGE_INSTALL_ERROR == (
        "RuntimeError:fixture_shadow_install_failure"
    )


def test_legacy_snapshot_installer_always_installs_shadow_only_bridge():
    reloaded_winner_stack = importlib.reload(winner_stack)
    engine = SimpleNamespace(predict_all=lambda *args, **kwargs: {"predictions": []})

    snapshot_v1.apply(engine)

    assert engine._INQSI_MLB_FUNDAMENTALS_SCORING_BRIDGE_V1_INSTALLED is True
    assert engine._INQSI_MLB_FUNDAMENTALS_SCORING_SHADOW_V2_INSTALLED is True
    assert engine.MLB_FUNDAMENTALS_SCORING_BRIDGE_VERSION == bridge.VERSION
    assert engine.MLB_FUNDAMENTALS_SCORING_BRIDGE_SHADOW_ONLY is True
    assert engine.MLB_FUNDAMENTALS_SCORING_BRIDGE_CAN_INFLUENCE_LIVE_PICK is False
    assert (
        reloaded_winner_stack._INQSI_MLB_FUNDAMENTALS_SCORING_BRIDGE_V1_INSTALLED
        is True
    )
    assert (
        reloaded_winner_stack._INQSI_MLB_FUNDAMENTALS_SCORING_SHADOW_V2_INSTALLED
        is True
    )


def _provenance(dataset):
    return {
        "provider": "fixture-provider",
        "endpoint": "https://example.invalid/pregame",
        "dataset": dataset,
        "retrievedAtUtc": "2026-08-10T17:00:10+00:00",
        "sourceEffectiveAtUtc": "2026-08-10T16:59:00+00:00",
        "payloadFingerprint": f"fixture-{dataset}",
    }


def _real_v2_row():
    row = _row()
    row.update(
        {
            "gameId": "official:999",
            "officialGamePk": 999,
            "slateDateEt": "2026-08-10",
            "commenceTime": "2026-08-10T19:00:00+00:00",
            "homeTeam": "Home Club",
            "awayTeam": "Away Club",
            "predictionSourcePullAt": "2026-08-10T17:00:00+00:00",
            "predictionSourcePullId": "pull-999",
            "predictionPersistedAtUtc": "2026-08-10T17:01:00+00:00",
            "lockedAtUtc": "2026-08-10T17:15:00+00:00",
            "advanced_context": {
                "confirmed_probable_pitchers": {
                    "source_status": "CONNECTED",
                    "home_probable_pitcher": "Home Starter",
                    "away_probable_pitcher": "Away Starter",
                    "sourceProvenance": _provenance("probable-pitchers"),
                },
                "fip_xfip": {
                    "source_status": "CONNECTED",
                    "home_starter_fip": 3.1,
                    "away_starter_fip": 4.2,
                    "home_starter_xfip": 3.3,
                    "away_starter_xfip": 4.0,
                    "home_starter_k_minus_bb_pct": 0.19,
                    "away_starter_k_minus_bb_pct": 0.12,
                    "sourceProvenance": _provenance("starter-quality"),
                },
                "wrc_plus": {
                    "source_status": "CONNECTED",
                    "home_team_wrc_plus": 112.0,
                    "away_team_wrc_plus": 98.0,
                    "sourceProvenance": _provenance("offense-quality"),
                },
                "starter_handedness_splits": {
                    "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
                    "reason": "not required for partial-safe live scoring",
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
                "weather_wind_roof": {
                    "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
                    "reason": "weather unavailable",
                },
                "ballpark_factors": {
                    "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
                    "reason": "park unavailable",
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
            },
        }
    )
    return row


def test_real_v2_snapshot_validation_and_shadow_candidate_work_together():
    row = _real_v2_row()
    bridge.install_snapshot_shadow_evaluation(snapshot_v2)
    snapshot_v2.enhance_row(row)

    assert snapshot_v2.validate(row["fundamentalsSnapshotV2"]) == []
    assert "weather_roof" in row["fundamentalsSnapshotV2"]["missingGroups"]
    shadow = row[bridge.SHADOW_FIELD]
    assert shadow["evaluated"] is True
    assert shadow["snapshotFingerprint"] == row["fundamentalsSnapshotV2"][
        "fingerprint"
    ]
    assert shadow["snapshotRef"]["fingerprint"] == shadow[
        "snapshotFingerprint"
    ]
    assert shadow["liveScoringAuthority"] is False

    prepared = bridge.apply_to_row(row)

    assert prepared["fundamentalsApplied"] is True
    assert prepared["winnerOptimizer"]["fundamentalsApplied"] is True
    assert prepared["fundamentalsLayer"]["weatherMissing"] is True
    assert prepared["homeSignal"]["fundamentalsAdjustment"] > 0


def test_shadow_refuses_structurally_valid_post_persistence_evidence():
    row = _real_v2_row()
    row["predictionPersistedAtUtc"] = "2026-08-10T17:00:05+00:00"
    snapshot_v2.enhance_row(row)

    shadow = bridge.evaluate_shadow(row)

    assert snapshot_v2.validate(row["fundamentalsSnapshotV2"]) == []
    assert shadow["evaluated"] is False
    assert shadow["shadowOnly"] is False
    assert shadow["wouldApply"] is False
    assert shadow["reason"] == "snapshot_v2_invalid_or_not_lock_safe"
    assert (
        "fundamentals_v2_evidence_not_at_or_before_"
        "persisted_prediction_and_lock"
    ) in shadow["validationErrors"]


def test_selected_away_side_receives_the_inverse_fundamentals_edge(monkeypatch):
    snapshot = _snapshot()
    monkeypatch.setattr(bridge, "_snapshot_for_row", lambda row: (snapshot, ()))
    row = _row()
    row["predictedSide"] = "away"

    prepared = bridge.apply_to_row(row)
    scored = winner_stack.enhance_prediction(prepared)
    fundamentals = scored["winnerStackV2"]["components"]["fundamentals"]

    assert prepared["homeSignal"]["fundamentalsAdjustment"] > 0
    assert prepared["awaySignal"]["fundamentalsAdjustment"] < 0
    assert fundamentals["applied"] is True
    assert fundamentals["edge"] < 0


def test_passive_shadow_survives_prediction_persistence_round_trip():
    row = _real_v2_row()
    row.pop("predictionPersistedAtUtc")
    row.pop("lockedAtUtc")
    snapshot_v2.enhance_row(row)
    shadow = bridge.evaluate_shadow(row)

    assert shadow["evaluated"] is False
    assert shadow["wouldApply"] is False
    assert shadow["validationErrors"] == [
        bridge.EXPECTED_PASSIVE_PROVENANCE_ERROR
    ]
    assert bridge.is_expected_passive_provenance_block(shadow, row) is True

    row[bridge.SHADOW_FIELD] = shadow
    persisted = pull_history.ddb_safe({"data": row})["data"]
    persisted_shadow = persisted[bridge.SHADOW_FIELD]

    assert "home" not in persisted_shadow["boundedHypotheticalAdjustments"]
    assert "away" not in persisted_shadow["boundedHypotheticalAdjustments"]
    assert bridge.is_expected_passive_provenance_block(
        persisted_shadow, persisted
    ) is True
    assert bridge.validate_shadow_attestation(persisted_shadow, persisted) == []

    extra_null = copy.deepcopy(persisted_shadow)
    extra_null["untrustedOptional"] = None
    assert "shadow_canonical_evaluation_mismatch" in (
        bridge.validate_shadow_attestation(extra_null, persisted)
    )


def test_evaluated_shadow_survives_prediction_persistence_round_trip():
    row = _real_v2_row()
    snapshot_v2.enhance_row(row)
    shadow = bridge.evaluate_shadow(row)

    assert shadow["evaluated"] is True
    assert shadow["wouldApply"] is True

    row[bridge.SHADOW_FIELD] = shadow
    persisted = pull_history.ddb_safe({"data": row})["data"]
    persisted_shadow = persisted[bridge.SHADOW_FIELD]

    assert "reason" not in persisted_shadow
    assert bridge.validate_shadow_attestation(persisted_shadow, persisted) == []


def test_evaluated_shadow_without_durable_boundary_remains_invalid():
    row = _real_v2_row()
    row.pop("predictionPersistedAtUtc")
    row.pop("lockedAtUtc")
    snapshot_v2.enhance_row(row)
    shadow = bridge.evaluate_shadow(row)
    forged = copy.deepcopy(shadow)
    forged["evaluated"] = True
    forged["shadowOnly"] = True

    errors = bridge.validate_shadow_attestation(forged, row)

    assert "shadow_current_snapshot_provenance_invalid" in errors
    assert "shadow_canonical_evaluation_mismatch" in errors

