from __future__ import annotations

from datetime import datetime, timezone

import handler
from mlb_auto_llm import decision_evidence
import orchestrator_v3
import pytest


def _parse(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _normalize(value):
    return str(value or "").strip().lower()


def _consensus(game):
    event = game["oddsCore"]
    return {
        "available": True,
        "bookCount": event["bookCount"],
        "homeProbability": event["homeProbability"],
        "awayProbability": 1.0 - event["homeProbability"],
        "marketFavorite": "Home" if event["homeProbability"] > 0.5 else "Away",
    }


def _packet(captured_at, home_probability):
    return {
        "slateDateEt": "2026-09-04",
        "retrievedAtUtc": captured_at,
        "games": [
            {
                "gamePk": "1",
                "gameDate": "2026-09-04T23:00:00Z",
                "home": {"name": "Home"},
                "away": {"name": "Away"},
                "official": {
                    "gamePk": "1",
                    "home": {"probablePitcher": {"id": 10, "fullName": "Home SP"}},
                    "away": {"probablePitcher": {"id": 20, "fullName": "Away SP"}},
                },
                "bbs": {
                    "match": {"id": "bbs-1"},
                    "lineups": {"ok": True, "data": [{"battingOrder": 1}]},
                    "statistics": {"ok": True, "data": {"reliefEra": 3.5}},
                    "teamForm": {"home": {"ok": True, "data": {"games": 10}}},
                },
                "officialBullpenContext": {
                    "home": {
                        "available": True,
                        "reliefPitches1d": 22,
                        "reliefPitches3d": 91,
                    },
                    "away": {
                        "available": True,
                        "reliefPitches1d": 48,
                        "reliefPitches3d": 137,
                    },
                },
                "oddsCore": {
                    "id": "odds-1",
                    "bookCount": 8,
                    "homeProbability": home_probability,
                },
                "marketConsensus": {
                    "available": True,
                    "bookCount": 8,
                    "homeProbability": home_probability,
                    "awayProbability": 1.0 - home_probability,
                    "marketFavorite": "Home" if home_probability > 0.5 else "Away",
                },
            }
        ],
    }


def _historical_payload(*, qualified=False):
    return {
        "model_version": "INQSI-MLB-prospective-test",
        "primaryAlgorithm": "chronological-walk-forward",
        "winner_predictions": [
            {
                "gamePk": "1",
                "homeTeam": "Home",
                "awayTeam": "Away",
                "predictedWinner": "Away",
                "probability": 0.61,
                "tags": ["UNDERDOG", "STEAM"],
                "winnerStackV2": {
                    "components": {
                        "movement": {"score": 64.0, "cleanSteam": True}
                    }
                },
                "mlOptimizationRuntime": {
                    "championAvailable": qualified,
                    "directionAuthorityEnabled": qualified,
                    "shadowOnly": not qualified,
                },
            }
        ],
    }


def test_attaches_live_historical_and_real_pregame_moneyline_movement():
    opening = _packet("2026-09-04T18:00:00Z", 0.56)
    middle = _packet("2026-09-04T20:00:00Z", 0.52)
    current = _packet("2026-09-04T22:00:00Z", 0.49)
    post_start = _packet("2026-09-04T23:10:00Z", 0.80)

    result = decision_evidence.attach(
        current,
        historical_payload=_historical_payload(),
        packet_history=[opening, middle, post_start],
        normalize=_normalize,
        parse=_parse,
        iso=_iso,
        market_consensus=_consensus,
    )

    evidence = result["games"][0]["decisionEvidence"]
    assert result["decisionEvidenceComplete"] is True
    assert evidence["weights"] == decision_evidence.DECISION_WEIGHTS
    assert evidence["moneylineMovement"]["observationCount"] == 3
    assert evidence["moneylineMovement"]["homeProbabilityDelta"] == -0.07
    assert evidence["moneylineMovement"]["latestHomeProbability"] == 0.49
    assert evidence["moneylineMovement"]["postStartObservationsExcluded"] is True
    assert evidence["historicalModelFindings"]["predictedWinner"] == "Away"
    assert evidence["historicalModelFindings"]["homeWinProbability"] == 0.39
    assert evidence["historicalModelFindings"]["advisoryOnly"] is True
    assert evidence["liveBaseballContext"]["battingLineupsAvailable"] is True
    assert (
        evidence["liveBaseballContext"]["bullpenAvailabilityInputsAvailable"]
        is True
    )
    assert result["qualifiedHistoricalChampionForEveryGame"] is False


def test_missing_movement_history_fails_decision_evidence_closed():
    current = _packet("2026-09-04T22:00:00Z", 0.49)

    result = decision_evidence.attach(
        current,
        historical_payload=_historical_payload(),
        packet_history=[],
        normalize=_normalize,
        parse=_parse,
        iso=_iso,
        market_consensus=_consensus,
    )

    assert result["decisionEvidenceComplete"] is False
    assert result["decisionEvidenceMissingByGame"] == [
        {"gamePk": "1", "missing": ["moneylineMovement"]}
    ]


def test_missing_batting_lineup_or_bullpen_inputs_fails_closed():
    current = _packet("2026-09-04T22:00:00Z", 0.49)
    opening = _packet("2026-09-04T18:00:00Z", 0.56)
    current["games"][0]["bbs"]["lineups"] = {"ok": True, "data": []}

    result = decision_evidence.attach(
        current,
        historical_payload=_historical_payload(),
        packet_history=[opening],
        normalize=_normalize,
        parse=_parse,
        iso=_iso,
        market_consensus=_consensus,
    )

    live = result["games"][0]["decisionEvidence"]["liveBaseballContext"]
    assert live["battingLineupsAvailable"] is False
    assert live["bullpenAvailabilityInputsAvailable"] is True
    assert result["decisionEvidenceComplete"] is False
    assert result["decisionEvidenceMissingByGame"] == [
        {"gamePk": "1", "missing": ["liveBaseballContext"]}
    ]


def test_missing_official_bullpen_workload_fails_closed():
    current = _packet("2026-09-04T22:00:00Z", 0.49)
    opening = _packet("2026-09-04T18:00:00Z", 0.56)
    current["games"][0]["officialBullpenContext"] = {}

    result = decision_evidence.attach(
        current,
        historical_payload=_historical_payload(),
        packet_history=[opening],
        normalize=_normalize,
        parse=_parse,
        iso=_iso,
        market_consensus=_consensus,
    )

    live = result["games"][0]["decisionEvidence"]["liveBaseballContext"]
    assert live["battingLineupsAvailable"] is True
    assert live["bullpenAvailabilityInputsAvailable"] is False
    assert result["decisionEvidenceComplete"] is False


def test_official_bullpen_workload_excludes_starters(monkeypatch):
    schedule = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 99,
                        "officialDate": "2026-09-03",
                        "status": {"abstractGameState": "Final"},
                        "teams": {
                            "home": {"team": {"id": 1}},
                            "away": {"team": {"id": 2}},
                        },
                    }
                ]
            }
        ]
    }
    boxscore = {
        "teams": {
            "home": {
                "pitchers": [101, 102],
                "players": {
                    "ID101": {
                        "person": {"fullName": "Starter"},
                        "stats": {
                            "pitching": {"gamesStarted": 1, "numberOfPitches": 91}
                        },
                    },
                    "ID102": {
                        "person": {"fullName": "Reliever"},
                        "stats": {
                            "pitching": {"gamesStarted": 0, "numberOfPitches": 27}
                        },
                    },
                },
            },
            "away": {"pitchers": [], "players": {}},
        }
    }

    def http_json(url, **_kwargs):
        return (boxscore if url.endswith("/99/boxscore") else schedule), {}

    monkeypatch.setattr(orchestrator_v3.production.base, "_http_json", http_json)
    result = orchestrator_v3.production._official_bullpen_context(
        "2026-09-04",
        [{"home": {"id": 1}, "away": {"id": 2}}],
    )

    assert result["1"]["reliefPitches1d"] == 27
    assert result["1"]["reliefPitches3d"] == 27
    assert result["1"]["relieverAppearances3d"] == 1
    assert result["1"]["relievers"] == [
        {
            "id": "102",
            "name": "Reliever",
            "pitches1d": 27,
            "pitches3d": 27,
            "appearances3d": 1,
        }
    ]


def test_only_explicit_non_shadow_champion_qualifies_for_production():
    current = _packet("2026-09-04T22:00:00Z", 0.49)
    opening = _packet("2026-09-04T18:00:00Z", 0.56)

    result = decision_evidence.attach(
        current,
        historical_payload=_historical_payload(qualified=True),
        packet_history=[opening],
        normalize=_normalize,
        parse=_parse,
        iso=_iso,
        market_consensus=_consensus,
    )

    assert result["decisionEvidenceComplete"] is True
    assert result["qualifiedHistoricalChampionForEveryGame"] is True
    assert (
        result["games"][0]["decisionEvidence"]["historicalModelFindings"]
        ["qualifiedProductionChampion"]
        is True
    )


def test_recent_accuracy_never_reweights_toward_market_favorites(monkeypatch):
    monkeypatch.setattr(
        handler,
        "_now",
        lambda: datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        handler,
        "_get",
        lambda _pk, _sk: {"graded": 10, "correct": 1},
    )

    state = handler._recent_accuracy_state()

    assert state["recentAccuracy"] == 0.1
    assert state["targetRole"] == (
        "long_term_goal_not_a_decision_weight_or_advancement_gate"
    )
    assert state["decisionWeights"] == decision_evidence.DECISION_WEIGHTS
    assert state["marketFavoriteFallbackAllowed"] is False
    assert "marketAnchorWeight" not in state


def _covered_card_packet():
    return {
        "slateDateEt": "2026-09-04",
        "sourceStatus": {},
        "games": [
            {
                "gamePk": "1",
                "home": {"name": "Home"},
                "away": {"name": "Away"},
                "official": {"gamePk": "1"},
                "oddsCore": {"id": "odds-1"},
                "bbs": {"match": {"id": "bbs-1"}},
            }
        ],
    }


def test_card_creation_fails_closed_when_weighted_evidence_is_incomplete(
    monkeypatch,
):
    def incomplete(packet):
        packet["decisionEvidenceComplete"] = False
        packet["decisionEvidenceMissingByGame"] = [
            {"gamePk": "1", "missing": ["moneylineMovement"]}
        ]
        return packet

    monkeypatch.setattr(orchestrator_v3, "_attach_decision_evidence", incomplete)

    with pytest.raises(RuntimeError, match="MLB_DECISION_EVIDENCE_INCOMPLETE"):
        orchestrator_v3._build_card_three_source_model(_covered_card_packet())


@pytest.mark.parametrize("qualified", [False, True])
def test_official_production_flag_requires_explicit_qualified_champion(
    monkeypatch,
    qualified,
):
    def attached(packet):
        packet["decisionEvidenceComplete"] = True
        packet["decisionEvidenceVersion"] = decision_evidence.VERSION
        packet["qualifiedHistoricalChampionForEveryGame"] = qualified
        return packet

    monkeypatch.setattr(orchestrator_v3, "_attach_decision_evidence", attached)
    monkeypatch.setattr(
        orchestrator_v3,
        "_model_card",
        lambda _packet: {
            "picks": [{"gamePk": "1", "decisionAuthority": "BEDROCK_LLM"}]
        },
    )

    card = orchestrator_v3._build_card_three_source_model(
        _covered_card_packet()
    )

    assert card["historicalChampionQualified"] is qualified
    assert card["productionAuthorityBlocked"] is (not qualified)
    assert card["officialProductionPickCount"] == (1 if qualified else 0)
    assert card["picks"][0]["officialProductionPick"] is qualified
