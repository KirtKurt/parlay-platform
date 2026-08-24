from hello_world import mlb_three_api_prediction_overlay as overlay


def _row():
    return {
        "official_game_pk": "123",
        "official_commence_time": "2026-08-24T23:10:00+00:00",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "predictedWinner": "Home Team",
        "winProbability": 0.60,
        "bookmakers": [
            {
                "key": "book1",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home Team", "price": -110},
                            {"name": "Away Team", "price": -110},
                        ],
                    }
                ],
            },
            {
                "key": "book2",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home Team", "price": 105},
                            {"name": "Away Team", "price": -125},
                        ],
                    }
                ],
            },
        ],
        "advancedContext": {
            "official_game_pk": "123",
            "schedule_authority": "MLB Stats API exact-date schedule",
        },
    }


def test_final_decision_materially_blends_existing_model_market_and_llm(monkeypatch):
    monkeypatch.setenv("MLB_THREE_API_ENABLED", "true")
    monkeypatch.setenv("MLB_THREE_API_REQUIRE_ALL_SOURCES", "true")

    bbd_context = {
        "sourceStatus": "CONNECTED",
        "operationsSucceeded": 4,
        "contextFingerprint": "bbd-fingerprint",
        "categories": {},
    }
    monkeypatch.setattr(
        overlay.bbd,
        "collect_game_context",
        lambda game, as_of_utc=None: bbd_context,
    )
    monkeypatch.setattr(
        overlay.bbd,
        "merge_into_advanced_context",
        lambda context, bbd: {
            **context,
            "big_balls_data_pro": bbd,
            "three_api_source_status": {
                "mlbStatsApi": "PRESENT",
                "theOddsApi": "PRESENT",
                "bigBallsDataPro": "CONNECTED",
            },
        },
    )
    monkeypatch.setattr(
        overlay.llm,
        "analyze_game",
        lambda game, context, as_of_utc=None: {
            "status": "CONNECTED",
            "modelId": "test-model",
            "predictedWinner": "Away Team",
            "predictedLoser": "Home Team",
            "winProbability": 0.70,
            "sourceCompleteness": 1.0,
            "evidenceFingerprint": "llm-fingerprint",
        },
    )

    result = overlay.apply_prediction_overlay(_row(), as_of_utc="2026-08-24T16:00:00+00:00")
    decision = result["threeApiDecision"]
    components = {row["component"]: row for row in decision["components"]}

    assert set(components) == {
        "existingAutonomousMLModel",
        "theOddsApiNoVigMarketConsensus",
        "bedrockThreeSourceAnalyst",
    }
    assert components["bedrockThreeSourceAnalyst"]["weight"] > 0
    assert components["theOddsApiNoVigMarketConsensus"]["weight"] > 0
    assert all(decision["sourceReady"].values())
    assert result["predictedWinner"] in {"Home Team", "Away Team"}
    assert result["predictedLoser"] in {"Home Team", "Away Team"}
    assert result["predictedWinner"] != result["predictedLoser"]
    assert decision["llmEvidenceFingerprint"] == "llm-fingerprint"
    assert decision["bbdContextFingerprint"] == "bbd-fingerprint"
    assert decision["noPass"] is True
    assert decision["accuracyGuarantee"] is False


def test_strict_mode_rejects_missing_bbd_or_llm(monkeypatch):
    monkeypatch.setenv("MLB_THREE_API_ENABLED", "true")
    monkeypatch.setenv("MLB_THREE_API_REQUIRE_ALL_SOURCES", "true")
    monkeypatch.setattr(
        overlay.bbd,
        "collect_game_context",
        lambda game, as_of_utc=None: {
            "sourceStatus": "ERROR",
            "operationsSucceeded": 0,
            "errors": ["provider unavailable"],
        },
    )
    monkeypatch.setattr(
        overlay.bbd,
        "merge_into_advanced_context",
        lambda context, bbd: {**context, "big_balls_data_pro": bbd},
    )
    monkeypatch.setattr(
        overlay.llm,
        "analyze_game",
        lambda game, context, as_of_utc=None: {"status": "UNAVAILABLE"},
    )

    try:
        overlay.apply_prediction_overlay(_row())
    except overlay.ThreeApiPredictionError as exc:
        assert "THREE_API_SOURCE_NOT_READY" in str(exc)
    else:
        raise AssertionError("Strict mode must fail closed when BBD/LLM evidence is unavailable")


def test_overlay_installer_wraps_prediction_function(monkeypatch):
    monkeypatch.setenv("MLB_THREE_API_ENABLED", "true")
    namespace = {}

    def make_prediction():
        return _row()

    namespace["make_prediction"] = make_prediction
    monkeypatch.setattr(
        overlay,
        "apply_to_value",
        lambda value, as_of_utc=None, depth=0: {**value, "overlayApplied": True},
    )
    installed = overlay.install_named_overlays(namespace, "unit_test_module", ["make_prediction"])
    assert installed == ["make_prediction"]
    assert namespace["make_prediction"]()["overlayApplied"] is True
