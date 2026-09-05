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


def test_bbd_match_rows_quarantine_scores_value_as_non_match_data() -> None:
    payload = {
        "data": {
            "scores": {
                "value": [
                    {
                        "match_id": "one",
                        "home": 4,
                        "away": 2,
                        "status": "final",
                        "updated_at": "2026-09-05T22:40:00Z",
                    }
                ]
            }
        },
        "meta": {},
        "error": None,
    }

    assert base._bbs_is_scores_field_envelope(payload) is True
    with pytest.raises(RuntimeError, match="BBS_MATCHES_NOT_LIST"):
        base._bbs_payload_rows(payload)


def test_bbd_resolver_uses_stored_catalogue_for_scores_field_envelope(
    monkeypatch,
) -> None:
    official = {
        "games": [
            _game("1", "2026-09-05T22:40:00Z", "Away One", "Home One")
        ]
    }
    provider_row = _event(
        "event-1", "2026-09-05T22:40:00Z", "Away One", "Home One"
    )
    calls = []

    def fake_get(path, params):
        calls.append((path, dict(params)))
        if path == "/v1/matches":
            return {
                "data": {
                    "scores": {
                        "value": [
                            {
                                "match_id": "event-1",
                                "home": None,
                                "away": None,
                                "status": "scheduled",
                                "updated_at": "2026-09-05T00:00:00Z",
                            }
                        ]
                    }
                },
                "meta": {},
                "error": None,
            }
        assert path == "/v1/stored/matches"
        return {"data": [provider_row], "meta": {}, "error": None}

    monkeypatch.setattr(base, "_bbs_get", fake_get)

    result = base._bbs_matches("2026-09-05", official)

    assert result["events"] == [provider_row]
    assert result["meta"]["matchedOfficialGameCount"] == 1
    assert result["meta"]["missingOfficialGamePks"] == []
    assert result["meta"]["storedCatalogueFallbackUsed"] is True
    assert result["meta"]["queries"][0]["responsePath"] == "/v1/stored/matches"
    assert result["meta"]["queries"][0]["liveEnvelopeQuarantined"] == (
        "data.scores.value"
    )
    assert [path for path, _ in calls] == [
        "/v1/matches",
        "/v1/stored/matches",
    ]


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


def test_bbd_get_accepts_catalogue_envelope_only_on_stored_route(
    monkeypatch,
) -> None:
    payload = {
        "data": [],
        "pagination": {"limit": 200, "offset": 0, "total": 0},
    }
    monkeypatch.setattr(
        base,
        "_http_json",
        lambda *args, **kwargs: (payload, {}),
    )
    monkeypatch.setattr(base, "_bbs_key", lambda: "secret")

    stored = base._bbs_get(
        "/v1/stored/matches", {"sport": "baseball", "league": "mlb"}
    )

    assert stored["data"] == []
    assert stored["error"] is None
    assert stored["meta"]["catalogueContract"] == "data_plus_pagination"
    with pytest.raises(RuntimeError, match="BBS_RESPONSE_INVALID:ENVELOPE_KEYS"):
        base._bbs_get("/v1/matches", {"sport": "baseball", "league": "mlb"})


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


def test_bbd_utc_union_excludes_same_series_event_on_next_eastern_day(
    monkeypatch,
) -> None:
    official = {
        "games": [
            _game(
                "1",
                "2026-09-06T01:40:00Z",
                "Chicago Cubs",
                "Miami Marlins",
            )
        ]
    }
    current = _event(
        "current",
        "2026-09-06T01:40:00Z",
        "Chicago Cubs",
        "Miami Marlins",
    )
    next_et_day = _event(
        "next-et-day",
        "2026-09-06T17:00:00Z",
        "Chicago Cubs",
        "Miami Marlins",
    )

    monkeypatch.setattr(
        base,
        "_bbs_get",
        lambda _path, _params: {
            "data": [current, next_et_day],
            "meta": {},
            "error": None,
        },
    )

    result = base._bbs_matches("2026-09-05", official)

    assert [row["id"] for row in result["events"]] == ["current"]
    assert result["assignments"]["1"]["id"] == "current"
    assert result["meta"]["matchedOfficialGameCount"] == 1
    assert result["meta"]["missingOfficialGamePks"] == []


def test_one_bbd_event_cannot_cover_two_official_games() -> None:
    official = [
        _game("1", "2026-09-05T17:10:00Z", "Away", "Home"),
        _game("2", "2026-09-05T23:40:00Z", "Away", "Home"),
    ]
    rows = [_event("only", "2026-09-05T17:12:00Z", "Away", "Home")]

    assigned = base._assign_bbs_events(official, rows)

    assert assigned["1"]["id"] == "only"
    assert "2" not in assigned
    assert len({row["id"] for row in assigned.values()}) == 1


def test_shuffled_bbd_doubleheader_rows_pair_to_nearest_unique_games() -> None:
    official = [
        _game("1", "2026-09-05T17:10:00Z", "Away", "Home"),
        _game("2", "2026-09-05T23:40:00Z", "Away", "Home"),
    ]
    rows = [
        _event("late", "2026-09-05T23:42:00Z", "Away", "Home"),
        _event("early", "2026-09-05T17:12:00Z", "Away", "Home"),
    ]

    assigned = base._assign_bbs_events(official, rows)

    assert assigned["1"]["id"] == "early"
    assert assigned["2"]["id"] == "late"
    assert len({row["id"] for row in assigned.values()}) == 2


@pytest.mark.parametrize(
    "provider_row",
    [
        {
            "id": "missing-start",
            "away": {"name": "Away"},
            "home": {"name": "Home"},
        },
        _event("reversed", "2026-09-05T17:10:00Z", "Home", "Away"),
    ],
    ids=("missing-start", "reversed-teams"),
)
def test_bbd_assignment_rejects_missing_start_and_reversed_teams(
    provider_row,
) -> None:
    official = [_game("1", "2026-09-05T17:10:00Z", "Away", "Home")]

    assert base._assign_bbs_events(official, [provider_row]) == {}


def test_assembly_reuses_resolver_bbd_assignment_instead_of_rematching(
    monkeypatch,
) -> None:
    official_game = _game(
        "1",
        "2026-09-05T20:10:00Z",
        "Chicago Cubs",
        "Miami Marlins",
    )
    resolver_selection = _event(
        "resolver-selection",
        "2026-09-05T21:00:00Z",
        "Chicago Cubs",
        "Miami Marlins",
    )
    rematch_decoy = _event(
        "rematch-decoy",
        "2026-09-05T20:10:00Z",
        "Chicago Cubs",
        "Miami Marlins",
    )

    monkeypatch.setattr(
        base,
        "_official_schedule",
        lambda _slate: {"totalGames": 1, "games": [official_game]},
    )
    monkeypatch.setattr(
        base,
        "_odds_core",
        lambda _slate: {
            "events": [],
            "catalogEvents": [],
            "oddsRequestOk": True,
            "catalogRequestOk": True,
            "quota": {},
        },
    )
    monkeypatch.setattr(
        base,
        "_bbs_matches",
        lambda _slate, _official: {
            "events": [rematch_decoy, resolver_selection],
            "assignments": {"1": resolver_selection},
        },
    )

    packet = base._assemble("2026-09-05", expanded=False)

    assert packet["games"][0]["bbs"]["match"]["id"] == "resolver-selection"
