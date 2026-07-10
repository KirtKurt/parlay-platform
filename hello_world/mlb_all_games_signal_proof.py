from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import inqsi_pull_history as history
import mlb_b10_engine
import mlb_game_winner_engine

try:
    import mlb_accuracy_target_patch
    mlb_accuracy_target_patch.apply(mlb_game_winner_engine)
except Exception:
    pass

try:
    import slate_date_patch
    slate_date_patch.apply_to_history(history)
except Exception:
    pass

SLATE_TZ = ZoneInfo("America/New_York")
REPORT_PATH = "runtime_reports/mlb_all_games_signal_proof_latest.json"


def _today_et() -> str:
    return datetime.now(SLATE_TZ).date().isoformat()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return default


def _compact_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "side": signal.get("side"),
        "team": signal.get("team"),
        "grade": signal.get("grade"),
        "score": _safe_float(signal.get("score")),
        "winProbability": signal.get("winProbability"),
        "winProbabilityPct": signal.get("winProbabilityPct"),
        "marketConsensusProbability": signal.get("marketConsensusProbability"),
        "probStart": signal.get("probStart"),
        "probLatest": signal.get("probLatest"),
        "delta": signal.get("delta"),
        "bookCount": signal.get("bookCount"),
        "bookDivergence": signal.get("bookDivergence"),
        "latestGap": signal.get("latestGap"),
        "reversalCount": signal.get("reversalCount"),
        "runLineMovement": signal.get("runLineMovement"),
        "averageAmericanOdds": signal.get("averageAmericanOdds"),
        "tags": signal.get("tags") or [],
    }


def _compact_winner(winner: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not winner:
        return None
    has_winner = bool(winner.get("predictedWinner"))
    playable = bool(winner.get("actionablePick") is True or winner.get("officialPick") is True)
    return {
        "predictedWinner": winner.get("predictedWinner"),
        "predictedSide": winner.get("predictedSide"),
        "opponent": winner.get("opponent"),
        "winProbability": winner.get("winProbability"),
        "winProbabilityPct": winner.get("winProbabilityPct"),
        "score": winner.get("score"),
        "rank": winner.get("rank"),
        "confidenceTier": winner.get("confidenceTier"),
        "pickQuality": winner.get("pickQuality"),
        "platformPick": bool(winner.get("platformPick") or has_winner),
        "officialPrediction": bool(winner.get("officialPrediction") or has_winner),
        "customerVisibleWinnerPick": bool(winner.get("customerVisibleWinnerPick") or has_winner),
        "requiredGameWinnerPrediction": bool(winner.get("requiredGameWinnerPrediction") or has_winner),
        "winnerPredictionAvailable": has_winner,
        "displayPrediction": bool(winner.get("displayPrediction") or has_winner),
        "predictionDisplayStatus": winner.get("predictionDisplayStatus") or ("REQUIRED_GAME_WINNER_PREDICTION" if has_winner else "MISSING_GAME_WINNER_PREDICTION"),
        "recommendationStatus": winner.get("recommendationStatus") or ("PLAYABLE_PREDICTION" if playable else "LOW_CONFIDENCE_PREDICTION_NOT_PLAYABLE"),
        "playable": playable,
        "actionablePick": winner.get("actionablePick"),
        "officialPick": winner.get("officialPick"),
        "accuracyTargetEligible": winner.get("accuracyTargetEligible"),
        "actionability": winner.get("actionability"),
        "actionabilityReason": winner.get("actionabilityReason"),
        "actionabilityRiskReasons": winner.get("actionabilityRiskReasons") or [],
        "rolling24hAccuracyTarget": winner.get("rolling24hAccuracyTarget"),
        "accuracyGatePolicy": winner.get("accuracyGatePolicy"),
        "scoreBeforeWinnerStackV2": winner.get("scoreBeforeWinnerStackV2"),
        "winProbabilityBeforeWinnerStackV2": winner.get("winProbabilityBeforeWinnerStackV2"),
        "scoreBeforeSignalPolicyV13": winner.get("scoreBeforeSignalPolicyV13"),
        "scoreAfterSignalPolicyV13": winner.get("scoreAfterSignalPolicyV13"),
        "signalPolicyV13Adjustment": winner.get("signalPolicyV13Adjustment"),
        "tags": winner.get("tags") or [],
        "pullCountForGame": winner.get("pullCountForGame"),
        "homeSignal": _compact_signal(winner.get("homeSignal") or {}),
        "awaySignal": _compact_signal(winner.get("awaySignal") or {}),
        "winnerStackV2": winner.get("winnerStackV2"),
        "mlSignalLayers": winner.get("mlSignalLayers"),
        "mlOverlay": winner.get("mlOverlay"),
        "signalPolicyV13": winner.get("signalPolicyV13"),
        "slatePredictionLock": winner.get("slatePredictionLock"),
        "lockedPrediction": winner.get("lockedPrediction"),
        "lockedAtUtc": winner.get("lockedAtUtc"),
        "predictionSourcePullAt": winner.get("predictionSourcePullAt"),
        "stored": winner.get("stored"),
    }


def _compact_b10_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "side": signal.get("side"),
        "grade": signal.get("grade"),
        "score": _safe_float(signal.get("score")),
        "probStart": signal.get("probStart"),
        "probLatest": signal.get("probLatest"),
        "delta": signal.get("delta"),
        "velocityPpHr": signal.get("velocityPpHr"),
        "bookDivergenceAvg": signal.get("bookDivergenceAvg"),
        "bookAgreementAvg": signal.get("bookAgreementAvg"),
        "reversalCount": signal.get("reversalCount"),
        "lateInstability": signal.get("lateInstability"),
        "totalMovement": signal.get("totalMovement"),
        "runLineMovement": signal.get("runLineMovement"),
        "tags": signal.get("tags") or [],
        "pointCount": signal.get("pointCount"),
        "reason": signal.get("reason"),
    }


def _index_by_game_key(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = row.get("gameKey") or row.get("game_key") or row.get("gameId")
        if key:
            out[str(key)] = row
    return out


def _b10_all_games(slate: str) -> Dict[str, Any]:
    pulls, games = mlb_b10_engine.histories(slate)
    coverage = mlb_b10_engine.pull_coverage(pulls, slate)
    rows = []
    for game in games:
        home = mlb_b10_engine.score_side(game, "home")
        away = mlb_b10_engine.score_side(game, "away")
        selected = home if _safe_float(home.get("score"), 0.0) >= _safe_float(away.get("score"), 0.0) else away
        selected_side = selected.get("side")
        rows.append({
            "gameId": game.get("gameId"),
            "gameKey": game.get("gameKey"),
            "homeTeam": game.get("homeTeam"),
            "awayTeam": game.get("awayTeam"),
            "commenceTime": game.get("commenceTime"),
            "providerSportKey": game.get("providerSportKey"),
            "slateDate": game.get("slateDate"),
            "status": game.get("status"),
            "frozen": game.get("frozen"),
            "cutoffTime": game.get("cutoffTime"),
            "pointCount": game.get("pointCount"),
            "selectedSide": selected_side,
            "selectedTeam": game.get("homeTeam") if selected_side == "home" else game.get("awayTeam"),
            "selectedGrade": selected.get("grade"),
            "selectedScore": _safe_float(selected.get("score")),
            "homeSignal": _compact_b10_signal(home),
            "awaySignal": _compact_b10_signal(away),
            "points": game.get("points") or [],
        })
    rows.sort(key=lambda row: (_safe_float(row.get("selectedScore"), 0.0) or 0.0), reverse=True)
    return {
        "pullCount": len(pulls),
        "coverage": coverage,
        "gameCount": len(games),
        "rows": rows,
    }


def _combined_rows(winners: Dict[str, Any], b10: Dict[str, Any]) -> List[Dict[str, Any]]:
    winner_by_key = _index_by_game_key(winners.get("predictions") or [])
    rows = []
    for b10_row in b10.get("rows") or []:
        key = b10_row.get("gameKey") or b10_row.get("gameId")
        winner = winner_by_key.get(str(key)) or {}
        rows.append({
            "gameId": b10_row.get("gameId"),
            "gameKey": b10_row.get("gameKey"),
            "matchup": f"{b10_row.get('awayTeam')} at {b10_row.get('homeTeam')}",
            "homeTeam": b10_row.get("homeTeam"),
            "awayTeam": b10_row.get("awayTeam"),
            "commenceTime": b10_row.get("commenceTime"),
            "providerSportKey": b10_row.get("providerSportKey"),
            "b10Status": b10_row.get("status"),
            "b10Frozen": b10_row.get("frozen"),
            "b10PointCount": b10_row.get("pointCount"),
            "b10SelectedTeam": b10_row.get("selectedTeam"),
            "b10SelectedSide": b10_row.get("selectedSide"),
            "b10SelectedGrade": b10_row.get("selectedGrade"),
            "b10SelectedScore": b10_row.get("selectedScore"),
            "b10HomeSignal": b10_row.get("homeSignal"),
            "b10AwaySignal": b10_row.get("awaySignal"),
            "gameWinnerPrediction": _compact_winner(winner),
            "rawB10Points": b10_row.get("points") or [],
        })
    rows.sort(key=lambda row: (_safe_float((row.get("gameWinnerPrediction") or {}).get("score"), -1.0) or -1.0, _safe_float(row.get("b10SelectedScore"), 0.0) or 0.0), reverse=True)
    for idx, row in enumerate(rows, 1):
        row["rank"] = idx
    return rows


def build(slate_date: Optional[str] = None, store: bool = True, write_file: bool = True) -> Dict[str, Any]:
    slate = slate_date or _today_et()
    winners = mlb_game_winner_engine.predict_all(slate, store=store, limit=500)
    b10 = _b10_all_games(slate)
    rows = _combined_rows(winners, b10)
    winner_rows = [r for r in rows if r.get("gameWinnerPrediction")]
    required_rows = [r for r in winner_rows if (r.get("gameWinnerPrediction") or {}).get("displayPrediction")]
    actionable_rows = [r for r in winner_rows if (r.get("gameWinnerPrediction") or {}).get("playable")]
    low_confidence_rows = [r for r in required_rows if not (r.get("gameWinnerPrediction") or {}).get("playable")]
    proof = {
        "ok": True,
        "proofType": "MLB_ALL_GAMES_SIGNAL_PROOF",
        "createdAtUtc": _now_utc(),
        "createdAtEt": datetime.now(SLATE_TZ).isoformat(),
        "sport": "mlb",
        "slate_date": slate,
        "source": "15-minute pull history only plus current moneyline/run-line book data",
        "pullCount": b10.get("pullCount"),
        "coverage": b10.get("coverage"),
        "providerGameCount": winners.get("gameCount"),
        "b10GameCount": b10.get("gameCount"),
        "gameWinnerPredictionCount": winners.get("count"),
        "allGamesPredicted": winners.get("allGamesPredicted"),
        "storedGameWinnerCount": winners.get("storedCount"),
        "requiredGameWinnerPredictionCount": len(required_rows),
        "allGamesHaveDisplayedWinnerPrediction": bool(rows and len(required_rows) == len(rows)),
        "rolling24hAccuracyTarget": winners.get("rolling24hAccuracyTarget"),
        "accuracyTarget": winners.get("accuracyTarget"),
        "winnerStackV2": winners.get("winnerStackV2"),
        "requiredWinnerPredictionDisplay": winners.get("requiredWinnerPredictionDisplay") or [],
        "officialPredictionDisplay": winners.get("officialPredictionDisplay") or [],
        "playablePredictionDisplay": winners.get("playablePredictionDisplay") or [],
        "nonOfficialPredictionDisplay": winners.get("nonOfficialPredictionDisplay") or [],
        "rows": rows,
        "summary": {
            "totalRows": len(rows),
            "gamesWithWinnerPrediction": len(winner_rows),
            "gamesMissingWinnerPrediction": [r.get("matchup") for r in rows if not r.get("gameWinnerPrediction")],
            "requiredGameWinnerPredictionCount": len(required_rows),
            "allGamesHaveDisplayedWinnerPrediction": bool(rows and len(required_rows) == len(rows)),
            "gamesMissingDisplayedWinnerPrediction": [r.get("matchup") for r in rows if not (r.get("gameWinnerPrediction") or {}).get("displayPrediction")],
            "playablePredictionCount": len(actionable_rows),
            "lowConfidencePredictionCount": len(low_confidence_rows),
            "playableTeams": [(r.get("gameWinnerPrediction") or {}).get("predictedWinner") for r in actionable_rows],
            "lowConfidencePredictionTeams": [(r.get("gameWinnerPrediction") or {}).get("predictedWinner") for r in low_confidence_rows],
            "b10QualifiedCandidates": sum(1 for r in rows if r.get("b10SelectedGrade") in {"MLB_STRONG", "MLB_LEAN"}),
        },
        "policy": "Every MLB game with convertible 15-minute pull data receives one visible platform winner prediction. Playable/actionable status is separate and may be false without removing the prediction.",
    }
    if write_file:
        os.makedirs("runtime_reports", exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(proof, f, indent=2, default=str)
            f.write("\n")
    return proof


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
