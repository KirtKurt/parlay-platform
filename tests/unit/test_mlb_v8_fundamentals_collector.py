from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from hello_world.mlb_v8_fundamentals_collector import _put_immutable, build_snapshot, normalize_match

NOW = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)


def _lineup():
    return [{"id": f"p{i}", "name": f"Player {i}", "battingOrder": i, "position": "OF"} for i in range(1, 10)]


def _match(match_id="game-1"):
    return {"id": match_id, "date": "2026-07-29", "awayTeam": {"id": "A", "name": "Away"}, "homeTeam": {"id": "H", "name": "Home"}}


def _resources():
    meta = {"confirmed": True, "updatedAt": "2026-07-29T11:55:00Z", "source": "bbs"}
    team = {"record": "55-50", "recentForm": "7-3", "homeAwaySplit": {"away": "25-25"}, "restDays": 1, "travel": {"miles": 200}}
    bullpen = {"era": 3.5, "fip": 3.6, "last3DaysInnings": 7.2, "last2DaysPitches": 83, "highLeverageAvailable": True, "closerAvailable": True, "expectedInnings": 3.1}
    starter = {"id": "sp", "name": "Starter", "confirmed": True, "stats": {"era": 3.2, "fip": 3.4}}
    return {
        "pitchers": {"data": {"away": {**starter, "id": "asp"}, "home": {**starter, "id": "hsp"}}, "meta": meta},
        "bullpens": {"data": {"away": bullpen, "home": bullpen}, "meta": meta},
        "lineups": {"data": {"away": {"confirmed": True, "players": _lineup()}, "home": {"confirmed": True, "players": _lineup()}}, "meta": meta},
        "injuries": {"data": {"away": [], "home": [], "awayMeta": meta, "homeMeta": meta}, "meta": meta},
        "team_context": {"data": {"away": team, "home": team}, "meta": meta},
        "weather": {"data": {"temperature": 80}, "meta": meta},
        "park": {"data": {"name": "Ballpark"}, "meta": meta},
    }


def test_complete_authoritative_resources_are_training_eligible():
    game = normalize_match(_match(), NOW, _resources())
    assert game["trainingEligible"] is True
    assert game["coverage"]["missingDomains"] == []


def test_false_strings_do_not_become_confirmed():
    resources = _resources()
    resources["pitchers"]["data"]["home"]["confirmed"] = "false"
    resources["lineups"]["data"]["away"]["confirmed"] = "false"
    game = normalize_match(_match(), NOW, resources)
    assert game["coverage"]["confirmedStarters"] is False
    assert game["coverage"]["confirmedLineups"] is False
    assert game["trainingEligible"] is False


def test_empty_injury_list_requires_authoritative_report():
    resources = _resources()
    resources["injuries"]["meta"] = {"confirmed": False, "updatedAt": None}
    resources["injuries"]["data"].pop("awayMeta")
    resources["injuries"]["data"].pop("homeMeta")
    game = normalize_match(_match(), NOW, resources)
    assert game["injuries"]["away"]["count"] == 0
    assert "injuries" in game["coverage"]["missingDomains"]
    assert game["trainingEligible"] is False


def test_duplicate_lineup_slots_and_players_fail_closed():
    resources = _resources()
    bad = _lineup()
    bad[-1] = dict(bad[0])
    resources["lineups"]["data"]["home"]["players"] = bad
    game = normalize_match(_match(), NOW, resources)
    assert game["lineups"]["home"]["uniqueSlotCount"] == 8
    assert game["lineups"]["home"]["uniquePlayerCount"] == 8
    assert game["trainingEligible"] is False


def test_bullpen_requires_quality_freshness_workload_and_availability():
    resources = _resources()
    resources["bullpens"]["data"]["away"].pop("closerAvailable")
    game = normalize_match(_match(), NOW, resources)
    assert "bullpens" in game["coverage"]["missingDomains"]


def test_team_context_requires_more_than_identity():
    resources = _resources()
    resources["team_context"]["data"]["home"] = {"id": "H", "name": "Home"}
    game = normalize_match(_match(), NOW, resources)
    assert "team_context" in game["coverage"]["missingDomains"]


def test_doubleheaders_keep_distinct_match_ids():
    first = normalize_match(_match("game-1"), NOW, _resources())
    second = normalize_match(_match("game-2"), NOW, _resources())
    assert first["matchId"] != second["matchId"]


def test_fingerprint_excludes_capture_time_and_deploy_sha():
    game_a = normalize_match(_match(), NOW, _resources())
    game_b = normalize_match(_match(), datetime(2026, 7, 29, 13, tzinfo=timezone.utc), _resources())
    a = build_snapshot([game_a], "2026-07-29", NOW, "sha-a")
    b = build_snapshot([game_b], "2026-07-29", datetime(2026, 7, 29, 14, tzinfo=timezone.utc), "sha-b")
    assert a["fingerprint"] == b["fingerprint"]


def test_immutable_write_uses_conditional_create():
    s3 = Mock()
    assert _put_immutable(s3, "bucket", "key", b"{}", "abc") == "CREATED"
    assert s3.put_object.call_args.kwargs["IfNoneMatch"] == "*"


def test_identical_retry_is_idempotent():
    s3 = Mock()
    error = {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}}
    s3.put_object.side_effect = ClientError(error, "PutObject")
    s3.head_object.return_value = {"Metadata": {"sha256": "abc"}}
    assert _put_immutable(s3, "bucket", "key", b"{}", "abc") == "EXISTING_IDENTICAL"


def test_conflicting_existing_artifact_fails_closed():
    s3 = Mock()
    error = {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}}
    s3.put_object.side_effect = ClientError(error, "PutObject")
    s3.head_object.return_value = {"Metadata": {"sha256": "different"}}
    with pytest.raises(RuntimeError, match="FINGERPRINT_CONFLICT"):
        _put_immutable(s3, "bucket", "key", b"{}", "abc")
