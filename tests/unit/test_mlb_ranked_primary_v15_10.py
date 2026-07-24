from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_ranked_primary_v15_10 as ranked


def _row(date: str, *, home_market: float = 0.62, official: bool = False) -> dict:
    return {
        "sport": "mlb",
        "slate_date": date,
        "gameId": "game-1",
        "gameIdentity": "game-1",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "commenceTime": f"{date}T23:10:00Z",
        "predictedWinner": "Away Club",  # legacy direction must be replaced
        "predictedSide": "away",
        "opponent": "Home Club",
        "winProbability": 0.55,
        "score": 55.0,
        "officialPrediction": official,
        "officialPick": official,
        "tags": ["NO_PICK", "RELEASE_BLOCKED"],
        "blockedReasons": ["legacy_negative_ev"],
        "homeMarketDeVigProbability": home_market,
        "awayMarketDeVigProbability": 1.0 - home_market,
        "homeSignal": {
            "team": "Home Club",
            "americanOdds": -165,
            "marketProbability": home_market,
            "fairProbability": home_market,
        },
        "awaySignal": {
            "team": "Away Club",
            "americanOdds": 145,
            "marketProbability": 1.0 - home_market,
            "fairProbability": 1.0 - home_market,
        },
        "features": {"total_line": 8.5, "home_runline": -1.5},
    }


def test_exported_model_matches_original_joblib_for_live_feature_vector() -> None:
    source = Path(
        "/mnt/data/work_v1510/nfl_cfb_parlay_v15.10_RANKED_PRIMARY/"
        "models/mlb/active/leg_model.joblib"
    )
    if not source.exists():
        pytest.skip("reference active model is not present")
    joblib = pytest.importorskip("joblib")
    pd = pytest.importorskip("pandas")
    reference_root = source.parents[3]
    if str(reference_root) not in sys.path:
        sys.path.insert(0, str(reference_root))
    payload = joblib.load(source)
    model = payload["model"]
    row = _row("2026-07-24")
    vector, available = ranked.feature_vector(row)
    frame = pd.DataFrame([{name: vector[index] for index, name in enumerate(ranked.FEATURE_COLUMNS)}])
    expected = float(model.predict_proba(frame)[0, 1])
    actual = float(ranked.score_home_probability(row)["home_probability"])
    assert actual == pytest.approx(expected, abs=1e-12)
    assert available["market_home"] == pytest.approx(0.62)


def test_before_cutover_is_unchanged() -> None:
    original = _row("2026-07-23")
    assert ranked.apply_model_direction(original) == original
    assert ranked.apply_selection(original) == original


def test_direction_is_owned_by_exported_active_model() -> None:
    result = ranked.apply_model_direction(_row("2026-07-24"))
    assert result["modelVersion"] == ranked.VERSION
    assert result["legacySelectorUsed"] is False
    assert result["homeModelWinProbability"] + result["awayModelWinProbability"] == pytest.approx(1.0)
    assert result["rankedWinnerModel"]["artifactSha256"] == ranked.MODEL_ARTIFACT_SHA256
    assert result["rankedWinnerModel"]["priorSelectorDiagnostic"]["predictedSide"] == "away"


def test_every_valid_july24_game_becomes_pick_even_without_precision_evidence() -> None:
    directed = ranked.apply_model_direction(_row("2026-07-24"))
    home_p = directed["homeModelWinProbability"]
    directed.update(
        {
            "predictedSide": "home" if home_p >= 0.5 else "away",
            "predictedWinner": "Home Club" if home_p >= 0.5 else "Away Club",
            "modelWinProbability": max(home_p, 1.0 - home_p),
            "winProbability": max(home_p, 1.0 - home_p),
        }
    )
    result = ranked.apply_selection(directed)
    assert result["selectionStatus"] == "PICK"
    assert result["productionSelectionAllowed"] is True
    assert result["actionablePick"] is True
    assert result["precisionQualified"] is False
    assert result["productionTradeAllowed"] is False
    assert result["automaticWagerAllowed"] is False
    assert result["legacySelectorUsed"] is False
    assert result["blockedReasons"] == []
    assert "legacy_negative_ev" in result["diagnosticLegacyBlockReasons"]


def test_apply_wraps_live_and_persisted_readers_and_is_idempotent() -> None:
    def base(date: str, **kwargs):
        return {"slate_date": date, "predictions": [_row(date)]}

    module = SimpleNamespace(predict_all=base, read_persisted_predictions=base)
    ranked.apply_direction(module)
    first_direction = module.predict_all
    ranked.apply_direction(module)
    assert module.predict_all is first_direction

    # Simulate the canonical probability contract between direction and selection.
    direction_result = module.predict_all("2026-07-24")
    for row in direction_result["predictions"]:
        home_p = row["homeModelWinProbability"]
        row["predictedSide"] = "home" if home_p >= 0.5 else "away"
        row["predictedWinner"] = "Home Club" if home_p >= 0.5 else "Away Club"
        row["modelWinProbability"] = max(home_p, 1.0 - home_p)
        row["winProbability"] = max(home_p, 1.0 - home_p)

    module.predict_all = lambda *args, **kwargs: direction_result
    module.read_persisted_predictions = lambda *args, **kwargs: direction_result
    ranked.apply_selection_authority(module)
    first_selection = module.predict_all
    ranked.apply_selection_authority(module)
    assert module.predict_all is first_selection
    result = module.predict_all("2026-07-24")
    assert result["productionSelectionCount"] == 1
    assert result["passCount"] == 0
    assert result["predictions"][0]["selectionStatus"] == "PICK"
