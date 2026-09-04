from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional


VERSION = "MLB-AUTO-DECISION-EVIDENCE-v1-live-history-movement"
DECISION_WEIGHTS = {
    "liveBaseballContext": 0.40,
    "historicalModelFindings": 0.30,
    "moneylineMovement": 0.20,
    "currentMarketLevel": 0.10,
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _has_data(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("ok") is False:
            return False
        if value.get("data") not in (None, {}, []):
            return True
        return any(
            key not in {"ok", "error", "meta"} and child not in (None, {}, [])
            for key, child in value.items()
        )
    return value not in (None, {}, [])


def _live_context(game: Mapping[str, Any]) -> Dict[str, Any]:
    official = game.get("official") if isinstance(game.get("official"), Mapping) else {}
    bbs = game.get("bbs") if isinstance(game.get("bbs"), Mapping) else {}
    home = official.get("home") if isinstance(official.get("home"), Mapping) else {}
    away = official.get("away") if isinstance(official.get("away"), Mapping) else {}
    probable_pitchers = bool(home.get("probablePitcher") and away.get("probablePitcher"))
    batting_lineups = _has_data(bbs.get("lineups"))
    supporting_bullpen_blocks = [
        name
        for name in ("statistics", "teamForm", "players")
        if _has_data(bbs.get(name))
    ]
    bullpen_context = (
        game.get("officialBullpenContext")
        if isinstance(game.get("officialBullpenContext"), Mapping)
        else {}
    )
    bullpen = all(
        isinstance(bullpen_context.get(side), Mapping)
        and bullpen_context[side].get("available") is True
        for side in ("home", "away")
    )
    return {
        "available": bool(official) and probable_pitchers and batting_lineups and bullpen,
        "sources": ["MLB Stats API", "Big Balls Sports Data Pro"],
        "probablePitchersAvailable": probable_pitchers,
        "battingLineupsAvailable": batting_lineups,
        "bullpenAvailabilityInputsAvailable": bullpen,
        "bullpenSource": "MLB Stats API official final boxscores",
        "bullpenSupportingBbsBlocks": supporting_bullpen_blocks,
        "weatherAvailable": _has_data(bbs.get("weather")),
        "injuriesAvailable": _has_data(
            (game.get("bbsLeagueContext") or {}).get("injuries")
        ),
        "requiredForDecision": [
            "confirmed_probable_pitchers",
            "batting_lineups",
            "bullpen_availability_and_recent_workload",
        ],
    }


def _team_probability(
    row: Mapping[str, Any],
    *,
    home: str,
    away: str,
    normalize: Callable[[Any], str],
) -> Optional[float]:
    winner = str(
        row.get("predictedWinner")
        or row.get("predicted_winner")
        or row.get("winner")
        or row.get("selection")
        or ""
    )
    probability = _f(
        row.get("probability")
        or row.get("winProbability")
        or row.get("modelProbability")
        or row.get("teamWinProbabilityPct"),
        0.0,
    )
    if probability > 1.0:
        probability /= 100.0
    if not 0.5 <= probability <= 0.95 or not winner:
        return None
    if normalize(winner) == normalize(home):
        return probability
    if normalize(winner) == normalize(away):
        return 1.0 - probability
    return None


def _historical_row(
    rows: Iterable[Mapping[str, Any]],
    game: Mapping[str, Any],
    *,
    normalize: Callable[[Any], str],
) -> Optional[Mapping[str, Any]]:
    game_pk = str(game.get("gamePk") or "")
    home = str((game.get("home") or {}).get("name") or "")
    away = str((game.get("away") or {}).get("name") or "")
    exact_pk = [
        row
        for row in rows
        if str(row.get("gamePk") or row.get("officialGamePk") or "") == game_pk
    ]
    candidates = exact_pk or [
        row
        for row in rows
        if normalize(row.get("homeTeam") or row.get("home_team")) == normalize(home)
        and normalize(row.get("awayTeam") or row.get("away_team")) == normalize(away)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _historical_signal(
    payload: Mapping[str, Any],
    game: Mapping[str, Any],
    *,
    normalize: Callable[[Any], str],
) -> Dict[str, Any]:
    rows = payload.get("winner_predictions") or payload.get("predictions") or []
    rows = [row for row in rows if isinstance(row, Mapping)]
    row = _historical_row(rows, game, normalize=normalize)
    if row is None:
        return {"available": False, "reason": "exact_historical_model_row_missing"}
    home = str((game.get("home") or {}).get("name") or "")
    away = str((game.get("away") or {}).get("name") or "")
    home_probability = _team_probability(
        row,
        home=home,
        away=away,
        normalize=normalize,
    )
    if home_probability is None:
        return {
            "available": False,
            "reason": "historical_model_direction_or_probability_invalid",
        }
    winner = str(
        row.get("predictedWinner")
        or row.get("predicted_winner")
        or row.get("winner")
        or row.get("selection")
        or ""
    )
    tags = [str(value) for value in (row.get("tags") or []) if value]
    components = (row.get("winnerStackV2") or {}).get("components") or {}
    movement = components.get("movement") or {}
    fundamentals = components.get("fundamentals") or {}
    learned = row.get("mlOptimizationRuntime") or {}
    qualified = bool(
        learned.get("championAvailable") is True
        and learned.get("directionAuthorityEnabled") is True
        and learned.get("shadowOnly") is False
    )
    return {
        "available": True,
        "source": "immutable prospective MLB model prediction",
        "advisoryOnly": not qualified,
        "qualifiedProductionChampion": qualified,
        "modelVersion": payload.get("model_version")
        or payload.get("game_winner_model")
        or payload.get("modelVersion"),
        "primaryAlgorithm": payload.get("primaryAlgorithm")
        or payload.get("primary_algorithm"),
        "predictedWinner": winner,
        "homeWinProbability": round(home_probability, 6),
        "confidenceTier": row.get("confidenceTier") or row.get("confidence"),
        "score": row.get("score"),
        "tags": tags[:40],
        "reasonCodes": [
            str(value)
            for value in (row.get("reason_codes") or row.get("reasonCodes") or [])
            if value
        ][:40],
        "learnedMovementPattern": (
            copy.deepcopy(movement) if isinstance(movement, Mapping) else {}
        ),
        "learnedFundamentalPattern": (
            copy.deepcopy(fundamentals)
            if isinstance(fundamentals, Mapping)
            else {}
        ),
        "postgameFieldsIncluded": False,
    }


def _stored_game(packet: Mapping[str, Any], game_pk: str) -> Optional[Mapping[str, Any]]:
    for row in packet.get("games") or []:
        if isinstance(row, Mapping) and str(row.get("gamePk") or "") == game_pk:
            return row
    return None


def _movement_signal(
    packets: Iterable[Mapping[str, Any]],
    game: Mapping[str, Any],
    *,
    parse: Callable[[Any], Any],
    iso: Callable[[Any], str],
    market_consensus: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    game_pk = str(game.get("gamePk") or "")
    start = parse(game.get("gameDate"))
    points: Dict[str, Dict[str, Any]] = {}
    for packet in packets:
        captured = parse(packet.get("retrievedAtUtc"))
        if captured is None or start is None or captured >= start:
            continue
        stored = _stored_game(packet, game_pk)
        event = (stored or {}).get("oddsCore") if isinstance(stored, Mapping) else None
        if not isinstance(event, Mapping):
            continue
        consensus = market_consensus(
            {
                "home": copy.deepcopy(game.get("home") or {}),
                "away": copy.deepcopy(game.get("away") or {}),
                "oddsCore": copy.deepcopy(dict(event)),
            }
        )
        if consensus.get("available") is not True:
            continue
        points[iso(captured)] = {
            "capturedAtUtc": iso(captured),
            "homeProbability": round(_f(consensus.get("homeProbability"), 0.5), 6),
            "awayProbability": round(_f(consensus.get("awayProbability"), 0.5), 6),
            "bookCount": int(_f(consensus.get("bookCount"), 0.0)),
        }
    ordered = [points[key] for key in sorted(points)]
    if len(ordered) < 2:
        return {
            "available": False,
            "observationCount": len(ordered),
            "reason": "at_least_two_distinct_pregame_moneyline_snapshots_required",
            "postStartObservationsExcluded": True,
        }
    deltas = [
        ordered[index]["homeProbability"]
        - ordered[index - 1]["homeProbability"]
        for index in range(1, len(ordered))
    ]
    reversals = sum(
        1
        for left, right in zip(deltas, deltas[1:])
        if left and right and (left > 0) != (right > 0)
    )
    opening, latest = ordered[0], ordered[-1]
    return {
        "available": True,
        "source": "immutable MLB Auto discovery packets from The Odds API",
        "observationCount": len(ordered),
        "openingCapturedAtUtc": opening["capturedAtUtc"],
        "latestCapturedAtUtc": latest["capturedAtUtc"],
        "openingHomeProbability": opening["homeProbability"],
        "latestHomeProbability": latest["homeProbability"],
        "homeProbabilityDelta": round(latest["homeProbability"] - opening["homeProbability"], 6),
        "latestBookCount": latest["bookCount"],
        "reversalCount": reversals,
        "postStartObservationsExcluded": True,
    }


def attach(
    packet: Dict[str, Any],
    *,
    historical_payload: Mapping[str, Any],
    packet_history: Iterable[Mapping[str, Any]],
    normalize: Callable[[Any], str],
    parse: Callable[[Any], Any],
    iso: Callable[[Any], str],
    market_consensus: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    packets: List[Mapping[str, Any]] = [
        row for row in packet_history if isinstance(row, Mapping)
    ]
    packets.append(packet)
    missing: List[Dict[str, Any]] = []
    qualified = True
    for game in packet.get("games") or []:
        historical = _historical_signal(historical_payload, game, normalize=normalize)
        movement = _movement_signal(
            packets,
            game,
            parse=parse,
            iso=iso,
            market_consensus=market_consensus,
        )
        evidence = {
            "version": VERSION,
            "asOfUtc": packet.get("retrievedAtUtc"),
            "weights": copy.deepcopy(DECISION_WEIGHTS),
            "liveBaseballContext": _live_context(game),
            "historicalModelFindings": historical,
            "moneylineMovement": movement,
            "currentMarketLevel": copy.deepcopy(game.get("marketConsensus") or {}),
            "immutablePregameOnly": True,
        }
        game["decisionEvidence"] = evidence
        absent = [
            name
            for name in (
                "liveBaseballContext",
                "historicalModelFindings",
                "moneylineMovement",
                "currentMarketLevel",
            )
            if (evidence.get(name) or {}).get("available") is not True
        ]
        if absent:
            missing.append({"gamePk": game.get("gamePk"), "missing": absent})
        qualified = qualified and historical.get("qualifiedProductionChampion") is True
    packet["decisionEvidenceVersion"] = VERSION
    packet["decisionEvidenceComplete"] = not missing
    packet["decisionEvidenceMissingByGame"] = missing
    packet["qualifiedHistoricalChampionForEveryGame"] = qualified and bool(packet.get("games"))
    return packet
