from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_all_games_coverage_patch as coverage_patch
import mlb_all_games_signal_proof as proof_module


GAME_KEY = "mlb|authority-test"


def _winner(*, official: bool = False, locked: bool = False):
    return {
        "gameId": "authority-test",
        "gameIdentity": "authority-test",
        "gameKey": GAME_KEY,
        "homeTeam": "Published Winners",
        "awayTeam": "Raw Leaders",
        "commenceTime": "2026-07-18T23:00:00Z",
        "predictedWinner": "Published Winners",
        "predictedSide": "home",
        "winProbabilityPct": 57.25,
        "score": 61.5,
        "displayPrediction": True,
        "officialPrediction": official,
        "officialPick": official,
        "lockedPrediction": locked,
        "perGameCanonicalLock": {
            "canonical": bool(official and locked),
            "status": "OFFICIAL_LOCKED_PREDICTION" if official and locked else "OPEN_PRE_LOCK",
        },
        "immutableLockedStorage": bool(official and locked),
    }


def _b10():
    return {
        "gameId": "authority-test",
        "gameKey": GAME_KEY,
        "homeTeam": "Published Winners",
        "awayTeam": "Raw Leaders",
        "commenceTime": "2026-07-18T23:00:00Z",
        "selectedTeam": "Raw Leaders",
        "selectedSide": "away",
        "selectedGrade": "MLB_LEAN",
        "selectedScore": 72.0,
        "homeSignal": {"team": "Published Winners", "score": 50.0},
        "awaySignal": {"team": "Raw Leaders", "score": 72.0},
        "points": [],
    }


def test_combined_row_labels_only_game_winner_authority_as_prediction():
    module = importlib.reload(proof_module)

    rows = module._combined_rows(
        {"predictions": [_winner()]},
        {"rows": [_b10()]},
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["predictedWinner"] == "Published Winners"
    assert row["publishedPrediction"]["predictedWinner"] == "Published Winners"
    assert row["publishedPrediction"]["sourceField"] == (
        "requiredWinnerPredictionDisplay[].predictedWinner"
    )
    assert row["rawSignalScoreLeader"]["team"] == "Raw Leaders"
    assert row["rawSignalScoreLeader"]["isPublishedPrediction"] is False
    assert row["b10SelectedTeam"] == "Raw Leaders"
    assert row["b10SelectedTeamIsPrediction"] is False
    assert row["signalPredictionAgreement"] is False
    assert row["officialPrediction"] is False


def test_prelock_row_cannot_be_promoted_to_official_by_display_compaction():
    module = importlib.reload(proof_module)
    coverage_patch.apply(module)

    rows = module._combined_rows(
        {"predictions": [_winner(official=True, locked=False)]},
        {"rows": [_b10()]},
    )

    card = rows[0]["gameWinnerPrediction"]
    assert card["displayPrediction"] is True
    assert card["officialPrediction"] is False
    assert card["officialPick"] is False
    assert rows[0]["publishedPrediction"]["officialPrediction"] is False


def test_validated_canonical_overlay_is_the_only_official_prediction():
    module = importlib.reload(proof_module)
    coverage_patch.apply(module)

    rows = module._combined_rows(
        {"predictions": [_winner(official=True, locked=True)]},
        {"rows": [_b10()]},
    )

    card = rows[0]["gameWinnerPrediction"]
    assert card["officialPrediction"] is True
    assert card["officialPick"] is True
    assert rows[0]["publishedPrediction"]["officialPrediction"] is True
    assert rows[0]["publishedPrediction"]["lockedPrediction"] is True


def test_partial_lock_markers_cannot_spoof_official_status():
    module = importlib.reload(proof_module)
    winner = _winner(official=True, locked=True)
    winner["immutableLockedStorage"] = False

    rows = module._combined_rows(
        {"predictions": [winner]},
        {"rows": [_b10()]},
    )

    assert rows[0]["officialPrediction"] is False
    assert rows[0]["publishedPrediction"]["officialPrediction"] is False


def test_build_exposes_unambiguous_published_display_and_diagnostic_role(monkeypatch):
    module = importlib.reload(proof_module)
    coverage_patch.apply(module)
    # Deliberately stale upstream display metadata must not bypass normalized
    # per-row authority.
    display = [{
        "gameId": "authority-test",
        "predictedWinner": "Raw Leaders",
        "officialPrediction": True,
    }]
    monkeypatch.setattr(
        module.mlb_game_winner_engine,
        "predict_all",
        lambda *args, **kwargs: {
            "gameCount": 1,
            "count": 1,
            "storedCount": 1,
            "predictions": [_winner()],
            "requiredWinnerPredictionDisplay": display,
            "officialPredictionDisplay": [],
            "playablePredictionDisplay": [],
            "nonOfficialPredictionDisplay": display,
            "slateCoverage": {"coverageComplete": True},
            "slatePredictionLock": {"locked": False},
        },
    )
    monkeypatch.setattr(
        module,
        "_b10_all_games",
        lambda slate: {"gameCount": 1, "pullCount": 1, "coverage": {}, "rows": [_b10()]},
    )

    proof = module.build("2026-07-18", store=False, write_file=False)

    assert proof["publishedPredictionDisplay"] != display
    assert proof["requiredWinnerPredictionDisplay"] == proof["publishedPredictionDisplay"]
    assert proof["publishedPredictionDisplay"][0]["predictedWinner"] == "Published Winners"
    assert proof["publishedPredictionDisplay"][0]["officialPrediction"] is False
    assert proof["publishedPredictionAuthority"]["rawB10SignalLeaderIsPrediction"] is False
    assert proof["summary"]["officialPredictionCount"] == 0
    assert proof["rows"][0]["predictedWinner"] == "Published Winners"


def test_retired_signal_proof_workflow_cannot_publish_or_write_predictions():
    workflow = (
        ROOT / ".github" / "workflows" / "proof-run-1800-et-mlb-all-signals.yml"
    )

    assert workflow.exists() is False
