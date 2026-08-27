from __future__ import annotations

import json

import orchestrator_v2 as subject
import pytest


@pytest.fixture(autouse=True)
def _fixed_model_catalog(monkeypatch):
    monkeypatch.setattr(
        subject,
        "configured_models",
        lambda: ["us-east-1::test-bedrock-model"],
    )


def _game() -> dict:
    return {
        "gamePk": "823826",
        "gameDate": "2026-08-25T22:40:00Z",
        "home": {"name": "Miami Marlins"},
        "away": {"name": "Boston Red Sox"},
        "official": {
            "gamePk": "823826",
            "gameDate": "2026-08-25T22:40:00Z",
            "home": {"name": "Miami Marlins"},
            "away": {"name": "Boston Red Sox"},
        },
        "oddsCore": {
            "id": "odds-event",
            "home_team": "Miami Marlins",
            "away_team": "Boston Red Sox",
            "bookmakers": [
                {
                    "key": "book",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Miami Marlins", "price": 1.70},
                                {"name": "Boston Red Sox", "price": 1.411},
                            ],
                        }
                    ],
                }
            ],
        },
        "marketConsensus": {
            "available": True,
            "bookCount": 1,
            "homeProbability": 0.4535,
            "awayProbability": 0.5465,
            "marketFavorite": "Boston Red Sox",
            "marketFavoriteProbability": 0.5465,
        },
        "bbs": {"match": {"id": "bbs-event"}},
    }


def test_strict_packet_exposes_normalized_consensus_and_decimal_direction() -> None:
    packet = subject._strict_packet(_game(), {"marketAnchorWeight": 0.72})
    odds = packet["theOddsApi"]

    assert odds["normalizedH2hConsensus"]["marketFavorite"] == "Boston Red Sox"
    assert odds["normalizedH2hConsensus"]["awayProbability"] > odds[
        "normalizedH2hConsensus"
    ]["homeProbability"]
    assert odds["decimalOddsContract"] == {
        "format": "decimal",
        "lowerPriceMeansHigherImpliedProbability": True,
        "rawImpliedProbabilityFormula": "1 / decimal_price",
        "favoriteAuthority": "normalizedH2hConsensus.marketFavorite",
    }


def test_normalizer_treats_lower_decimal_price_as_the_favorite() -> None:
    game = _game()
    game.pop("marketConsensus")

    consensus = subject.base._market_consensus(game)

    assert consensus["available"] is True
    assert consensus["marketFavorite"] == "Boston Red Sox"
    assert consensus["awayProbability"] > consensus["homeProbability"]


def test_bedrock_prompt_cannot_reverse_decimal_price_direction(monkeypatch) -> None:
    captured = {}

    def invoke(prompt, **kwargs):
        captured["prompt"] = prompt
        return {
            "ok": True,
            "modelId": "us.amazon.nova-2-lite-v1:0",
            "text": json.dumps(
                {
                    "winner": "Boston Red Sox",
                    "loser": "Miami Marlins",
                    "probability": 0.55,
                    "confidence": "medium",
                    "rationale": "Normalized no-vig consensus favors Boston.",
                    "source_weights": {"normalized_market_consensus": 0.72},
                    "disagreements": [],
                }
            ),
        }

    monkeypatch.setattr(subject, "invoke_chain_text", invoke)
    result = subject._strict_bedrock_decision(
        _game(), {"marketAnchorWeight": 0.72}
    )

    assert result["winner"] == "Boston Red Sox"
    assert "LOWER decimal price means a HIGHER raw implied win probability" in captured[
        "prompt"
    ]
    assert "Never call the team with the higher decimal price the favorite" in captured[
        "prompt"
    ]
    prompt_packet = json.loads(captured["prompt"].split("DATA=", 1)[1])
    assert (
        prompt_packet["theOddsApi"]["normalizedH2hConsensus"]["marketFavorite"]
        == "Boston Red Sox"
    )


def test_oversized_provider_payload_cannot_truncate_decimal_odds_authority(
    monkeypatch,
) -> None:
    game = _game()
    game["bbs"] = {
        "match": {"id": "bbs-event"},
        "statistics": {"oversized": "x" * 50000},
    }
    captured = {}

    def invoke(prompt, **_kwargs):
        captured["packet"] = json.loads(prompt.split("DATA=", 1)[1])
        return {
            "ok": True,
            "modelId": "us.amazon.nova-2-lite-v1:0",
            "text": json.dumps(
                {
                    "winner": "Boston Red Sox",
                    "loser": "Miami Marlins",
                    "probability": 0.55,
                    "confidence": "medium",
                    "rationale": "Normalized consensus favors Boston.",
                    "source_weights": {
                        "normalized_market_consensus": 0.72,
                    },
                    "disagreements": [],
                }
            ),
        }

    monkeypatch.setattr(subject, "invoke_chain_text", invoke)
    subject._strict_bedrock_decision(
        game,
        {"marketAnchorWeight": 0.72},
    )

    packet = captured["packet"]
    assert packet.get("truncated") is not True
    assert packet["bigBallsDataPro"]["truncated"] is True
    assert (
        packet["theOddsApi"]["normalizedH2hConsensus"]["marketFavorite"]
        == "Boston Red Sox"
    )
    assert packet["theOddsApi"]["decimalOddsContract"] == {
        "format": "decimal",
        "lowerPriceMeansHigherImpliedProbability": True,
        "rawImpliedProbabilityFormula": "1 / decimal_price",
        "favoriteAuthority": "normalizedH2hConsensus.marketFavorite",
    }
    assert len(json.dumps(packet, separators=(",", ":"))) < 30000


def _deployment_smoke_model_response(
    *,
    winner: str = "Boston Red Sox",
    market_favorite: str = "Boston Red Sox",
    market_favorite_price: object = 1.411,
    market_interpretation: str = (
        "The lower decimal price means higher implied probability."
    ),
) -> dict:
    loser = (
        "Miami Marlins" if winner == "Boston Red Sox" else "Boston Red Sox"
    )
    return {
        "ok": True,
        "modelId": "mantle::us-east-1::test-bedrock-model",
        "text": json.dumps(
            {
                "winner": winner,
                "loser": loser,
                "probability": 0.5465,
                "confidence": "deployment-smoke",
                "rationale": "Normalized no-vig consensus favors Boston.",
                "source_weights": {"normalized_market_consensus": 1.0},
                "disagreements": [],
                "market_favorite": market_favorite,
                "market_favorite_price": market_favorite_price,
                "market_interpretation": market_interpretation,
            }
        ),
    }


def _deployment_smoke_event() -> dict:
    return {
        "mode": subject.DEPLOYMENT_DECIMAL_ODDS_SMOKE_MODE,
        "contractVersion": subject.DEPLOYMENT_DECIMAL_ODDS_SMOKE_CONTRACT,
    }


def test_deployment_smoke_runs_deployed_strict_decision_without_mutation(
    monkeypatch,
) -> None:
    calls = {"model": 0, "production": 0}

    def invoke(_prompt, **_kwargs):
        calls["model"] += 1
        return _deployment_smoke_model_response()

    def production_handler(_event, _context):
        calls["production"] += 1
        raise AssertionError("deployment smoke must not enter card runtime")

    original_put = subject.base._put
    monkeypatch.setattr(subject, "invoke_chain_text", invoke)
    monkeypatch.setattr(subject.production, "lambda_handler", production_handler)

    result = subject.lambda_handler(_deployment_smoke_event(), None)

    assert calls == {"model": 1, "production": 0}
    assert subject.base._put is original_put
    assert result["ok"] is True
    assert result["status"] == "DEPLOYMENT_DECIMAL_ODDS_DECISION_VERIFIED"
    assert result["decisionAuthority"] == "BEDROCK_LLM"
    assert result["winner"] == result["marketFavorite"] == "Boston Red Sox"
    assert result["marketFavoritePrice"] == 1.411
    assert result["otherTeamPrice"] == 1.70
    assert result["marketFavoritePrice"] < result["otherTeamPrice"]
    assert result["normalizedH2hConsensus"]["marketFavorite"] == (
        "Boston Red Sox"
    )
    assert result["writeGuardArmed"] is True
    assert result["persistenceAttempted"] is False
    assert result["cardMutationAttempted"] is False
    assert result["historyMutationAttempted"] is False
    assert result["mlFallbackAttempted"] is False


def test_deployment_smoke_event_is_closed_not_configurable(monkeypatch) -> None:
    invoked = []
    monkeypatch.setattr(
        subject,
        "invoke_chain_text",
        lambda *_args, **_kwargs: invoked.append(True),
    )
    event = _deployment_smoke_event()
    event["homePrice"] = 1.20

    try:
        subject.lambda_handler(event, None)
    except RuntimeError as exc:
        assert str(exc) == "DEPLOYMENT_DECIMAL_ODDS_SMOKE_EVENT_REJECTED"
    else:
        raise AssertionError("configurable deployment smoke event was accepted")
    assert invoked == []


def test_deployment_smoke_fails_if_model_reverses_decimal_favorite(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        subject,
        "invoke_chain_text",
        lambda *_args, **_kwargs: _deployment_smoke_model_response(
            winner="Miami Marlins",
            market_favorite="Miami Marlins",
            market_favorite_price=1.70,
        ),
    )

    try:
        subject.lambda_handler(_deployment_smoke_event(), None)
    except RuntimeError as exc:
        assert str(exc) == (
            "DEPLOYMENT_SMOKE_DECIMAL_ODDS_FAVORITE_NOT_SELECTED"
        )
    else:
        raise AssertionError("reversed decimal favorite was accepted")


def test_deployment_smoke_fails_on_parse_or_interpretation_contract_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        subject,
        "invoke_chain_text",
        lambda *_args, **_kwargs: _deployment_smoke_model_response(
            market_interpretation="Boston is favored.",
        ),
    )

    try:
        subject.lambda_handler(_deployment_smoke_event(), None)
    except RuntimeError as exc:
        assert str(exc) == (
            "DEPLOYMENT_SMOKE_DECIMAL_ODDS_INTERPRETATION_MISSING"
        )
    else:
        raise AssertionError("missing decimal interpretation was accepted")


def test_deployment_smoke_fails_on_non_json_model_output(monkeypatch) -> None:
    monkeypatch.setattr(
        subject,
        "invoke_chain_text",
        lambda *_args, **_kwargs: {
            "ok": True,
            "modelId": "mantle::us-east-1::test-bedrock-model",
            "text": "Boston is the favorite, but this is not JSON.",
        },
    )

    try:
        subject.lambda_handler(_deployment_smoke_event(), None)
    except RuntimeError as exc:
        assert str(exc) == "LLM_WINNER_NOT_EXACT_TEAM"
    else:
        raise AssertionError("non-JSON model output was accepted")


def test_deployment_smoke_write_guard_fails_closed(monkeypatch) -> None:
    original_put = subject.base._put

    def decision_with_forbidden_write(*_args, **_kwargs):
        subject.base._put("CARD#2099-07-15", "FINAL", {"forbidden": True})
        raise AssertionError("write guard did not stop mutation")

    monkeypatch.setattr(subject, "_strict_bedrock_decision", decision_with_forbidden_write)

    try:
        subject.lambda_handler(_deployment_smoke_event(), None)
    except RuntimeError as exc:
        assert str(exc).startswith(
            "DEPLOYMENT_DECIMAL_ODDS_SMOKE_WRITE_FORBIDDEN:CARD#"
        )
    else:
        raise AssertionError("deployment smoke persistence was not rejected")
    assert subject.base._put is original_put
