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
            "gameWinnerPrediction": {
                "predictedWinner": winner.get("predictedWinner"),
                "predictedSide": winner.get("predictedSide"),
                "opponent": winner.get("opponent"),
                "winProbability": winner.get("winProbability"),
                "winProbabilityPct": winner.get("winProbabilityPct"),
                "score": winner.get("score"),
                "rawScoreBefore75TargetCalibration": winner.get("rawScoreBefore75TargetCalibration"),
                "rawWinProbabilityPctBefore75TargetCalibration": winner.get("rawWinProbabilityPctBefore75TargetCalibration"),
                "calibrationPenalty": winner.get("calibrationPenalty"),
                "targetAccuracyPct": winner.get("targetAccuracyPct"),
                "officialPick": winner.get("officialPick"),
                "accuracyTargetEligible": winner.get("accuracyTargetEligible"),
                "actionability": winner.get("actionability"),
                "actionabilityReason": winner.get("actionabilityReason"),
                "accuracyGatePolicy": winner.get("accuracyGatePolicy"),
                "confidenceTier": winner.get("confidenceTier"),
                "pickQuality": winner.get("pickQuality"),
                "tags": winner.get("tags") or [],
                "pullCountForGame": winner.get("pullCountForGame"),
                "homeSignal": _compact_signal(winner.get("homeSignal") or {}),
                "awaySignal": _compact_signal(winner.get("awaySignal") or {}),
                "stored": winner.get("stored"),
            } if winner else None,
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
    official_rows = [r for r in rows if (r.get("gameWinnerPrediction") or {}).get("officialPick")]
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
        "accuracyTarget": winners.get("accuracyTarget"),
        "rows": rows,
        "summary": {
            "totalRows": len(rows),
            "gamesWithWinnerPrediction": sum(1 for r in rows if r.get("gameWinnerPrediction")),
            "gamesMissingWinnerPrediction": [r.get("matchup") for r in rows if not r.get("gameWinnerPrediction")],
            "official75TargetPicks": len(official_rows),
            "official75TargetTeams": [(r.get("gameWinnerPrediction") or {}).get("predictedWinner") for r in official_rows],
            "b10QualifiedCandidates": sum(1 for r in rows if r.get("b10SelectedGrade") in {"MLB_STRONG", "MLB_LEAN"}),
        },
        "policy": "Every MLB game with convertible 15-minute pull data must receive an individual game-winner score. Only officialPick=true rows count toward the 75% individual-pick accuracy target.",
    }
    if write_file:
        os.makedirs("runtime_reports", exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(proof, f, indent=2, default=str)
            f.write("\n")
    return proof


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
