from __future__ import annotations

import copy
import math
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple


VERSION = "MLB-AUTO-DECISION-EVIDENCE-v2-verified-market-movement"
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


def _event_id(event: Any) -> str:
    if not isinstance(event, Mapping):
        return ""
    return str(event.get("id") or "").strip()


def _effective_observation_time(
    packet: Mapping[str, Any],
    event: Mapping[str, Any],
    game_start: Any,
    *,
    parse: Callable[[Any], Any],
) -> Tuple[Optional[Any], bool]:
    """Resolve live versus replay provenance without minting observations.

    The second return value identifies an invalid replay marker.  Once the
    internal marker is present, its original capture time is authoritative;
    malformed, future-relative-to-packet, or post-start values fail closed.
    """

    try:
        packet_captured = parse(packet.get("retrievedAtUtc"))
    except Exception:
        packet_captured = None
    if packet_captured is None or game_start is None:
        return None, "_inqsiPregameEvidence" in event
    if "_inqsiPregameEvidence" not in event:
        return (
            (packet_captured, False)
            if packet_captured < game_start
            else (None, False)
        )

    marker = event.get("_inqsiPregameEvidence")
    if not isinstance(marker, Mapping):
        return None, True
    try:
        marker_captured = parse(marker.get("capturedAtUtc"))
    except Exception:
        marker_captured = None
    if (
        marker_captured is None
        or str(marker.get("eventId") or "").strip() != _event_id(event)
        or marker.get("capturedBeforeGameStart") is not True
        or marker_captured > packet_captured
        or marker_captured >= game_start
    ):
        return None, True
    return marker_captured, False


def _duplicate_event_ids(packet: Mapping[str, Any]) -> set[str]:
    """Find an Odds event ID reused across game rows in one snapshot."""

    assignments: Dict[str, set[str]] = {}
    for stored_game in packet.get("games") or []:
        if not isinstance(stored_game, Mapping):
            continue
        event_id = _event_id(stored_game.get("oddsCore"))
        game_pk = str(stored_game.get("gamePk") or "").strip()
        if event_id and game_pk:
            assignments.setdefault(event_id, set()).add(game_pk)
    return {
        event_id
        for event_id, game_pks in assignments.items()
        if len(game_pks) > 1
    }


def _event_matches_game(
    event: Mapping[str, Any],
    game: Mapping[str, Any],
    *,
    normalize: Callable[[Any], str],
    parse: Callable[[Any], Any],
    match_event: Optional[Callable[..., Optional[Dict[str, Any]]]],
) -> bool:
    """Revalidate immutable Odds identity before using it as movement.

    Production supplies the same matcher used by live event assignment.  The
    exact-start fallback exists for direct/library callers and is deliberately
    stricter than production matching rather than guessing at a date window.
    """

    event_id = _event_id(event)
    if not event_id:
        return False
    home = normalize((game.get("home") or {}).get("name"))
    away = normalize((game.get("away") or {}).get("name"))
    event_home = normalize(event.get("home_team") or event.get("homeTeam"))
    event_away = normalize(event.get("away_team") or event.get("awayTeam"))
    if not home or not away or event_home != home or event_away != away:
        return False

    if match_event is not None:
        try:
            matched = match_event(dict(game), [dict(event)], provider="odds")
        except Exception:
            return False
        return isinstance(matched, Mapping) and _event_id(matched) == event_id

    try:
        official_start = parse(game.get("gameDate"))
        provider_start = parse(
            event.get("commence_time") or event.get("commenceTime")
        )
    except Exception:
        return False
    return (
        official_start is not None
        and provider_start is not None
        and provider_start == official_start
    )


def _crosswalk_packet(
    packet: Mapping[str, Any],
    official_games: Iterable[Mapping[str, Any]],
    *,
    normalize: Callable[[Any], str],
    parse: Callable[[Any], Any],
    match_event: Optional[Callable[..., Optional[Dict[str, Any]]]],
    assign_odds_events: Optional[Callable[..., Mapping[str, Dict[str, Any]]]],
) -> Tuple[Dict[str, Dict[str, Any]], set[str]]:
    """Crosswalk one legacy snapshot without trusting its stored gamePk."""

    duplicates = _duplicate_event_ids(packet)
    events_by_id: Dict[str, Dict[str, Any]] = {}
    for stored_game in packet.get("games") or []:
        if not isinstance(stored_game, Mapping):
            continue
        event = stored_game.get("oddsCore")
        event_id = _event_id(event)
        if (
            event_id
            and isinstance(event, Mapping)
            and event_id not in events_by_id
        ):
            events_by_id[event_id] = copy.deepcopy(dict(event))

    games = [dict(game) for game in official_games if isinstance(game, Mapping)]
    events = list(events_by_id.values())
    assigned: Mapping[str, Any] = {}
    if assign_odds_events is not None:
        try:
            candidate = assign_odds_events(games, events, require_h2h=True)
        except Exception:
            candidate = {}
        if isinstance(candidate, Mapping):
            assigned = candidate
    else:
        # Direct-call compatibility. Production supplies the canonical
        # maximum-coverage/minimum-drift slate assigner above.
        fallback: Dict[str, Dict[str, Any]] = {}
        unused = list(events)
        for game in games:
            matched: Optional[Mapping[str, Any]] = None
            if match_event is not None:
                try:
                    value = match_event(game, unused, provider="odds")
                except Exception:
                    value = None
                matched = value if isinstance(value, Mapping) else None
            else:
                exact = [
                    event
                    for event in unused
                    if _event_matches_game(
                        event,
                        game,
                        normalize=normalize,
                        parse=parse,
                        match_event=None,
                    )
                ]
                matched = exact[0] if len(exact) == 1 else None
            event_id = _event_id(matched)
            game_pk = str(game.get("gamePk") or "")
            if event_id and game_pk and isinstance(matched, Mapping):
                fallback[game_pk] = copy.deepcopy(dict(matched))
                unused = [event for event in unused if _event_id(event) != event_id]
        assigned = fallback

    validated: Dict[str, Dict[str, Any]] = {}
    used: set[str] = set()
    games_by_pk = {
        str(game.get("gamePk") or ""): game
        for game in games
        if str(game.get("gamePk") or "")
    }
    for raw_game_pk, raw_event in assigned.items():
        game_pk = str(raw_game_pk)
        event_id = _event_id(raw_event)
        game = games_by_pk.get(game_pk)
        if (
            game is None
            or not isinstance(raw_event, Mapping)
            or not event_id
            or event_id in used
            or event_id not in events_by_id
            or not _event_matches_game(
                raw_event,
                game,
                normalize=normalize,
                parse=parse,
                match_event=match_event,
            )
        ):
            continue
        validated[game_pk] = copy.deepcopy(dict(raw_event))
        used.add(event_id)
    return validated, duplicates


def _movement_signal(
    packets: Iterable[
        Tuple[Mapping[str, Any], Mapping[str, Dict[str, Any]], set[str]]
    ],
    game: Mapping[str, Any],
    *,
    slate: str,
    parse: Callable[[Any], Any],
    iso: Callable[[Any], str],
    normalize: Callable[[Any], str],
    market_consensus: Callable[[Dict[str, Any]], Dict[str, Any]],
    match_event: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    game_pk = str(game.get("gamePk") or "")
    try:
        start = parse(game.get("gameDate"))
    except Exception:
        start = None
    points: Dict[str, Dict[str, Any]] = {}
    rejected_identity = 0
    rejected_reuse = 0
    rejected_replay_timestamp = 0
    for packet, assignments, duplicate_ids in packets:
        if str(packet.get("slateDateEt") or "") != slate:
            rejected_identity += 1
            continue
        event = assignments.get(game_pk)
        if not isinstance(event, Mapping):
            rejected_identity += 1
            rejected_reuse += len(duplicate_ids)
            continue
        if not _event_matches_game(
            event,
            game,
            normalize=normalize,
            parse=parse,
            match_event=match_event,
        ):
            rejected_identity += 1
            continue
        captured, invalid_replay_timestamp = _effective_observation_time(
            packet,
            event,
            start,
            parse=parse,
        )
        if captured is None:
            rejected_replay_timestamp += int(invalid_replay_timestamp)
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
        home_probability = _f(consensus.get("homeProbability"), float("nan"))
        away_probability = _f(consensus.get("awayProbability"), float("nan"))
        if (
            not math.isfinite(home_probability)
            or not math.isfinite(away_probability)
            or not 0.0 < home_probability < 1.0
            or not 0.0 < away_probability < 1.0
            or abs((home_probability + away_probability) - 1.0) > 0.01
        ):
            continue
        points[iso(captured)] = {
            "capturedAtUtc": iso(captured),
            "homeProbability": round(home_probability, 6),
            "awayProbability": round(away_probability, 6),
            "bookCount": int(_f(consensus.get("bookCount"), 0.0)),
        }
    ordered = [points[key] for key in sorted(points)]
    if len(ordered) < 2:
        return {
            "available": False,
            "observationCount": len(ordered),
            "reason": "at_least_two_distinct_pregame_moneyline_snapshots_required",
            "postStartObservationsExcluded": True,
            "identityRejectedObservationCount": rejected_identity,
            "reusedEventObservationCountRejected": rejected_reuse,
            "invalidReplayTimestampObservationCountRejected": (
                rejected_replay_timestamp
            ),
            "storedEventIdentityRevalidated": True,
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
        "identityRejectedObservationCount": rejected_identity,
        "reusedEventObservationCountRejected": rejected_reuse,
        "invalidReplayTimestampObservationCountRejected": (
            rejected_replay_timestamp
        ),
        "storedEventIdentityRevalidated": True,
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
    match_event: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
    assign_odds_events: Optional[
        Callable[..., Mapping[str, Dict[str, Any]]]
    ] = None,
) -> Dict[str, Any]:
    packets: List[Mapping[str, Any]] = [
        row for row in packet_history if isinstance(row, Mapping)
    ]
    packets.append(packet)
    slate = str(packet.get("slateDateEt") or "")
    official_games: List[Mapping[str, Any]] = [
        game for game in packet.get("games") or [] if isinstance(game, Mapping)
    ]
    crosswalked_packets = [
        (
            stored_packet,
            *_crosswalk_packet(
                stored_packet,
                official_games,
                normalize=normalize,
                parse=parse,
                match_event=match_event,
                assign_odds_events=assign_odds_events,
            ),
        )
        for stored_packet in packets
    ]
    missing: List[Dict[str, Any]] = []
    qualified = True
    for game in packet.get("games") or []:
        historical = _historical_signal(historical_payload, game, normalize=normalize)
        movement = _movement_signal(
            crosswalked_packets,
            game,
            slate=slate,
            parse=parse,
            iso=iso,
            normalize=normalize,
            market_consensus=market_consensus,
            match_event=match_event,
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
