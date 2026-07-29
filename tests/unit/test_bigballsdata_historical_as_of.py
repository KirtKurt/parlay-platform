from __future__ import annotations

import json

from bigballsdata_client import BigBallsDataClient


class Response:
    status = 200
    headers = {"x-ratelimit-remaining": "99"}

    def __init__(self, payload):
        self.payload = payload

    def getcode(self):
        return 200

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_resource_request_includes_historical_as_of(monkeypatch):
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        return Response(
            {
                "data": {},
                "meta": {"asOfUtc": "2026-07-01T22:15:00Z"},
                "error": None,
            }
        )

    client = BigBallsDataClient(api_key="bbs_live_abcdefghijkl", opener=opener)
    value = client.get_mlb_match_resource(
        "m 1",
        "pitchers",
        game_date="2026-07-01",
        as_of="2026-07-01T22:15:00Z",
    )

    assert "as_of=2026-07-01T22%3A15%3A00Z" in seen["url"]
    assert value["_transport"]["requestedAsOfUtc"] == "2026-07-01T22:15:00Z"


def test_stored_historical_match_surface_is_explicit():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        return Response(
            {
                "data": [],
                "meta": {"source": "cache"},
                "error": None,
            }
        )

    client = BigBallsDataClient(api_key="bbs_live_abcdefghijkl", opener=opener)
    value = client.list_mlb_matches("2025-04-01", limit=200, stored=True)

    assert "/v1/stored/matches?" in seen["url"]
    assert "sport=baseball" in seen["url"]
    assert "league=mlb" in seen["url"]
    assert "date=2025-04-01" in seen["url"]
    assert value["_transport"]["endpoint"] == "/v1/stored/matches"
