from __future__ import annotations

from typing import Any, Dict, List


def _compact_game(game: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "gameId": game.get("gameId"),
        "gameKey": game.get("gameKey"),
        "homeTeam": game.get("homeTeam"),
        "awayTeam": game.get("awayTeam"),
        "commenceTime": game.get("commenceTime"),
        "slateDate": game.get("slateDate"),
        "providerSportKey": game.get("providerSportKey"),
        "status": game.get("status"),
        "frozen": game.get("frozen"),
        "cutoffTime": game.get("cutoffTime"),
        "pointCount": game.get("pointCount"),
        "selectedSide": game.get("selectedSide"),
        "selection": game.get("selection"),
        "grade": game.get("grade"),
        "score": game.get("score"),
        "tags": game.get("tags") or [],
        "homeSignal": game.get("homeSignal"),
        "awaySignal": game.get("awaySignal"),
        "points": game.get("points") or [],
    }


def _full_board(module: Any, slate: str) -> Dict[str, Any]:
    pulls, games = module.histories(slate)
    rows: List[Dict[str, Any]] = []
    for game in games:
        home = module.score_side(game, "home")
        away = module.score_side(game, "away")
        best = home if float(home.get("score") or 0) >= float(away.get("score") or 0) else away
        selected_side = best.get("side")
        game.update({
            "homeSignal": home,
            "awaySignal": away,
            "selectedSide": selected_side,
            "selection": game.get("homeTeam") if selected_side == "home" else game.get("awayTeam"),
            "grade": best.get("grade"),
            "score": best.get("score"),
            "tags": best.get("tags") or [],
        })
        rows.append(_compact_game(game))
    rows.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
    for idx, row in enumerate(rows, 1):
        row["rank"] = idx
    return {
        "allScoredGamesCount": len(rows),
        "allScoredGames": rows,
        "candidateFilterPolicy": "Candidates are the post-score subset where frozen=true and selected grade is MLB_STRONG or MLB_LEAN. This full board shows the pre-filter scoring pass for every MLB game.",
    }


def apply(module: Any) -> None:
    if module is None or getattr(module, "_inqsi_full_signal_board_installed", False):
        return
    original_build = module.build

    def build(slate=None):
        result = original_build(slate)
        try:
            resolved_slate = result.get("slate_date") or slate or module.today()
            result.update(_full_board(module, resolved_slate))
        except Exception as exc:
            result["allScoredGamesError"] = str(exc)
        return result

    module.build = build
    module._inqsi_full_signal_board_installed = True
