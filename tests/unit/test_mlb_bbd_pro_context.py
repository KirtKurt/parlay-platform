from hello_world import mlb_bbd_pro_context as bbd


def test_bbd_openapi_discovery_collection_and_projection(monkeypatch):
    monkeypatch.setenv("BIG_BALLS_DATA_API_KEY", "unit-test-key")
    monkeypatch.setenv("BIG_BALLS_DATA_OPENAPI_URL", "https://provider.test/openapi.json")
    bbd._OPENAPI_CACHE.update({"loaded_at": 0.0, "manifest": None, "error": None})
    bbd._RESPONSE_CACHE.clear()

    spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://provider.test"}],
        "paths": {
            "/v1/mlb/lineups": {
                "get": {
                    "operationId": "getMlbLineups",
                    "summary": "MLB confirmed starting lineups",
                    "parameters": [
                        {"name": "date", "in": "query", "required": True, "schema": {"type": "string"}}
                    ],
                }
            },
            "/v1/mlb/injuries": {
                "get": {
                    "operationId": "getMlbInjuries",
                    "summary": "MLB injuries and IL reports",
                    "parameters": [
                        {"name": "date", "in": "query", "required": True, "schema": {"type": "string"}}
                    ],
                }
            },
            "/v1/mlb/probable-pitchers": {
                "get": {
                    "operationId": "getMlbProbablePitchers",
                    "summary": "MLB probable starting pitchers",
                    "parameters": [
                        {"name": "date", "in": "query", "required": True, "schema": {"type": "string"}}
                    ],
                }
            },
        },
    }

    def fake_get(request, timeout):
        url = request.full_url
        if url == "https://provider.test/openapi.json":
            return spec
        if "/lineups" in url:
            return {"lineups": [{"team": "Home", "confirmed": True, "batting_order": ["A", "B"]}]}
        if "/injuries" in url:
            return {"injuries": [{"team": "Away", "player": "P", "status": "10-day IL"}]}
        if "/probable-pitchers" in url:
            return {"probable_pitchers": [{"team": "Home", "starter": "Starter H", "throws": "R"}]}
        raise AssertionError(url)

    game = {
        "official_game_pk": "123",
        "official_commence_time": "2026-08-24T23:10:00+00:00",
        "away_team": "Away",
        "home_team": "Home",
        "provider_event_id": "odds-event-1",
        "bookmakers": [{"key": "book", "markets": []}],
    }
    context = bbd.collect_game_context(game, as_of_utc="2026-08-24T15:00:00+00:00", http_get=fake_get)
    assert context["sourceStatus"] == "CONNECTED"
    assert context["operationsSucceeded"] == 3
    assert context["contextFingerprint"]

    merged = bbd.merge_into_advanced_context(
        {
            "official_game_pk": "123",
            "bookmakers": game["bookmakers"],
            "confirmed_lineups": {"source_status": "MISSING_FROM_PROVIDER"},
            "injuries_late_scratches_news": {"source_status": "MISSING_FROM_PROVIDER"},
            "confirmed_probable_pitchers": {"source_status": "MISSING_FROM_PROVIDER"},
        },
        context,
    )
    assert merged["confirmed_lineups"]["source_status"] == "CONNECTED"
    assert merged["injuries_late_scratches_news"]["source_status"] == "CONNECTED"
    assert merged["confirmed_probable_pitchers"]["source_status"] == "CONNECTED"
    assert merged["three_api_source_status"]["bigBallsDataPro"] == "CONNECTED"


def test_missing_key_is_explicit_not_silently_fabricated(monkeypatch):
    for name in bbd.KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    result = bbd.collect_game_context(
        {
            "official_game_pk": "1",
            "official_commence_time": "2026-08-24T23:00:00+00:00",
            "away_team": "Away",
            "home_team": "Home",
        }
    )
    assert result["sourceStatus"] == "NOT_CONFIGURED"
    assert result["operationsSucceeded"] == 0
