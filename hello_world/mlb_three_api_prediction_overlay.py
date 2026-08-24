from __future__ import annotations

"""Three-source ensemble applied before an MLB prediction row is persisted.

The existing trained MLB model remains the largest single component. Market
consensus from The Odds API and the Bedrock analyst over official MLB and BBD
Pro evidence both materially influence the final home-win probability. Every
row remains a winner/loser prediction; there is no PASS path.
"""

import copy
import functools
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import mlb_bbd_pro_context as bbd
import mlb_three_api_llm_analyst as llm


VERSION = "MLB-THREE-API-PREDICTION-ENSEMBLE-v1"
ENABLED_ENV = "MLB_THREE_API_ENABLED"
STRICT_ENV = "MLB_THREE_API_REQUIRE_ALL_SOURCES"
MIN_PROBABILITY = 0.5001
MAX_PROBABILITY = 0.99


class ThreeApiPredictionError(RuntimeError):
    pass


def enabled() -> bool:
    return str(os.environ.get(ENABLED_ENV, "false")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def strict() -> bool:
    return str(os.environ.get(STRICT_ENV, "true")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _team(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _team_key(value: Any) -> str:
    return _team(value).lower()


def _first(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _home_away(row: Mapping[str, Any]) -> Tuple[str, str]:
    home = _team(_first(row, ("home_team", "homeTeam", "home")))
    away = _team(_first(row, ("away_team", "awayTeam", "away")))
    if not home or not away or _team_key(home) == _team_key(away):
        nested = _first(row, ("game", "event", "match", "officialGame", "official_game"))
        if isinstance(nested, Mapping):
            home = _team(_first(nested, ("home_team", "homeTeam", "home")))
            away = _team(_first(nested, ("away_team", "awayTeam", "away")))
    return home, away


def _game_id(row: Mapping[str, Any]) -> str:
    value = _first(
        row,
        (
            "official_game_pk", "officialGamePk", "official_game_id", "officialGameId",
            "provider_event_id", "providerEventId", "provider_game_id", "providerGameId",
            "game_id", "gameId", "id", "gameIdentity",
        ),
    )
    return str(value or "")


def _as_probability(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return number if 0.0 <= number <= 1.0 else None


def _implied_probability(price: Any) -> Optional[float]:
    if isinstance(price, bool) or price in (None, ""):
        return None
    try:
        value = float(price)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    # The Odds API may be configured for American or decimal output. American
    # prices are normally <= -100 or >= +100; decimal prices are > 1.
    if value <= -100:
        return (-value) / ((-value) + 100.0)
    if value >= 100:
        return 100.0 / (value + 100.0)
    if 1.0 < value <= 50.0:
        return 1.0 / value
    if 0.0 < value < 1.0:
        return value
    return None


def _bookmakers(row: Mapping[str, Any]) -> List[Dict[str, Any]]:
    values: List[Any] = []
    for container in (
        row,
        row.get("advancedContext") if isinstance(row.get("advancedContext"), Mapping) else {},
        row.get("advanced_context") if isinstance(row.get("advanced_context"), Mapping) else {},
        row.get("marketContext") if isinstance(row.get("marketContext"), Mapping) else {},
        row.get("market_context") if isinstance(row.get("market_context"), Mapping) else {},
    ):
        if isinstance(container, Mapping):
            candidate = container.get("bookmakers")
            if isinstance(candidate, list):
                values.extend(candidate)
    return [copy.deepcopy(value) for value in values if isinstance(value, dict)]


def market_home_probability(row: Mapping[str, Any], home: str, away: str) -> Optional[float]:
    home_key, away_key = _team_key(home), _team_key(away)
    probabilities: List[float] = []
    for bookmaker in _bookmakers(row):
        markets = bookmaker.get("markets") if isinstance(bookmaker.get("markets"), list) else []
        for market in markets:
            if not isinstance(market, Mapping):
                continue
            key = str(market.get("key") or market.get("market_key") or "").lower()
            if key not in {"h2h", "moneyline", "game_moneyline", "match_winner"}:
                continue
            outcomes = market.get("outcomes") if isinstance(market.get("outcomes"), list) else []
            sides: Dict[str, float] = {}
            for outcome in outcomes:
                if not isinstance(outcome, Mapping):
                    continue
                name = _team_key(outcome.get("name") or outcome.get("team") or outcome.get("participant"))
                implied = _implied_probability(outcome.get("price") or outcome.get("odds"))
                if implied is not None and name in {home_key, away_key}:
                    sides[name] = implied
            if set(sides) == {home_key, away_key}:
                total = sides[home_key] + sides[away_key]
                if total > 0:
                    probabilities.append(sides[home_key] / total)
    if not probabilities:
        return None
    probabilities.sort()
    # A trimmed mean is more stable against one stale or malformed book.
    trimmed = probabilities[1:-1] if len(probabilities) >= 5 else probabilities
    return sum(trimmed) / len(trimmed)


def original_model_home_probability(row: Mapping[str, Any], home: str, away: str) -> Optional[float]:
    winner = _team(
        _first(
            row,
            (
                "predictedWinner", "predicted_winner", "prediction", "winnerPrediction",
                "selectedTeam", "selected_team", "pick", "winner",
            ),
        )
    )
    probability = _as_probability(
        _first(
            row,
            (
                "winProbability", "win_probability", "probability", "predictedProbability",
                "predicted_probability", "modelProbability", "model_probability", "confidenceScore",
            ),
        )
    )
    if probability is None:
        # Some model rows carry explicit home/away probabilities.
        probability = _as_probability(
            _first(row, ("homeWinProbability", "home_win_probability", "probHome", "prob_home"))
        )
        return probability
    if not winner:
        return probability
    if _team_key(winner) == _team_key(home):
        return probability
    if _team_key(winner) == _team_key(away):
        return 1.0 - probability
    return None


def _context_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    for key in (
        "advancedContext", "advanced_context", "fundamentalsSnapshotV2",
        "fundamentals_snapshot_v2", "context", "features", "featureContext",
    ):
        value = row.get(key)
        if isinstance(value, dict):
            context = copy.deepcopy(value)
            break
    else:
        context = {}
    for key in (
        "official_game_pk", "officialGamePk", "schedule_authority", "scheduleAuthority",
        "bookmakers", "market_context", "marketContext", "model", "modelPrediction",
    ):
        if row.get(key) not in (None, "") and key not in context:
            context[key] = copy.deepcopy(row.get(key))
    return context


def _game_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    nested = _first(row, ("game", "event", "match", "officialGame", "official_game"))
    game = copy.deepcopy(nested) if isinstance(nested, dict) else {}
    for key in (
        "official_game_pk", "officialGamePk", "official_game_id", "officialGameId",
        "provider_event_id", "providerEventId", "game_id", "gameId", "id",
        "official_commence_time", "officialCommenceTime", "commence_time", "commenceTime",
        "home_team", "homeTeam", "away_team", "awayTeam", "bookmakers",
        "schedule_authority", "scheduleAuthority", "official_status", "officialStatus",
    ):
        if row.get(key) not in (None, ""):
            game[key] = copy.deepcopy(row.get(key))
    return game


def _source_ready(game: Mapping[str, Any], context: Mapping[str, Any], signal: Mapping[str, Any]) -> Dict[str, bool]:
    official = bool(
        _first(game, ("official_game_pk", "officialGamePk", "official_game_id", "officialGameId"))
        and _first(game, ("official_commence_time", "officialCommenceTime", "commence_time", "commenceTime"))
    )
    odds = bool(_bookmakers({**dict(game), **dict(context)}))
    bbd_context = context.get("big_balls_data_pro") if isinstance(context.get("big_balls_data_pro"), Mapping) else {}
    bbd_ready = (
        str(bbd_context.get("sourceStatus") or "").upper() in {"CONNECTED", "PARTIAL"}
        and int(bbd_context.get("operationsSucceeded") or 0) > 0
    )
    llm_ready = str(signal.get("status") or "").upper() == "CONNECTED"
    return {
        "mlbStatsApi": official,
        "theOddsApi": odds,
        "bigBallsDataPro": bbd_ready,
        "bedrockLlm": llm_ready,
    }


def _weight_probability(components: Iterable[Tuple[str, Optional[float], float]]) -> Tuple[float, List[Dict[str, Any]]]:
    used: List[Dict[str, Any]] = []
    numerator = 0.0
    denominator = 0.0
    for name, probability, weight in components:
        if probability is None or not 0.0 <= probability <= 1.0 or weight <= 0:
            continue
        numerator += probability * weight
        denominator += weight
        used.append({"component": name, "homeWinProbability": probability, "weight": weight})
    if denominator <= 0:
        raise ThreeApiPredictionError("NO_VALID_PREDICTION_COMPONENTS")
    return numerator / denominator, used


def apply_prediction_overlay(row: Mapping[str, Any], *, as_of_utc: Optional[str] = None) -> Dict[str, Any]:
    current = copy.deepcopy(dict(row))
    home, away = _home_away(current)
    if not home or not away:
        return current
    game = _game_from_row(current)
    context = _context_from_row(current)
    as_of = as_of_utc or str(
        _first(
            current,
            (
                "predictionPersistedAtUtc", "lockedAtUtc", "asOfUtc", "observedAtUtc",
                "createdAtUtc", "generatedAtUtc",
            ),
        )
        or _now_iso()
    )

    bbd_context = context.get("big_balls_data_pro")
    if not isinstance(bbd_context, dict) or str(bbd_context.get("sourceStatus") or "") not in {"CONNECTED", "PARTIAL"}:
        bbd_context = bbd.collect_game_context(game, as_of_utc=as_of)
        context = bbd.merge_into_advanced_context(context, bbd_context)
    signal = context.get("three_api_llm_signal")
    if not isinstance(signal, dict) or signal.get("status") != "CONNECTED":
        signal = llm.analyze_game(game, context, as_of_utc=as_of)
        context["three_api_llm_signal"] = signal

    ready = _source_ready(game, context, signal)
    if strict() and not all(ready.values()):
        missing = sorted(name for name, value in ready.items() if not value)
        raise ThreeApiPredictionError("THREE_API_SOURCE_NOT_READY:" + ",".join(missing))

    original_home = original_model_home_probability(current, home, away)
    market_home = market_home_probability({**current, **context}, home, away)
    llm_home: Optional[float] = None
    if signal.get("status") == "CONNECTED":
        llm_probability = _as_probability(signal.get("winProbability"))
        llm_winner = _team(signal.get("predictedWinner"))
        if llm_probability is not None and _team_key(llm_winner) == _team_key(home):
            llm_home = llm_probability
        elif llm_probability is not None and _team_key(llm_winner) == _team_key(away):
            llm_home = 1.0 - llm_probability

    completeness = _as_probability(signal.get("sourceCompleteness")) or 0.0
    llm_weight = 0.20 + 0.10 * completeness if llm_home is not None else 0.0
    market_weight = 0.25 if market_home is not None else 0.0
    model_weight = 0.55 if original_home is not None else 0.0
    home_probability, components = _weight_probability(
        (
            ("existingAutonomousMLModel", original_home, model_weight),
            ("theOddsApiNoVigMarketConsensus", market_home, market_weight),
            ("bedrockThreeSourceAnalyst", llm_home, llm_weight),
        )
    )
    home_probability = min(MAX_PROBABILITY, max(1.0 - MAX_PROBABILITY, home_probability))
    winner = home if home_probability >= 0.5 else away
    loser = away if winner == home else home
    winner_probability = home_probability if winner == home else 1.0 - home_probability
    winner_probability = min(MAX_PROBABILITY, max(MIN_PROBABILITY, winner_probability))

    original = {
        key: copy.deepcopy(current.get(key))
        for key in (
            "predictedWinner", "predicted_winner", "predictedLoser", "predicted_loser",
            "winProbability", "win_probability", "probability", "modelProbability",
        )
        if key in current
    }
    current["preThreeApiPrediction"] = original
    current["predictedWinner"] = winner
    current["predicted_winner"] = winner
    current["predictedLoser"] = loser
    current["predicted_loser"] = loser
    current["winProbability"] = winner_probability
    current["win_probability"] = winner_probability
    current["threeApiDecision"] = {
        "version": VERSION,
        "asOfUtc": as_of,
        "gameId": _game_id(game) or _game_id(current),
        "homeTeam": home,
        "awayTeam": away,
        "predictedWinner": winner,
        "predictedLoser": loser,
        "winnerProbability": winner_probability,
        "homeWinProbability": home_probability,
        "components": components,
        "sourceReady": ready,
        "llmModelId": signal.get("modelId"),
        "llmEvidenceFingerprint": signal.get("evidenceFingerprint"),
        "bbdContextFingerprint": bbd_context.get("contextFingerprint") if isinstance(bbd_context, dict) else None,
        "noPass": True,
        "dailyAccuracyGoal": 0.70,
        "accuracyGuarantee": False,
    }
    # Preserve the enriched source envelope for the immutable frozen vector and
    # later audit. Use the same key that was already present when possible.
    target_key = next(
        (
            key for key in (
                "advancedContext", "advanced_context", "fundamentalsSnapshotV2",
                "fundamentals_snapshot_v2", "context",
            ) if isinstance(current.get(key), dict)
        ),
        "advancedContext",
    )
    current[target_key] = context
    return current


def _is_prediction_row(value: Mapping[str, Any]) -> bool:
    home, away = _home_away(value)
    if not home or not away:
        return False
    prediction_keys = {
        "predictedWinner", "predicted_winner", "predictedSide", "predicted_side",
        "winProbability", "win_probability", "modelProbability", "model_probability",
        "selectedTeam", "selected_team", "pick",
    }
    return bool(prediction_keys.intersection(value))


def apply_to_value(value: Any, *, as_of_utc: Optional[str] = None, depth: int = 0) -> Any:
    if depth > 8:
        return value
    if isinstance(value, dict):
        if _is_prediction_row(value):
            return apply_prediction_overlay(value, as_of_utc=as_of_utc)
        current = copy.deepcopy(value)
        for key, item in list(current.items()):
            if isinstance(item, (dict, list, tuple)):
                current[key] = apply_to_value(item, as_of_utc=as_of_utc, depth=depth + 1)
        return current
    if isinstance(value, list):
        return [apply_to_value(item, as_of_utc=as_of_utc, depth=depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(apply_to_value(item, as_of_utc=as_of_utc, depth=depth + 1) for item in value)
    return value


def install_named_overlays(
    namespace: MutableMapping[str, Any],
    module_name: str,
    function_names: Sequence[str],
) -> List[str]:
    if not enabled():
        return []
    installed: List[str] = []
    for name in function_names:
        original = namespace.get(name)
        if not callable(original) or getattr(original, "__mlb_three_api_prediction_overlay__", False):
            continue

        @functools.wraps(original)
        def wrapped(*args: Any, __original: Callable[..., Any] = original, **kwargs: Any) -> Any:
            result = __original(*args, **kwargs)
            as_of = _first(
                kwargs,
                (
                    "as_of_utc", "asOfUtc", "prediction_persisted_at_utc",
                    "predictionPersistedAtUtc", "locked_at_utc", "lockedAtUtc",
                ),
            )
            return apply_to_value(result, as_of_utc=str(as_of) if as_of else None)

        wrapped.__mlb_three_api_prediction_overlay__ = True
        wrapped.__mlb_three_api_module__ = module_name
        namespace[name] = wrapped
        installed.append(name)
    return installed
