from __future__ import annotations

import copy
import json
import math
import urllib.error
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional


VERSION = "MLB-AUTO-PREGAME-ODDS-REPLAY-v2-exact-h2h-identity"


def _provider_probe_fallback_allowed(error: Exception) -> bool:
    if isinstance(
        error,
        (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError),
    ):
        return True
    return isinstance(error, RuntimeError) and str(error).startswith(
        (
            "BBS_",
            "ODDS_",
            "PROVIDER_INTEGRATION_INCOMPLETE:",
            "THREE_SOURCE_GAME_COVERAGE_INCOMPLETE:",
        )
    )


def _event_id(event: Any) -> str:
    if not isinstance(event, Mapping):
        return ""
    return str(event.get("id") or "").strip()


def _effective_capture_time(
    base: Any,
    stored_packet: Mapping[str, Any],
    event: Mapping[str, Any],
    game_start: Any,
) -> Any:
    """Return the original provider-observation time for immutable evidence.

    A packet persisted after replay is not a new Odds observation.  Its nested
    replay marker carries the original capture time and must remain bounded by
    both the enclosing packet time and first pitch.  A present but malformed
    marker fails closed instead of falling back to the later packet timestamp.
    """

    try:
        packet_captured = base._parse(stored_packet.get("retrievedAtUtc"))
    except Exception:
        packet_captured = None
    if packet_captured is None or game_start is None:
        return None
    if "_inqsiPregameEvidence" not in event:
        return packet_captured if packet_captured < game_start else None

    marker = event.get("_inqsiPregameEvidence")
    if not isinstance(marker, Mapping):
        return None
    try:
        marker_captured = base._parse(marker.get("capturedAtUtc"))
    except Exception:
        marker_captured = None
    if (
        marker_captured is None
        or str(marker.get("eventId") or "").strip() != _event_id(event)
        or marker.get("capturedBeforeGameStart") is not True
        or marker_captured > packet_captured
        or marker_captured >= game_start
    ):
        return None
    return marker_captured


def _valid_decimal_price(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 1.0


def _event_has_real_core_odds(
    event: Any,
    *,
    home: Any = None,
    away: Any = None,
    normalize: Optional[Callable[[Any], str]] = None,
) -> bool:
    """Require an exact two-team MLB moneyline from one bookmaker.

    Spreads, totals, a single priced outcome, or prices for teams other than
    the event's ordered home/away pair are not immutable moneyline evidence.
    When the official teams are supplied, provider identity must also agree
    with that exact ordered matchup.
    """

    if not isinstance(event, Mapping) or not _event_id(event):
        return False

    canonical = normalize or (lambda value: str(value or "").strip().casefold())
    event_home = canonical(event.get("home_team") or event.get("homeTeam"))
    event_away = canonical(event.get("away_team") or event.get("awayTeam"))
    expected_home = canonical(home) if home is not None else event_home
    expected_away = canonical(away) if away is not None else event_away
    if (
        not event_home
        or not event_away
        or event_home == event_away
        or event_home != expected_home
        or event_away != expected_away
    ):
        return False

    for bookmaker in event.get("bookmakers") or []:
        if not isinstance(bookmaker, Mapping):
            continue
        for market in bookmaker.get("markets") or []:
            if not isinstance(market, Mapping) or market.get("key") != "h2h":
                continue
            outcomes = market.get("outcomes") or []
            if not isinstance(outcomes, list) or len(outcomes) != 2:
                continue
            priced_teams = {
                canonical(row.get("name"))
                for row in outcomes
                if isinstance(row, Mapping) and _valid_decimal_price(row.get("price"))
            }
            if priced_teams == {expected_home, expected_away}:
                return True
    return False


def _event_has_exact_two_sided_h2h(
    base: Any,
    event: Any,
    *,
    home: Any,
    away: Any,
) -> bool:
    """Use the production Odds validator; retain a strict isolated fallback."""

    validator = getattr(base, "_odds_has_exact_h2h", None)
    if callable(validator):
        try:
            return bool(validator(event, home, away))
        except Exception:
            return False
    normalize = getattr(base, "_normalize", None)
    return _event_has_real_core_odds(
        event,
        home=home,
        away=away,
        normalize=normalize if callable(normalize) else None,
    )


def _duplicate_event_ids(packet: Mapping[str, Any]) -> set[str]:
    """Find provider IDs attached to multiple game rows in one snapshot."""

    assignments: Dict[str, set[str]] = {}
    for game in packet.get("games") or []:
        if not isinstance(game, Mapping):
            continue
        event_id = _event_id(game.get("oddsCore"))
        game_pk = str(game.get("gamePk") or "").strip()
        if event_id and game_pk:
            assignments.setdefault(event_id, set()).add(game_pk)
    return {
        event_id
        for event_id, game_pks in assignments.items()
        if len(game_pks) > 1
    }


def _crosswalk_packet(
    base: Any,
    match_event: Callable[..., Optional[Dict[str, Any]]],
    official_games: Iterable[Dict[str, Any]],
    stored_packet: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Reassign legacy events from identity, never from their stored gamePk."""

    duplicates = _duplicate_event_ids(stored_packet)
    events_by_id: Dict[str, Dict[str, Any]] = {}
    for stored_game in stored_packet.get("games") or []:
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

    games = [game for game in official_games if isinstance(game, dict)]
    events = list(events_by_id.values())
    assigner = getattr(base, "_assign_odds_events", None)
    if callable(assigner):
        try:
            assigned = assigner(games, events, require_h2h=True)
        except Exception:
            return {}
        if not isinstance(assigned, Mapping):
            return {}
        return {
            str(game_pk): copy.deepcopy(dict(event))
            for game_pk, event in assigned.items()
            if isinstance(event, Mapping) and _event_id(event) in events_by_id
        }

    # Isolated-test/backwards-compatible path. Production always exposes the
    # slate-level assigner; without it, ambiguity fails closed by consuming a
    # provider ID at most once.
    assigned_fallback: Dict[str, Dict[str, Any]] = {}
    used: set[str] = set()
    for game in games:
        matched = match_event(game, events, provider="odds")
        event_id = _event_id(matched)
        if not event_id or event_id in used or not isinstance(matched, Mapping):
            continue
        home = (game.get("home") or {}).get("name")
        away = (game.get("away") or {}).get("name")
        if not _event_has_exact_two_sided_h2h(
            base,
            matched,
            home=home,
            away=away,
        ):
            continue
        game_pk = str(game.get("gamePk") or "")
        if game_pk:
            assigned_fallback[game_pk] = copy.deepcopy(dict(matched))
            used.add(event_id)
    return assigned_fallback


def _packet_history(base: Any, slate: str, *, limit: int = 300) -> List[Dict[str, Any]]:
    table = getattr(base, "TABLE", None)
    if table is None:
        return []
    items: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {
        "KeyConditionExpression": "PK = :pk",
        "ExpressionAttributeValues": {":pk": f"PACKET#{slate}"},
        "ScanIndexForward": False,
        "ConsistentRead": True,
        "Limit": min(max(limit, 1), 500),
    }
    for _ in range(5):
        response = table.query(**kwargs)
        for item in response.get("Items") or []:
            data = item.get("data") if isinstance(item, dict) else None
            if isinstance(data, dict):
                items.append(base._plain(data))
            if len(items) >= limit:
                return items[:limit]
        key = response.get("LastEvaluatedKey")
        if not key:
            break
        kwargs["ExclusiveStartKey"] = key
    return items[:limit]


def _candidate(
    base: Any,
    match_event: Callable[..., Optional[Dict[str, Any]]],
    official_game: Dict[str, Any],
    stored_packet: Dict[str, Any],
    stored_event: Any,
    *,
    slate: str,
) -> Optional[Dict[str, Any]]:
    if str(stored_packet.get("slateDateEt") or "") != slate:
        return None
    start = base._parse(official_game.get("gameDate"))
    event = stored_event
    event_id = _event_id(event)
    if not event_id or not isinstance(event, Mapping):
        return None
    captured = _effective_capture_time(base, stored_packet, event, start)
    if captured is None:
        return None
    home = (official_game.get("home") or {}).get("name")
    away = (official_game.get("away") or {}).get("name")
    if not _event_has_exact_two_sided_h2h(
        base,
        event,
        home=home,
        away=away,
    ):
        return None
    matched = match_event(official_game, [event], provider="odds")
    if not isinstance(matched, Mapping) or _event_id(matched) != event_id:
        return None
    return {
        "capturedAtUtc": base._iso(captured),
        "event": copy.deepcopy(event),
        "packetRetrievedAtUtc": stored_packet.get("retrievedAtUtc"),
    }


def recover_persisted_pregame_odds(
    base: Any,
    production: Any,
    match_event: Callable[..., Optional[Dict[str, Any]]],
    packet: Dict[str, Any],
) -> Dict[str, Any]:
    slate = str(packet.get("slateDateEt") or "")
    history = [
        row
        for row in (_packet_history(base, slate) if slate else [])
        if str(row.get("slateDateEt") or "") == slate
    ]
    official_games = [
        (
            game.get("official")
            if isinstance(game.get("official"), dict)
            else game
        )
        for game in packet.get("games") or []
        if isinstance(game, dict)
    ]
    history_assignments = {
        id(stored_packet): _crosswalk_packet(
            base,
            match_event,
            official_games,
            stored_packet,
        )
        for stored_packet in history
    }
    current_assignments = _crosswalk_packet(
        base,
        match_event,
        official_games,
        packet,
    )
    duplicate_ids_by_packet = {
        id(stored_packet): _duplicate_event_ids(stored_packet)
        for stored_packet in [*history, packet]
    }
    rejected_duplicate_ids = {
        event_id
        for values in duplicate_ids_by_packet.values()
        for event_id in values
    }
    recovered: List[str] = []
    evidence: Dict[str, Dict[str, Any]] = {}
    rejected_current: List[str] = []

    for game in packet.get("games") or []:
        if not isinstance(game, dict):
            continue
        official = game.get("official") if isinstance(game.get("official"), dict) else game
        current_event = game.get("oddsCore")
        current_event_id = _event_id(current_event)
        game_pk = str(official.get("gamePk") or game.get("gamePk") or "")
        current_valid = False
        if (
            current_event_id
            and _event_id(current_assignments.get(game_pk)) == current_event_id
        ):
            home = (official.get("home") or {}).get("name")
            away = (official.get("away") or {}).get("name")
            current_valid = _event_has_exact_two_sided_h2h(
                base,
                current_event,
                home=home,
                away=away,
            )
            if current_valid:
                matched = match_event(official, [current_event], provider="odds")
                current_valid = (
                    isinstance(matched, Mapping)
                    and _event_id(matched) == current_event_id
                )
            if current_valid and "_inqsiPregameEvidence" in current_event:
                current_valid = _effective_capture_time(
                    base,
                    packet,
                    current_event,
                    base._parse(official.get("gameDate")),
                ) is not None
        if current_valid:
            continue
        if current_event is not None:
            rejected_current.append(str(game.get("gamePk") or ""))
            game["oddsCore"] = None
            game["marketConsensus"] = base._market_consensus(game)
        candidates = [
            value
            for stored_packet in history
            if (
                value := _candidate(
                    base,
                    match_event,
                    official,
                    stored_packet,
                    history_assignments[id(stored_packet)].get(
                        str(official.get("gamePk") or "")
                    ),
                    slate=slate,
                )
            )
            is not None
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda row: str(row.get("capturedAtUtc") or ""), reverse=True)
        selected = candidates[0]
        game_pk = str(game.get("gamePk") or "")
        event = copy.deepcopy(selected["event"])
        marker = {
            "version": VERSION,
            "source": "The Odds API",
            "capturedAtUtc": selected["capturedAtUtc"],
            "capturedBeforeGameStart": True,
            "replayedAfterLiveEndpointRemoval": True,
            "eventId": event.get("id"),
            "orderedTeamIdentityValidated": True,
            "exactTwoSidedH2hValidated": True,
            "eventIdNotReusedAcrossGames": True,
        }
        event["_inqsiPregameEvidence"] = copy.deepcopy(marker)
        game["oddsCore"] = event
        game["oddsCoreEvidence"] = copy.deepcopy(marker)
        game["marketConsensus"] = base._market_consensus(game)
        recovered.append(game_pk)
        evidence[game_pk] = marker

    packet = production._apply_source_coverage(packet)
    status = packet.setdefault("sourceStatus", {}).setdefault("theOddsApi", {})
    status.update(
        {
            "pregameReplayVersion": VERSION,
            "persistedPregameRecoveryCount": len(recovered),
            "persistedPregameRecoveredGamePks": sorted(recovered),
            "persistedPregameEvidenceByGamePk": evidence,
            "rejectedCurrentOddsGamePks": sorted(
                value for value in rejected_current if value
            ),
            "reusedHistoricalEventIdsRejected": sorted(rejected_duplicate_ids),
            "postStartOddsFabricationAllowed": False,
            "replayPolicy": (
                "Only an actual stored The Odds API event with one exact two-sided "
                "h2h market, ordered official teams/start time, a non-reused event ID, "
                "and a packet timestamp strictly before first pitch may be replayed."
            ),
        }
    )
    packet["pregameOddsReplayApplied"] = bool(recovered)
    return packet


def install(
    base: Any,
    production: Any,
    strict_bedrock: Any,
    *,
    match_event: Callable[..., Optional[Dict[str, Any]]],
) -> None:
    if getattr(production, "_pregame_odds_replay_installed", False):
        return
    original_assemble = production._assemble_with_full_bbd
    original_late_guard = strict_bedrock._late_guard

    def assemble(slate: str, *, expanded: bool) -> Dict[str, Any]:
        packet = original_assemble(slate, expanded=expanded)
        return recover_persisted_pregame_odds(
            base, production, match_event, packet
        )

    def late_guard(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        now = base._now()
        slate = str(
            payload.get("slate_date")
            or now.astimezone(base.ET).date().isoformat()
        )
        if payload.get("mode") != "deployment_provider_smoke":
            return original_late_guard(payload)
        if base._get(f"CARD#{slate}", "FINAL"):
            return original_late_guard(payload)
        schedule = base._official_schedule(slate)
        if not schedule.get("games"):
            return original_late_guard(payload)
        deadline = base._deadline(schedule)
        deadline_dt = base._parse(deadline.get("publishDeadlineUtc"))
        if deadline_dt is None or now <= deadline_dt:
            return original_late_guard(payload)

        try:
            packet = assemble(slate, expanded=False)
            source_status = packet.get("sourceStatus") or {}
            provider_integration_complete = all(
                (source_status.get(name) or {}).get("integrationOk") is True
                for name in ("mlbStatsApi", "theOddsApi", "bigBallsDataPro")
            )
            line_readiness_complete = (
                (source_status.get("theOddsApi") or {}).get(
                    "lineReadinessComplete"
                )
                is True
            )
            publication_ready = (
                packet.get("threeSourceCoverageComplete") is True
            )
            result = {
                "ok": provider_integration_complete,
                "status": "COLLECTING",
                "requestedSlateDateEt": slate,
                "slateDateEt": slate,
                "deadline": deadline,
                "sourceStatus": source_status,
                "threeSourceCoverageComplete": packet.get("threeSourceCoverageComplete"),
                "providerIntegrationComplete": provider_integration_complete,
                "lineReadinessComplete": line_readiness_complete,
                "publicationReady": publication_ready,
                "latePublicationPrevented": True,
                "providerProbeUsedFutureSlate": False,
                "providerProbeUsedPersistedPregameEvidence": bool(
                    packet.get("pregameOddsReplayApplied")
                ),
                "postStartPredictionCreationAllowed": False,
                "postStartOddsFabricationAllowed": False,
            }
            production._validate_deployment_smoke(result)
            return result
        except Exception as current_slate_error:
            # A live provider may no longer serve the completed/current slate,
            # even though its immutable pregame Odds snapshot is recoverable.
            # The strict guard already has a read-only future-slate probe for
            # this exact post-cutoff case. Delegate to it instead of weakening
            # any live-provider response contract or manufacturing evidence.
            if not _provider_probe_fallback_allowed(current_slate_error):
                raise
            fallback = original_late_guard(payload)
            if isinstance(fallback, dict):
                fallback["currentSlateProbeFallback"] = True
                fallback["currentSlateProbeErrorType"] = type(
                    current_slate_error
                ).__name__
                fallback["providerProbeUsedPersistedPregameEvidence"] = False
                fallback["postStartPredictionCreationAllowed"] = False
                fallback["postStartOddsFabricationAllowed"] = False
            return fallback

    production._assemble_with_full_bbd = assemble
    production._pregame_odds_replay_installed = True
    base._assemble = assemble
    strict_bedrock._late_guard = late_guard
