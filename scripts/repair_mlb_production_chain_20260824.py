#!/usr/bin/env python3
"""Repair MLB AUTO BBD UTC-slate coverage and isolated v3 verifier scope.

This script is intentionally idempotent and narrow. It does not change any
prediction, settlement, table, model, or promotion authority. It patches source
and deployment regression coverage only; production mutation remains the job of
the normal reviewed deployment workflows.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _replace_once(path: str, old: str, new: str, label: str) -> bool:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    if new in source:
        print(f"{label}: already applied")
        return False
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")
    print(f"{label}: applied")
    return True


def _write_if_changed(path: str, content: str, label: str) -> bool:
    target = ROOT / path
    old = target.read_text(encoding="utf-8") if target.exists() else None
    if old == content:
        print(f"{label}: already current")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"{label}: written")
    return True


def repair() -> bool:
    changed = False

    old_bbs = '''def _bbs_matches(slate: str) -> Dict[str, Any]:
    payload = _bbs_get("/v1/matches", {"sport": "baseball", "league": "mlb", "date": slate, "limit": 100})
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        raise RuntimeError("BBS_MATCHES_NOT_LIST")
    return {"source": "Big Balls Sports Data", "events": rows, "meta": payload.get("meta") or {}}
'''
    new_bbs = '''def _bbs_payload_rows(payload: Any) -> List[Dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("BBS_MATCHES_NOT_LIST")
    return [copy.deepcopy(row) for row in rows if isinstance(row, dict)]


def _bbs_event_identity(row: Dict[str, Any]) -> str:
    for key in (
        "id", "match_id", "matchId", "event_id", "eventId",
        "fixture_id", "fixtureId", "game_id", "gameId", "uuid",
    ):
        if row.get(key):
            return f"id:{row[key]}"
    home = _team_name(row.get("home") or row.get("home_team") or row.get("homeTeam"))
    away = _team_name(row.get("away") or row.get("away_team") or row.get("awayTeam"))
    start = (
        row.get("kickoff_utc")
        or row.get("start_time")
        or row.get("startTime")
        or row.get("commence_time")
        or row.get("commenceTime")
        or row.get("scheduled_at")
        or row.get("scheduledAt")
        or row.get("game_date")
        or row.get("gameDate")
        or row.get("date")
    )
    return "fallback:" + json.dumps(
        [_normalize(home), _normalize(away), str(start or "")],
        separators=(",", ":"),
    )


def _dedupe_bbs_events(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        identity = _bbs_event_identity(row)
        if identity in seen:
            continue
        seen.add(identity)
        events.append(copy.deepcopy(row))
    return events


def _official_utc_dates(slate: str, official: Dict[str, Any]) -> List[str]:
    values = set()
    for game in official.get("games") or []:
        if not isinstance(game, dict):
            continue
        start = _parse(game.get("gameDate"))
        if start is not None:
            values.add(start.date().isoformat())
    return sorted(values or {slate})


def _bbs_official_coverage(
    official: Dict[str, Any], rows: Iterable[Dict[str, Any]]
) -> Tuple[List[str], List[str]]:
    matched: List[str] = []
    missing: List[str] = []
    for game in official.get("games") or []:
        if not isinstance(game, dict):
            continue
        game_pk = str(game.get("gamePk") or "")
        if _match_event(game, rows, provider="bbs") is not None:
            matched.append(game_pk)
        else:
            missing.append(game_pk)
    return matched, missing


def _bbs_matches(
    slate: str, official: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    official = official if isinstance(official, dict) else {"games": []}
    utc_dates = _official_utc_dates(slate, official)
    rows: List[Dict[str, Any]] = []
    queries: List[Dict[str, Any]] = []
    provider_meta: List[Dict[str, Any]] = []

    def collect(label: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        payload = _bbs_get("/v1/matches", params)
        found = _bbs_payload_rows(payload)
        rows.extend(found)
        queries.append({
            "label": label,
            "params": copy.deepcopy(params),
            "count": len(found),
        })
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if isinstance(meta, dict):
            provider_meta.append(copy.deepcopy(meta))
        return found

    # BBD's documented date filter is UTC. An Eastern MLB slate can therefore
    # span two UTC dates, so use the official MLB gameDate values as the query
    # authority rather than treating the ET slate date as a UTC date.
    for date_value in utc_dates:
        collect(
            "official_utc_date",
            {
                "sport": "baseball",
                "league": "mlb",
                "date": date_value,
                "limit": 200,
                "offset": 0,
            },
        )

    events = _dedupe_bbs_events(rows)
    matched, missing = _bbs_official_coverage(official, events)
    fallback_used = False

    # The documented unfiltered league view is a bounded recovery path for a
    # delayed/misdated provider row. A row is admitted only after the existing
    # official team/start-time crosswalk succeeds.
    for offset in (0, 200, 400):
        if not missing:
            break
        fallback_used = True
        found = collect(
            "league_unfiltered_offset",
            {
                "sport": "baseball",
                "league": "mlb",
                "limit": 200,
                "offset": offset,
            },
        )
        events = _dedupe_bbs_events(rows)
        matched, missing = _bbs_official_coverage(official, events)
        if len(found) < 200:
            break

    return {
        "source": "Big Balls Sports Data",
        "events": events,
        "meta": {
            "resolver": "official_utc_date_union_v1",
            "officialUtcDates": utc_dates,
            "queries": queries,
            "providerMeta": provider_meta,
            "unfilteredFallbackUsed": fallback_used,
            "expectedOfficialGameCount": len(official.get("games") or []),
            "matchedOfficialGameCount": len(matched),
            "missingOfficialGamePks": missing,
        },
    }
'''
    changed |= _replace_once(
        "mlb_auto_llm/handler.py",
        old_bbs,
        new_bbs,
        "BBD official UTC-date union resolver",
    )
    changed |= _replace_once(
        "mlb_auto_llm/handler.py",
        "    bbs = _bbs_matches(slate)\n",
        "    bbs = _bbs_matches(slate, official)\n",
        "BBD resolver receives official schedule",
    )

    changed |= _replace_once(
        "scripts/verify_mlb_deploy_identity.py",
        '''ISOLATED_THREE_SOURCE_HANDLERS = (
    ISOLATED_THREE_SOURCE_HANDLER,
    "orchestrator_v2.lambda_handler",
)
''',
        '''ISOLATED_THREE_SOURCE_HANDLERS = (
    ISOLATED_THREE_SOURCE_HANDLER,
    "orchestrator_v2.lambda_handler",
    "orchestrator_v3.lambda_handler",
)
''',
        "root verifier isolated v3 handler scope",
    )

    bbd_test = '''from __future__ import annotations

import os
import sys
from pathlib import Path

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
'''
    changed |= _write_if_changed(
        "tests/unit/test_mlb_auto_bbd_utc_date_resolver.py",
        bbd_test,
        "BBD resolver regression tests",
    )

    verifier_test = '''from __future__ import annotations

from scripts import verify_mlb_deploy_identity as verifier


def _function(*, handler: str = "orchestrator_v3.lambda_handler", root_table: bool = False) -> dict:
    environment = {
        "MLB_AUTO_TABLE": "isolated-table",
        "ODDS_API_KEY": "configured",
        "BBS_API_SECRET_ARN": (
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:isolated-mlb-auto"
        ),
        "MLB_AUTO_FIRST_GAME_SAFETY_MINUTES": "10",
        "MLB_AUTO_BEDROCK_MODELS": "us.amazon.nova-lite-v1:0",
    }
    if root_table:
        environment["SNAPSHOTS_TABLE"] = "root-snapshots"
    return {
        "FunctionName": "parlay-platform-mlb-auto-llm-MLBAutoLLMFunction-AbCd1234",
        "FunctionArn": (
            "arn:aws:lambda:us-east-1:123456789012:function:"
            "parlay-platform-mlb-auto-llm-MLBAutoLLMFunction-AbCd1234"
        ),
        "Handler": handler,
        "Environment": {"Variables": environment},
    }


def test_orchestrator_v3_is_positive_isolated_boundary() -> None:
    function = _function()
    assert verifier._is_authorized_isolated_three_source_auto(function) is True
    assert verifier._root_authority_lambda_functions([function]) == []


def test_orchestrator_v3_with_root_table_is_not_exempted() -> None:
    function = _function(root_table=True)
    assert verifier._is_authorized_isolated_three_source_auto(function) is False
    assert verifier._root_authority_lambda_functions([function]) == [function]
'''
    changed |= _write_if_changed(
        "tests/unit/test_mlb_deploy_identity_isolated_v3.py",
        verifier_test,
        "isolated v3 verifier regression tests",
    )

    changed |= _replace_once(
        ".github/workflows/deploy-mlb-auto-llm.yml",
        '''          PYTHONPATH=mlb_auto_llm python -m pytest -q \\
            tests/unit/test_mlb_auto_model_gateway.py \\
            tests/unit/test_mlb_auto_ml_authority.py
''',
        '''          PYTHONPATH=mlb_auto_llm python -m pytest -q \\
            tests/unit/test_mlb_auto_bbd_utc_date_resolver.py \\
            tests/unit/test_mlb_auto_model_gateway.py \\
            tests/unit/test_mlb_auto_ml_authority.py
''',
        "MLB AUTO deploy BBD resolver regression test",
    )
    changed |= _replace_once(
        ".github/workflows/deploy.yml",
        '''            tests/unit/test_mlb_deploy_identity.py
            tests/unit/test_mlb_deploy_http_probe.py
''',
        '''            tests/unit/test_mlb_deploy_identity.py
            tests/unit/test_mlb_deploy_identity_isolated_v3.py
            tests/unit/test_mlb_deploy_http_probe.py
''',
        "root deploy isolated v3 regression test",
    )

    return changed


def main() -> int:
    changed = repair()
    print("MLB production chain source repaired" if changed else "MLB production chain source already repaired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
