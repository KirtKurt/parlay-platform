from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

MODULE = r'''from __future__ import annotations

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
'''

TEST = r'''from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pregame_odds_replay as replay


SLATE = "2026-08-24"
GAME_START = "2026-08-24T22:40:00+00:00"
EVENT = {
    "id": "odds-event-1",
    "home_team": "Detroit Tigers",
    "away_team": "Tampa Bay Rays",
    "commence_time": GAME_START,
    "bookmakers": [
        {
            "key": "book",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Detroit Tigers", "price": 1.8},
                        {"name": "Tampa Bay Rays", "price": 2.1},
                    ],
                }
            ],
        }
    ],
}


class Table:
    def __init__(self, captured_at):
        self.captured_at = captured_at
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "Items": [
                {
                    "data": {
                        "slateDateEt": SLATE,
                        "retrievedAtUtc": self.captured_at,
                        "games": [
                            {
                                "gamePk": "824235",
                                "oddsCore": EVENT,
                            }
                        ],
                    }
                }
            ]
        }


def parse(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def market_consensus(game):
    return {"available": bool(game.get("oddsCore")), "bookCount": 1}


def match_event(game, rows, *, provider):
    assert provider == "odds"
    for row in rows:
        if (
            row.get("home_team") == game["home"]["name"]
            and row.get("away_team") == game["away"]["name"]
            and row.get("commence_time") == game["gameDate"]
        ):
            return row
    return None


def apply_coverage(packet):
    missing = [
        str(game["gamePk"])
        for game in packet["games"]
        if not replay._event_has_real_core_odds(game.get("oddsCore"))
    ]
    status = packet.setdefault("sourceStatus", {}).setdefault("theOddsApi", {})
    status.update(
        {
            "ok": not missing,
            "scheduledGames": len(packet["games"]),
            "matchedGames": len(packet["games"]) - len(missing),
            "missingGamePks": missing,
        }
    )
    packet["threeSourceCoverageComplete"] = not missing
    return packet


def packet():
    official = {
        "gamePk": "824235",
        "gameDate": GAME_START,
        "home": {"name": "Detroit Tigers"},
        "away": {"name": "Tampa Bay Rays"},
    }
    return {
        "slateDateEt": SLATE,
        "retrievedAtUtc": "2026-08-25T02:10:00+00:00",
        "games": [
            {
                "gamePk": "824235",
                "gameDate": GAME_START,
                "home": official["home"],
                "away": official["away"],
                "official": official,
                "oddsCore": None,
            }
        ],
        "sourceStatus": {
            "mlbStatsApi": {"ok": True},
            "theOddsApi": {"ok": False},
            "bigBallsDataPro": {"ok": True},
        },
    }


def base(captured_at):
    return SimpleNamespace(
        TABLE=Table(captured_at),
        _plain=lambda value: value,
        _parse=parse,
        _iso=iso,
        _market_consensus=market_consensus,
    )


def test_replays_only_real_pregame_odds_snapshot():
    namespace = base("2026-08-24T20:00:00+00:00")
    production = SimpleNamespace(_apply_source_coverage=apply_coverage)

    result = replay.recover_persisted_pregame_odds(
        namespace, production, match_event, packet()
    )

    event = result["games"][0]["oddsCore"]
    assert event["id"] == "odds-event-1"
    assert event["_inqsiPregameEvidence"]["capturedBeforeGameStart"] is True
    assert result["threeSourceCoverageComplete"] is True
    status = result["sourceStatus"]["theOddsApi"]
    assert status["persistedPregameRecoveryCount"] == 1
    assert status["postStartOddsFabricationAllowed"] is False


def test_rejects_packet_captured_after_first_pitch():
    namespace = base("2026-08-24T23:00:00+00:00")
    production = SimpleNamespace(_apply_source_coverage=apply_coverage)

    result = replay.recover_persisted_pregame_odds(
        namespace, production, match_event, packet()
    )

    assert result["games"][0]["oddsCore"] is None
    assert result["threeSourceCoverageComplete"] is False
    assert result["sourceStatus"]["theOddsApi"]["persistedPregameRecoveryCount"] == 0


def test_install_uses_current_slate_evidence_without_late_publication():
    namespace = base("2026-08-24T20:00:00+00:00")
    namespace.ET = timezone.utc
    namespace._now = lambda: datetime(2026, 8, 25, 2, 10, tzinfo=timezone.utc)
    namespace._get = lambda pk, sk: None
    namespace._official_schedule = lambda slate: {"games": [packet()["games"][0]["official"]]}
    namespace._deadline = lambda schedule: {"publishDeadlineUtc": "2026-08-24T22:30:00+00:00"}

    validation = []
    production = SimpleNamespace(
        _assemble_with_full_bbd=lambda slate, expanded: apply_coverage(packet()),
        _apply_source_coverage=apply_coverage,
        _validate_deployment_smoke=lambda result: validation.append(result),
    )
    strict = SimpleNamespace(_late_guard=lambda payload: {"original": True})

    replay.install(namespace, production, strict, match_event=match_event)
    result = strict._late_guard(
        {"mode": "deployment_provider_smoke", "slate_date": SLATE}
    )

    assert result["slateDateEt"] == SLATE
    assert result["providerProbeUsedFutureSlate"] is False
    assert result["providerProbeUsedPersistedPregameEvidence"] is True
    assert result["latePublicationPrevented"] is True
    assert result["postStartPredictionCreationAllowed"] is False
    assert validation and validation[0] is result
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"missing {label} anchor")
    return text.replace(old, new, 1)


def main() -> None:
    module_path = ROOT / "mlb_auto_llm" / "pregame_odds_replay.py"
    module_path.write_text(MODULE, encoding="utf-8")

    test_path = ROOT / "tests" / "unit" / "test_mlb_auto_pregame_odds_replay.py"
    test_path.write_text(TEST, encoding="utf-8")

    orchestrator = ROOT / "mlb_auto_llm" / "orchestrator_v3.py"
    text = orchestrator.read_text(encoding="utf-8")
    install = '''\nfrom pregame_odds_replay import install as _install_pregame_odds_replay\n\n_install_pregame_odds_replay(\n    base,\n    production,\n    strict_bedrock,\n    match_event=_match_event_v2,\n)\n\n\n'''
    text = replace_once(
        text,
        "\ndef lambda_handler(event: Any, context: Any) -> Any:\n",
        install + "def lambda_handler(event: Any, context: Any) -> Any:\n",
        "orchestrator install",
    )
    orchestrator.write_text(text, encoding="utf-8")

    workflow = ROOT / ".github" / "workflows" / "deploy-mlb-auto-llm.yml"
    text = workflow.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "            mlb_auto_llm/orchestrator_v3.py \\\n            mlb_auto_llm/ml_authority.py \\\n",
        "            mlb_auto_llm/orchestrator_v3.py \\\n            mlb_auto_llm/pregame_odds_replay.py \\\n            mlb_auto_llm/ml_authority.py \\\n",
        "compile list",
    )
    text = replace_once(
        text,
        "            tests/unit/test_mlb_auto_ml_authority.py\n",
        "            tests/unit/test_mlb_auto_ml_authority.py \\\n            tests/unit/test_mlb_auto_pregame_odds_replay.py\n",
        "pytest list",
    )
    text = replace_once(
        text,
        "          grep -q 'teamRecentForm' mlb_auto_llm/orchestrator.py\n",
        "          grep -q 'teamRecentForm' mlb_auto_llm/orchestrator.py\n          grep -q 'postStartOddsFabricationAllowed' mlb_auto_llm/pregame_odds_replay.py\n",
        "source contract",
    )
    workflow.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
