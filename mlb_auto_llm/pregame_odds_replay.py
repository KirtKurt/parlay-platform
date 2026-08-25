from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional


VERSION = "MLB-AUTO-PREGAME-ODDS-REPLAY-v1-immutable-evidence-only"
CORE_MARKETS = {"h2h", "spreads", "totals"}


def _event_has_real_core_odds(event: Any) -> bool:
    if not isinstance(event, dict) or not event.get("id"):
        return False
    for bookmaker in event.get("bookmakers") or []:
        if not isinstance(bookmaker, dict):
            continue
        for market in bookmaker.get("markets") or []:
            if not isinstance(market, dict) or market.get("key") not in CORE_MARKETS:
                continue
            if any(isinstance(row, dict) and row.get("price") is not None for row in market.get("outcomes") or []):
                return True
    return False


def _packet_history(base: Any, slate: str, *, limit: int = 300) -> List[Dict[str, Any]]:
    table = getattr(base, "TABLE", None)
    if table is None:
        return []
    items: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {
        "KeyConditionExpression": "PK = :pk",
        "ExpressionAttributeValues": {":pk": f"PACKET#{slate}"},
        "ScanIndexForward": False,
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


def _stored_game(packet: Mapping[str, Any], game_pk: str) -> Optional[Dict[str, Any]]:
    for row in packet.get("games") or []:
        if isinstance(row, dict) and str(row.get("gamePk") or "") == game_pk:
            return row
    return None


def _candidate(
    base: Any,
    match_event: Callable[..., Optional[Dict[str, Any]]],
    official_game: Dict[str, Any],
    stored_packet: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    captured = base._parse(stored_packet.get("retrievedAtUtc"))
    start = base._parse(official_game.get("gameDate"))
    if captured is None or start is None or captured >= start:
        return None
    game_pk = str(official_game.get("gamePk") or "")
    stored = _stored_game(stored_packet, game_pk)
    event = (stored or {}).get("oddsCore") if isinstance(stored, dict) else None
    if not _event_has_real_core_odds(event):
        return None
    if match_event(official_game, [event], provider="odds") is None:
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
    history = _packet_history(base, slate) if slate else []
    recovered: List[str] = []
    evidence: Dict[str, Dict[str, Any]] = {}

    for game in packet.get("games") or []:
        if not isinstance(game, dict) or _event_has_real_core_odds(game.get("oddsCore")):
            continue
        official = game.get("official") if isinstance(game.get("official"), dict) else game
        candidates = [
            value
            for stored_packet in history
            if (value := _candidate(base, match_event, official, stored_packet)) is not None
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
            "postStartOddsFabricationAllowed": False,
            "replayPolicy": (
                "Only an actual stored The Odds API event with real core-market prices, "
                "matching teams/start time, and a packet timestamp strictly before first pitch may be replayed."
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

        packet = assemble(slate, expanded=False)
        result = {
            "ok": True,
            "status": "COLLECTING",
            "requestedSlateDateEt": slate,
            "slateDateEt": slate,
            "deadline": deadline,
            "sourceStatus": packet.get("sourceStatus") or {},
            "threeSourceCoverageComplete": packet.get("threeSourceCoverageComplete"),
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

    production._assemble_with_full_bbd = assemble
    production._pregame_odds_replay_installed = True
    base._assemble = assemble
    strict_bedrock._late_guard = late_guard
