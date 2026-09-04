from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mlb_auto_llm"))

import handler as base


def _game(game_pk: str, start: str, away: str, home: str) -> dict:
    return {
        "gamePk": game_pk,
        "gameDate": start,
        "away": {"name": away},
        "home": {"name": home},
    }


def _event(event_id: str, start: str, away: str, home: str) -> dict:
    return {
        "id": event_id,
        "kickoff_utc": start,
        "away": {"name": away},
        "home": {"name": home},
    }


def test_bbd_resolver_unions_every_official_utc_date(monkeypatch) -> None:
    official = {
        "games": [
            _game("1", "2026-08-24T22:40:00Z", "Away One", "Home One"),
            _game("2", "2026-08-25T01:40:00Z", "Away Two", "Home Two"),
        ]
    }
    calls = []

    def fake_get(path, params):
        calls.append((path, dict(params)))
        rows = {
            "2026-08-24": [
                _event("event-1", "2026-08-24T22:40:00Z", "Away One", "Home One")
            ],
            "2026-08-25": [
                _event("event-2", "2026-08-25T01:40:00Z", "Away Two", "Home Two")
            ],
        }.get(params.get("date"), [])
        return {"data": rows, "meta": {"source": "test"}}

    monkeypatch.setattr(base, "_bbs_get", fake_get)
    result = base._bbs_matches("2026-08-24", official)

    assert [params["date"] for _, params in calls] == [
        "2026-08-24",
        "2026-08-25",
    ]
    assert [row["id"] for row in result["events"]] == ["event-1", "event-2"]
    assert result["meta"]["officialUtcDates"] == ["2026-08-24", "2026-08-25"]
    assert result["meta"]["matchedOfficialGameCount"] == 2
    assert result["meta"]["missingOfficialGamePks"] == []
    assert result["meta"]["unfilteredFallbackUsed"] is False


def test_bbd_resolver_uses_bounded_crosswalk_fallback_and_deduplicates(
    monkeypatch,
) -> None:
    official = {
        "games": [
            _game("1", "2026-08-24T22:40:00Z", "Away One", "Home One"),
            _game("2", "2026-08-25T01:40:00Z", "Away Two", "Home Two"),
        ]
    }
    first = _event("event-1", "2026-08-24T22:40:00Z", "Away One", "Home One")
    second = _event("event-2", "2026-08-25T01:40:00Z", "Away Two", "Home Two")
    calls = []

    def fake_get(path, params):
        calls.append((path, dict(params)))
        if params.get("date") == "2026-08-24":
            return {"data": [first], "meta": {}}
        if params.get("date") == "2026-08-25":
            return {"data": [], "meta": {}}
        return {"data": [first, second], "meta": {}}

    monkeypatch.setattr(base, "_bbs_get", fake_get)
    result = base._bbs_matches("2026-08-24", official)

    assert [row["id"] for row in result["events"]] == ["event-1", "event-2"]
    assert result["meta"]["unfilteredFallbackUsed"] is True
    assert result["meta"]["matchedOfficialGameCount"] == 2
    assert result["meta"]["missingOfficialGamePks"] == []
    assert any("date" not in params for _, params in calls)


def test_bbd_match_rows_accept_the_documented_array_contract() -> None:
    payload = {"data": [{"id": "one"}], "meta": {}, "error": None}

    rows = base._bbs_payload_rows(payload)

    assert rows == [{"id": "one"}]
    assert rows is not payload["data"]
    assert rows[0] is not payload["data"][0]
    assert base._bbs_payload_rows({"data": [], "meta": {}, "error": None}) == []


def test_bbd_match_rows_accept_observed_scores_value_envelope() -> None:
    payload = {
        "data": {"scores": {"value": [{"match_id": "one"}]}},
        "meta": {},
        "error": None,
    }

    rows = base._bbs_payload_rows(payload)

    assert rows == [{"match_id": "one"}]
    assert rows[0] is not payload["data"]["scores"]["value"][0]
    assert base._bbs_payload_envelope(payload) == "data.scores.value"


def test_bbd_resolver_crosswalks_observed_scores_value_rows(monkeypatch) -> None:
    official = {
        "games": [
            _game("1", "2026-09-05T22:40:00Z", "Away One", "Home One")
        ]
    }
    provider_row = {
        "match_id": "event-1",
        "scheduled_at": "2026-09-05T22:40:00Z",
        "away": {"name": "Away One"},
        "home": {"name": "Home One"},
    }

    monkeypatch.setattr(
        base,
        "_bbs_get",
        lambda path, params: {
            "data": {"scores": {"value": [provider_row]}},
            "meta": {},
            "error": None,
        },
    )

    result = base._bbs_matches("2026-09-05", official)

    assert result["events"] == [provider_row]
    assert result["meta"]["matchedOfficialGameCount"] == 1
    assert result["meta"]["missingOfficialGamePks"] == []
    assert result["meta"]["queries"][0]["responseEnvelope"] == "data.scores.value"


@pytest.mark.parametrize(
    "payload,shape",
    [
        (None, "null"),
        ({"meta": {}, "error": None}, "null"),
        ({"data": None, "meta": {}, "error": None}, "null"),
        ({"data": "matches", "meta": {}, "error": None}, "string"),
        ({"data": {"matches": []}, "meta": {}, "error": None}, "object[matches]"),
        (
            {"data": {"scores": {"items": []}}, "meta": {}, "error": None},
            "object[scores]",
        ),
        (
            {
                "data": {"scores": {"value": []}, "matches": []},
                "meta": {},
                "error": None,
            },
            "object[matches,scores]",
        ),
    ],
)
def test_bbd_match_rows_reject_undocumented_envelopes(payload, shape) -> None:
    with pytest.raises(RuntimeError, match="BBS_MATCHES_NOT_LIST") as raised:
        base._bbs_payload_rows(payload)

    assert f'"dataShape":"{shape}"' in str(raised.value)


def test_bbd_match_rows_reject_partial_non_object_arrays() -> None:
    with pytest.raises(RuntimeError, match="BBS_MATCH_ROW_NOT_OBJECT"):
        base._bbs_payload_rows(
            {"data": [{"id": "one"}, "bad-row"], "meta": {}, "error": None}
        )


def test_bbd_get_rejects_falsey_error_objects(monkeypatch) -> None:
    monkeypatch.setattr(
        base,
        "_http_json",
        lambda *args, **kwargs: (
            {"data": [], "meta": {}, "error": {}},
            {},
        ),
    )
    monkeypatch.setattr(base, "_bbs_key", lambda: "secret")

    with pytest.raises(RuntimeError, match="BBS_RESPONSE_INVALID"):
        base._bbs_get("/v1/matches", {"sport": "baseball"})


@pytest.mark.parametrize(
    "payload,reason",
    [
        ([], "NOT_OBJECT"),
        ({"data": [], "meta": {}}, "ENVELOPE_KEYS"),
        ({"data": [], "meta": None, "error": None}, "META_NOT_OBJECT"),
    ],
)
def test_bbd_get_requires_the_standard_live_envelope(
    monkeypatch, payload, reason
) -> None:
    monkeypatch.setattr(
        base,
        "_http_json",
        lambda *args, **kwargs: (payload, {}),
    )
    monkeypatch.setattr(base, "_bbs_key", lambda: "secret")

    with pytest.raises(RuntimeError, match=f"BBS_RESPONSE_INVALID:{reason}"):
        base._bbs_get("/v1/matches", {"sport": "baseball"})


def test_bbd_resolver_rejects_partial_slate_when_one_utc_page_drifts(
    monkeypatch,
) -> None:
    official = {
        "games": [
            _game("1", "2026-08-24T22:40:00Z", "Away One", "Home One"),
            _game("2", "2026-08-25T01:40:00Z", "Away Two", "Home Two"),
        ]
    }
    calls = []

    def fake_get(path, params):
        calls.append((path, dict(params)))
        if params.get("date") == "2026-08-24":
            return {
                "data": [
                    _event(
                        "event-1",
                        "2026-08-24T22:40:00Z",
                        "Away One",
                        "Home One",
                    )
                ],
                "meta": {},
                "error": None,
            }
        return {
            "data": {"scores": {"items": []}},
            "meta": {"source": "drifted"},
            "error": None,
        }

    monkeypatch.setattr(base, "_bbs_get", fake_get)

    with pytest.raises(RuntimeError, match="BBS_MATCHES_NOT_LIST"):
        base._bbs_matches("2026-08-24", official)

    assert [params["date"] for _, params in calls] == [
        "2026-08-24",
        "2026-08-25",
    ]
