from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_fundamentals_scoring_bridge_v1 as bridge
import mlb_fundamentals_snapshot_v1 as snapshot_v1
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


def test_winner_stack_install_is_idempotent(monkeypatch):
    snapshot = _snapshot()
    monkeypatch.setattr(bridge, "_snapshot_for_row", lambda row: (snapshot, ()))
    calls = []

    def original(row):
        calls.append(row)
        return row

    module = SimpleNamespace(enhance_prediction=original)
    bridge.install_winner_stack(module)
    first = module.enhance_prediction
    bridge.install_winner_stack(module)

    result = module.enhance_prediction(_row())

    assert module.enhance_prediction is first
    assert len(calls) == 1
    assert result["fundamentalsApplied"] is True
    assert module.MLB_FUNDAMENTALS_SCORING_BRIDGE_VERSION == bridge.VERSION


def test_legacy_snapshot_installer_always_installs_live_scoring_bridge():
    reloaded_winner_stack = importlib.reload(winner_stack)
    engine = SimpleNamespace(predict_all=lambda *args, **kwargs: {"predictions": []})

    snapshot_v1.apply(engine)

    assert engine._INQSI_MLB_FUNDAMENTALS_SCORING_BRIDGE_V1_INSTALLED is True
    assert engine.MLB_FUNDAMENTALS_SCORING_BRIDGE_VERSION == bridge.VERSION
    assert (
        reloaded_winner_stack._INQSI_MLB_FUNDAMENTALS_SCORING_BRIDGE_V1_INSTALLED
        is True
    )
