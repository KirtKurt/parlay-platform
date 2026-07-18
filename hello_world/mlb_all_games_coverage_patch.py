from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from mlb_slate_coverage_patch import VERSION as COVERAGE_VERSION, game_identity

VERSION = "MLB-ALL-GAMES-PROOF-v2-complete-slate-doubleheader-safe"


def _tags(row: Dict[str, Any]) -> set[str]:
    return {str(value) for value in (row.get("tags") or [])}


def _playable(row: Dict[str, Any]) -> bool:
    tags = _tags(row)
    recommendation = str(row.get("recommendationStatus") or "").upper()
    actionability = str(row.get("actionability") or "").upper()
    if "NOT_PLAYABLE" in tags or "ML_REJECTED" in tags or "NOT_PLAYABLE" in recommendation or "LOW_CONFIDENCE" in recommendation or "NOT_PLAYABLE" in actionability:
        return False
    return bool(
        row.get("playable") is True
        or row.get("playablePick") is True
        or row.get("actionablePick") is True
        or row.get("accuracyTargetEligible") is True
        or recommendation == "PLAYABLE_PREDICTION"
        or "ACTIONABLE_PICK" in tags
        or "ML_CONFIRMED" in tags
    )


def _identity_keys(row: Dict[str, Any]) -> List[str]:
    keys = [game_identity(row)]
    game_key = row.get("gameKey") or row.get("game_key")
    commence = row.get("commenceTime") or row.get("commence_time")
    if game_key and commence:
        keys.append(f"key:{game_key}|start:{commence}")
    return list(dict.fromkeys(str(key) for key in keys if key))


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_ALL_GAMES_COVERAGE_PATCH_APPLIED", False):
        return module

    original_compact_winner = module._compact_winner

    def compact_winner(winner: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        card = original_compact_winner(winner)
        if not card:
            return card
        playable = _playable(winner)
        canonical = card.get("officialPrediction") is True
        card["playable"] = playable
        card["playablePick"] = playable
        card["recommendationStatus"] = winner.get("recommendationStatus") or (
            "PLAYABLE_PREDICTION"
            if playable
            else "OFFICIAL_PREDICTION_NOT_PLAYABLE"
            if canonical
            else "PRE_LOCK_PREDICTION_NOT_PLAYABLE"
        )
        card["officialPrediction"] = canonical
        card["officialPick"] = canonical
        card["gameIdentity"] = game_identity(winner)
        return card

    module._compact_winner = compact_winner

    def combined_rows(winners: Dict[str, Any], b10: Dict[str, Any]) -> List[Dict[str, Any]]:
        index: Dict[str, Dict[str, Any]] = {}
        for row in winners.get("predictions") or []:
            for key in _identity_keys(row):
                index[key] = row
        rows = []
        for b10_row in b10.get("rows") or []:
            winner = None
            for key in _identity_keys(b10_row):
                if key in index:
                    winner = index[key]
                    break
            winner_card = compact_winner(winner or {})
            rows.append({
                "gameId": b10_row.get("gameId"),
                "gameKey": b10_row.get("gameKey"),
                "gameIdentity": game_identity(b10_row),
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
                **module._authority_fields(winner_card, b10_row),
                "gameWinnerPrediction": winner_card,
                "rawB10Points": b10_row.get("points") or [],
            })
        rows.sort(key=lambda row: (module._safe_float((row.get("gameWinnerPrediction") or {}).get("score"), -1.0) or -1.0, module._safe_float(row.get("b10SelectedScore"), 0.0) or 0.0), reverse=True)
        for index_value, row in enumerate(rows, 1):
            row["rank"] = index_value
        return rows

    module._combined_rows = combined_rows

    def build(slate_date: Optional[str] = None, store: bool = True, write_file: bool = True) -> Dict[str, Any]:
        slate = slate_date or module._today_et()
        winners = module.mlb_game_winner_engine.predict_all(slate, store=store, limit=500)
        b10 = module._b10_all_games(slate)
        rows = combined_rows(winners, b10)
        winner_rows = [row for row in rows if row.get("gameWinnerPrediction")]
        required_rows = [row for row in winner_rows if (row.get("gameWinnerPrediction") or {}).get("displayPrediction")]
        playable_rows = [row for row in winner_rows if (row.get("gameWinnerPrediction") or {}).get("playable")]
        low_confidence_rows = [row for row in required_rows if not (row.get("gameWinnerPrediction") or {}).get("playable")]
        missing_rows = [row for row in rows if not row.get("gameWinnerPrediction")]
        published_display = module._published_display_rows(rows)
        official_display = [
            row for row in published_display if row.get("officialPrediction") is True
        ]
        playable_display = [
            row for row in published_display if row.get("playable") is True
        ]
        non_official_display = [
            row for row in published_display if row.get("officialPrediction") is not True
        ]
        slate_coverage = dict(winners.get("slateCoverage") or {})
        locked = bool((winners.get("slatePredictionLock") or {}).get("locked"))
        count_match = len(rows) == int(winners.get("gameCount") or len(rows)) == int(b10.get("gameCount") or len(rows))
        coverage_complete = bool(slate_coverage.get("coverageComplete")) if locked else not missing_rows
        coverage_complete = bool(coverage_complete and count_match and not missing_rows)
        operational_defect = bool(locked and not coverage_complete)
        slate_coverage.update({
            "proofVersion": VERSION,
            "coverageVersion": slate_coverage.get("version") or COVERAGE_VERSION,
            "locked": locked,
            "proofRowCount": len(rows),
            "winnerGameCount": winners.get("gameCount"),
            "b10GameCount": b10.get("gameCount"),
            "proofMissingPredictionCount": len(missing_rows),
            "proofMissingGameIdentities": [row.get("gameIdentity") for row in missing_rows],
            "countMatch": count_match,
            "coverageComplete": coverage_complete,
            "operationalStatus": "COMPLETE" if coverage_complete else "INCOMPLETE_BLOCKED" if locked else "OPEN_PRE_LOCK",
            "publicAccuracyEligible": bool(locked and coverage_complete),
        })
        proof = {
            "ok": not operational_defect,
            "proofType": "MLB_ALL_GAMES_SIGNAL_PROOF",
            "proofVersion": VERSION,
            "createdAtUtc": module._now_utc(),
            "createdAtEt": datetime.now(module.SLATE_TZ).isoformat(),
            "sport": "mlb",
            "slate_date": slate,
            "source": "15-minute pull history only plus current moneyline/run-line book data",
            "pullCount": b10.get("pullCount"),
            "coverage": b10.get("coverage"),
            "slateCoverage": slate_coverage,
            "operationalDefect": operational_defect,
            "providerGameCount": winners.get("gameCount"),
            "b10GameCount": b10.get("gameCount"),
            "gameWinnerPredictionCount": winners.get("count"),
            "allGamesPredicted": coverage_complete,
            "storedGameWinnerCount": winners.get("storedCount"),
            "requiredGameWinnerPredictionCount": len(required_rows),
            "allGamesHaveDisplayedWinnerPrediction": bool(rows and len(required_rows) == len(rows)),
            "rolling24hAccuracyTarget": winners.get("rolling24hAccuracyTarget"),
            "accuracyTarget": winners.get("accuracyTarget"),
            "winnerStackV2": winners.get("winnerStackV2"),
            "publishedPredictionAuthority": {
                "version": module.PUBLISHED_PREDICTION_AUTHORITY_VERSION,
                "source": module.PUBLISHED_PREDICTION_SOURCE,
                "sourceField": "requiredWinnerPredictionDisplay[].predictedWinner",
                "rawB10SignalLeaderIsPrediction": False,
                "officialRequiresCanonicalImmutableLock": True,
            },
            "publishedPredictionDisplay": published_display,
            "requiredWinnerPredictionDisplay": published_display,
            "officialPredictionDisplay": official_display,
            "playablePredictionDisplay": playable_display,
            "nonOfficialPredictionDisplay": non_official_display,
            "rows": rows,
            "summary": {
                "totalRows": len(rows),
                "gamesWithWinnerPrediction": len(winner_rows),
                "gamesMissingWinnerPrediction": [row.get("matchup") for row in missing_rows],
                "gamesMissingWinnerPredictionIdentities": [row.get("gameIdentity") for row in missing_rows],
                "requiredGameWinnerPredictionCount": len(required_rows),
                "allGamesHaveDisplayedWinnerPrediction": bool(rows and len(required_rows) == len(rows)),
                "gamesMissingDisplayedWinnerPrediction": [row.get("matchup") for row in rows if not (row.get("gameWinnerPrediction") or {}).get("displayPrediction")],
                "officialPredictionCount": sum(
                    1
                    for row in required_rows
                    if (row.get("gameWinnerPrediction") or {}).get("officialPrediction") is True
                ),
                "playablePredictionCount": len(playable_rows),
                "lowConfidencePredictionCount": len(low_confidence_rows),
                "lowConfidencePredictionTeams": [(row.get("gameWinnerPrediction") or {}).get("predictedWinner") for row in low_confidence_rows],
                "b10QualifiedCandidates": sum(1 for row in rows if row.get("b10SelectedGrade") in {"MLB_STRONG", "MLB_LEAN"}),
                "coverageComplete": coverage_complete,
                "operationalDefect": operational_defect,
                "publicAccuracyEligible": bool(locked and coverage_complete),
            },
            "policy": "Every observed MLB game has one published GAME_WINNERS prediction. The B10 leader is diagnostic-only. A published prediction becomes official only after its canonical immutable per-game lock. Doubleheaders are distinct by provider id or commence time.",
        }
        if write_file:
            os.makedirs("runtime_reports", exist_ok=True)
            with open(module.REPORT_PATH, "w", encoding="utf-8") as handle:
                json.dump(proof, handle, indent=2, default=str)
                handle.write("\n")
        return proof

    module.build = build
    module._INQSI_MLB_ALL_GAMES_COVERAGE_PATCH_APPLIED = True
    return module
