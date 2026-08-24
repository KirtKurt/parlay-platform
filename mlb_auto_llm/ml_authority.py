from __future__ import annotations

import json
import math
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
API_BASE_URL = os.environ.get("MLB_AUTO_ML_API_BASE_URL", "").rstrip("/")
AUTHORITY = "AWS_ML_RANKED_ENSEMBLE"
EXPECTED_MODEL = "INQSI-MLB-v5.0-ranked-winner-v15.10-active-ensemble"


def _normalize(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    aliases = {
        "oakland athletics": "athletics",
        "a s": "athletics",
        "la dodgers": "los angeles dodgers",
        "la angels": "los angeles angels",
        "ny yankees": "new york yankees",
        "ny mets": "new york mets",
        "chi cubs": "chicago cubs",
        "chi white sox": "chicago white sox",
        "tb rays": "tampa bay rays",
        "sf giants": "san francisco giants",
        "sd padres": "san diego padres",
        "az diamondbacks": "arizona diamondbacks",
    }
    return aliases.get(text, text)


def _first(mapping: Any, keys: Iterable[str]) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _team_name(value: Any) -> str:
    if isinstance(value, dict):
        nested = _first(
            value,
            (
                "name",
                "teamName",
                "team_name",
                "displayName",
                "display_name",
                "fullName",
                "full_name",
                "shortName",
                "short_name",
                "abbreviation",
            ),
        )
        if nested is not None:
            return str(nested)
        team = value.get("team")
        if isinstance(team, dict):
            return _team_name(team)
    return str(value or "")


def _row_game_pk(row: Dict[str, Any]) -> str:
    value = _first(
        row,
        (
            "gamePk",
            "game_pk",
            "officialGamePk",
            "official_game_pk",
            "sourceGamePk",
            "source_game_pk",
            "mlbGamePk",
            "mlb_game_pk",
            "gameId",
            "game_id",
        ),
    )
    if isinstance(value, dict):
        value = _first(value, ("id", "gamePk", "game_pk"))
    return str(value or "").strip()


def _row_side(row: Dict[str, Any], side: str) -> str:
    camel = side + "Team"
    snake = side + "_team"
    value = _first(
        row,
        (
            camel,
            snake,
            side,
            side + "Name",
            side + "_name",
            side + "TeamName",
            side + "_team_name",
        ),
    )
    if value is None and isinstance(row.get("game"), dict):
        value = _first(row["game"], (camel, snake, side))
    if value is None and isinstance(row.get("matchup"), dict):
        value = _first(row["matchup"], (camel, snake, side))
    return _team_name(value)


def _row_winner(row: Dict[str, Any]) -> str:
    value = _first(
        row,
        (
            "predictedWinner",
            "predicted_winner",
            "predictionTeam",
            "prediction_team",
            "predictedTeam",
            "predicted_team",
            "selectedTeam",
            "selected_team",
            "selection",
            "winner",
            "pick",
            "team",
            "recommendation",
        ),
    )
    if value is None:
        prediction = row.get("prediction")
        if isinstance(prediction, dict):
            value = _first(
                prediction,
                (
                    "winner",
                    "team",
                    "selection",
                    "predictedWinner",
                    "predicted_winner",
                    "name",
                ),
            )
        elif prediction is not None:
            value = prediction
    return _team_name(value)


def _row_probability(row: Dict[str, Any]) -> float:
    value = _first(
        row,
        (
            "probability",
            "winProbability",
            "win_probability",
            "modelProbability",
            "model_probability",
            "predictedProbability",
            "predicted_probability",
            "confidenceProbability",
            "confidence_probability",
        ),
    )
    if value is None and isinstance(row.get("prediction"), dict):
        value = _first(
            row["prediction"],
            ("probability", "winProbability", "win_probability", "confidence"),
        )
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.5
    if number > 1.0:
        number /= 100.0
    if not math.isfinite(number):
        number = 0.5
    return min(max(number, 0.5), 0.95)
def _http_json(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not API_BASE_URL:
        raise RuntimeError("MLB_AUTO_ML_API_BASE_URL_MISSING")
    query = urllib.parse.urlencode(
        {key: value for key, value in (params or {}).items() if value is not None}
    )
    url = API_BASE_URL + "/" + path.lstrip("/")
    if query:
        url += "?" + query
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "inqsi-mlb-auto-ml-authority/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("MLB_ML_API_RESPONSE_NOT_OBJECT")
    return payload


def fetch_predictions(slate: str) -> Dict[str, Any]:
    payload = _http_json(
        "/v1/mlb/game-winners",
        {"game_date_et": slate, "date": slate, "limit": 500},
    )
    if payload.get("ok") is not True:
        raise RuntimeError(
            "MLB_ML_API_NOT_READY:" + str(payload.get("error") or "unknown")
        )
    rows = payload.get("winner_predictions") or payload.get("predictions") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("MLB_ML_PREDICTIONS_EMPTY")
    return payload


def _ok_data(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("ok") is False:
            return False
        if value.get("data") not in (None, {}, []):
            return True
        return any(
            key not in {"ok", "error", "meta"} and child not in (None, {}, [])
            for key, child in value.items()
        )
    return value not in (None, {}, [])


def bbd_context_score(game: Dict[str, Any]) -> Tuple[float, List[str]]:
    bbs = game.get("bbs") if isinstance(game.get("bbs"), dict) else {}
    league = (
        game.get("bbsLeagueContext")
        if isinstance(game.get("bbsLeagueContext"), dict)
        else {}
    )
    blocks = {
        "match": bbs.get("match"),
        "detail": bbs.get("detail"),
        "statistics": bbs.get("statistics"),
        "lineups": bbs.get("lineups"),
        "teamForm": bbs.get("teamForm"),
        "players": bbs.get("players"),
        "events": bbs.get("events"),
        "weather": bbs.get("weather"),
        "standings": league.get("standings"),
        "injuries": league.get("injuries"),
    }
    present = sorted(name for name, value in blocks.items() if _ok_data(value))
    score = len(present) / len(blocks)
    return round(score, 6), present


def _candidate_rows(
    rows: List[Dict[str, Any]], game: Dict[str, Any]
) -> List[Dict[str, Any]]:
    game_pk = str(game.get("gamePk") or "")
    home = _normalize((game.get("home") or {}).get("name"))
    away = _normalize((game.get("away") or {}).get("name"))
    exact = [row for row in rows if _row_game_pk(row) == game_pk]
    if exact:
        return exact
    matches = []
    for row in rows:
        row_home = _normalize(_row_side(row, "home"))
        row_away = _normalize(_row_side(row, "away"))
        if row_home == home and row_away == away:
            matches.append(row)
    return matches


def build_card(
    packet: Dict[str, Any],
    *,
    bedrock_failure: Optional[str] = None,
) -> Dict[str, Any]:
    games = [row for row in packet.get("games") or [] if isinstance(row, dict)]
    if not games:
        raise RuntimeError("MLB_ML_CARD_HAS_NO_GAMES")
    slate = str(packet.get("slateDateEt") or "")
    payload = fetch_predictions(slate)
    rows = [row for row in payload.get("winner_predictions") or payload.get("predictions") or [] if isinstance(row, dict)]
    model_version = str(
        payload.get("model_version")
        or payload.get("game_winner_model")
        or payload.get("modelVersion")
        or EXPECTED_MODEL
    )
    primary_algorithm = str(
        payload.get("primaryAlgorithm")
        or payload.get("primary_algorithm")
        or "INQSI-MLB-RANKED-WINNER-v15.10.0-active-ensemble"
    )

    picks: List[Dict[str, Any]] = []
    used_rows: set[int] = set()
    missing: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []

    for game in games:
        candidates = _candidate_rows(rows, game)
        candidates = [row for row in candidates if id(row) not in used_rows]
        if not candidates:
            missing.append(
                {
                    "gamePk": game.get("gamePk"),
                    "homeTeam": (game.get("home") or {}).get("name"),
                    "awayTeam": (game.get("away") or {}).get("name"),
                }
            )
            continue
        if len(candidates) > 1:
            ambiguous.append(
                {
                    "gamePk": game.get("gamePk"),
                    "candidateCount": len(candidates),
                }
            )
            continue

        row = candidates[0]
        used_rows.add(id(row))
        home = str((game.get("home") or {}).get("name") or "")
        away = str((game.get("away") or {}).get("name") or "")
        raw_winner = _row_winner(row)
        winner_norm = _normalize(raw_winner)
        if winner_norm == _normalize(home):
            winner = home
            loser = away
        elif winner_norm == _normalize(away):
            winner = away
            loser = home
        else:
            raise RuntimeError(
                f"MLB_ML_WINNER_NOT_EXACT_TEAM:{game.get('gamePk')}:{raw_winner}"
            )

        base_probability = _row_probability(row)
        context_score, context_blocks = bbd_context_score(game)
        # The trained ensemble owns direction. Current BBD context is used to
        # calibrate certainty: sparse context shrinks probability toward 0.50.
        calibrated_probability = 0.5 + (base_probability - 0.5) * (
            0.75 + 0.25 * context_score
        )
        picks.append(
            {
                "gamePk": str(game.get("gamePk") or ""),
                "gameDate": game.get("gameDate"),
                "homeTeam": home,
                "awayTeam": away,
                "predictedWinner": winner,
                "predictedLoser": loser,
                "probability": round(
                    min(max(calibrated_probability, 0.5), 0.95), 6
                ),
                "baseModelProbability": round(base_probability, 6),
                "decisionAuthority": AUTHORITY,
                "mlModelVersion": model_version,
                "primaryAlgorithm": primary_algorithm,
                "confidence": str(
                    row.get("confidence")
                    or row.get("confidenceBand")
                    or "AWS_ML_MODEL"
                ),
                "rationale": (
                    "Direction comes from the deployed ranked MLB ensemble; "
                    "probability is calibrated against current three-source "
                    "context completeness."
                ),
                "sourceWeights": {
                    "awsMlRankedEnsembleDirection": 1.0,
                    "bigBallsDataContextCalibration": context_score,
                },
                "disagreements": [],
                "bbsContextScore": context_score,
                "bbsContextBlocks": context_blocks,
                "sourcePresence": {
                    "mlbStatsApi": bool(game.get("official")),
                    "theOddsApi": bool(game.get("oddsCore")),
                    "theOddsApiExpanded": bool(game.get("oddsExpanded")),
                    "bigBallsDataPro": bool(game.get("bbs")),
                },
            }
        )

    if missing:
        raise RuntimeError(
            "MLB_ML_GAME_COVERAGE_INCOMPLETE:"
            + json.dumps(missing, sort_keys=True, separators=(",", ":"))
        )
    if ambiguous:
        raise RuntimeError(
            "MLB_ML_GAME_MATCH_AMBIGUOUS:"
            + json.dumps(ambiguous, sort_keys=True, separators=(",", ":"))
        )
    if len(picks) != len(games):
        raise RuntimeError("MLB_ML_CARD_GAME_COUNT_MISMATCH")

    return {
        "version": "MLB-AUTO-LLM-v2-three-source-model-authority",
        "authority": "MLB_AUTO_MODEL_PRIMARY",
        "slateDateEt": slate,
        "publishedAtUtc": datetime.now(timezone.utc).isoformat(),
        "deadline": packet.get("deadline"),
        "targetDailyAccuracy": 0.70,
        "targetIsGoalNotGuarantee": True,
        "gameCount": len(picks),
        "llmPickCount": 0,
        "mlPickCount": len(picks),
        "fallbackPickCount": 0,
        "picks": picks,
        "sourceStatus": packet.get("sourceStatus"),
        "mlAuthority": {
            "modelVersion": model_version,
            "primaryAlgorithm": primary_algorithm,
            "endpointFamily": "aws-ml-readonly-api",
            "bedrockFailure": str(bedrock_failure or "")[:2000] or None,
        },
    }


def smoke(slate: Optional[str] = None) -> Dict[str, Any]:
    slate = slate or datetime.now(ET).date().isoformat()
    version = _http_json("/v1/mlb/model/version")
    if version.get("ok") is not True or version.get("primaryAlgorithmActive") is not True:
        raise RuntimeError(
            "MLB_ML_MODEL_NOT_ACTIVE:" + str(version.get("engine_import_error") or "")
        )
    predictions = fetch_predictions(slate)
    rows = predictions.get("winner_predictions") or predictions.get("predictions") or []
    model_version = str(
        version.get("model_version")
        or predictions.get("game_winner_model")
        or EXPECTED_MODEL
    )
    return {
        "ok": True,
        "responseNonEmpty": bool(rows),
        "modelId": model_version,
        "endpointFamily": "aws-ml-readonly-api",
        "decisionAuthority": AUTHORITY,
        "predictionCount": len(rows),
        "primaryAlgorithm": version.get("primaryAlgorithm"),
        "checkedAtUtc": datetime.now(timezone.utc).isoformat(),
    }
