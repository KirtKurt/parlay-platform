from __future__ import annotations

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
