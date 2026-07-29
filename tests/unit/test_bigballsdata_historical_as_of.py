from __future__ import annotations

import json

import pytest

from bigballsdata_client import BBSClientError, BigBallsDataClient


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


def test_stored_historical_match_surface_accepts_data_plus_pagination(monkeypatch):
    seen = {}
    sleeps = []
    monkeypatch.setenv("BBS_HISTORICAL_REQUEST_DELAY_SECONDS", "0.75")

    def opener(request, timeout):
        seen["url"] = request.full_url
        return Response(
            {
                "data": [],
                "pagination": {"limit": 200, "offset": 0, "total": 0},
            }
        )

    client = BigBallsDataClient(
        api_key="bbs_live_abcdefghijkl",
        opener=opener,
        sleeper=sleeps.append,
    )
    value = client.list_mlb_matches("2025-04-01", limit=200, stored=True)

    assert sleeps == [0.75]
    assert "/v1/stored/matches?" in seen["url"]
    assert "sport=baseball" in seen["url"]
    assert "league=mlb" in seen["url"]
    assert "date=2025-04-01" in seen["url"]
    assert value["_transport"]["endpoint"] == "/v1/stored/matches"
    assert value["meta"]["catalogueContract"] == "data_plus_pagination"


def test_stored_historical_delay_is_bounded_and_live_is_unaffected(monkeypatch):
    sleeps = []
    monkeypatch.setenv("BBS_HISTORICAL_REQUEST_DELAY_SECONDS", "99")

    def opener(request, timeout):
        return Response(
            {
                "data": [],
                "meta": {"source": "official-league"},
                "error": None,
            }
        )

    client = BigBallsDataClient(
        api_key="bbs_live_abcdefghijkl",
        opener=opener,
        sleeper=sleeps.append,
    )
    client.list_mlb_matches("2026-07-29", stored=False)
    assert sleeps == []

    stored_client = BigBallsDataClient(
        api_key="bbs_live_abcdefghijkl",
        opener=lambda request, timeout: Response(
            {
                "data": [],
                "pagination": {"limit": 200, "offset": 0, "total": 0},
            }
        ),
        sleeper=sleeps.append,
    )
    stored_client.list_mlb_matches("2025-04-01", stored=True)
    assert sleeps == [5.0]


def test_live_endpoint_does_not_accept_catalogue_envelope():
    def opener(request, timeout):
        return Response(
            {
                "data": [],
                "pagination": {"limit": 50, "offset": 0, "total": 0},
            }
        )

    client = BigBallsDataClient(api_key="bbs_live_abcdefghijkl", opener=opener)

    with pytest.raises(BBSClientError, match="BBS_RESPONSE_ENVELOPE_INCOMPLETE"):
        client.list_mlb_matches("2026-07-29")
