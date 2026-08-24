#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_bbd_pro_context as bbd  # noqa: E402


REQUIRED_FILES = (
    "hello_world/mlb_official_schedule_authority.py",
    "hello_world/mlb_canonical_final_labels_v1.py",
    "hello_world/mlb_bbd_pro_context.py",
    "hello_world/mlb_autonomous_llm_decision_v1.py",
    "hello_world/mlb_autonomous_orchestrator_v1.py",
    "hello_world/mlb_daily_card_deadline_v1.py",
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _static_verify() -> Dict[str, Any]:
    for relative in REQUIRED_FILES:
        _assert((ROOT / relative).is_file(), f"missing required file: {relative}")

    official = _read("hello_world/mlb_official_schedule_authority.py")
    finals = _read("hello_world/mlb_canonical_final_labels_v1.py")
    bbd_source = _read("hello_world/mlb_bbd_pro_context.py")
    llm_source = _read("hello_world/mlb_autonomous_llm_decision_v1.py")
    orchestrator = _read("hello_world/mlb_autonomous_orchestrator_v1.py")
    timing = _read("hello_world/mlb_daily_card_deadline_v1.py")
    template = _read("template.yaml")
    deploy = _read(".github/workflows/deploy.yml")

    _assert("statsapi.mlb.com/api/v1/schedule" in official, "MLB Stats API schedule authority missing")
    _assert("MLB Stats API exact-date official FINAL" in finals, "official FINAL-label authority missing")
    _assert("BIG_BALLS_DATA_API_KEY" in bbd_source, "BBD Pro key integration missing")
    _assert("discover_openapi" in bbd_source and "build_pregame_slate_context" in bbd_source, "BBD OpenAPI client incomplete")
    _assert("THE_ODDS_API_MARKET_AUTHORITY" in llm_source, "The Odds API market context missing from LLM decision layer")
    _assert("BIG_BALLS_DATA_PRO_SUPPLEMENTAL_BASEBALL_CONTEXT" in llm_source, "BBD context missing from LLM decision layer")
    _assert("MLB_STATS_API_OFFICIAL_IDENTITY_SCHEDULE_RESULT_AUTHORITY" in llm_source, "MLB official authority missing from LLM decision layer")
    _assert("ALL_GAMES_NO_PASS" in llm_source, "all-games prediction policy missing")
    _assert("0.70" in llm_source or "DAILY_ACCURACY_TARGET" in llm_source, "70% accuracy objective missing")
    _assert("rate(5 minutes)" in template, "five-minute autonomous orchestration schedule missing")
    _assert("MLBAutonomousOrchestratorFunction" in template, "autonomous orchestrator resource missing")
    _assert("BigBallsDataApiKey" in template and "BIG_BALLS_DATA_API_KEY" in template, "BBD secret not wired to SAM")
    _assert("bedrock:InvokeModel" in template, "Bedrock invoke permission missing")
    _assert("FIRST_GAME_PICK_BY_T45_AND_FULL_CARD_BY_SECOND_GAME_T45" in orchestrator, "second-game T-45 policy missing")
    _assert("second_game_start_utc" in timing and "full_card_deadline_utc" in timing, "deadline calculation missing")
    _assert("BIG_BALLS_DATA_API_KEY" in deploy, "BBD repository secret not wired in deployment")
    _assert("verify_mlb_no_bbd_runtime.py" not in deploy, "obsolete no-BBD deployment guard is still active")
    _assert("verify_mlb_three_source_authority.py" in deploy, "three-source deploy verifier not active")

    wrapped_files: List[str] = []
    for path in HELLO_WORLD.glob("mlb*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "MLB-MULTISOURCE-AUTONOMY-WRAPPER-v1" in text:
            ast.parse(text)
            wrapped_files.append(str(path.relative_to(ROOT)))
    _assert(wrapped_files, "no production predictor was wrapped with the autonomous LLM decision layer")

    return {
        "ok": True,
        "officialAuthority": True,
        "oddsAuthority": True,
        "bbdProContext": True,
        "bedrockDecisionLayer": True,
        "autonomousFiveMinuteSchedule": True,
        "secondGameT45Deadline": True,
        "allGamesNoPass": True,
        "dailyAccuracyGoal": 0.70,
        "wrappedPredictors": wrapped_files,
    }


def _live_bbd_smoke() -> Dict[str, Any]:
    api_key = bbd.api_key_from_env()
    _assert(bool(api_key), "Big Balls Data Pro repository secret is missing")
    discovery = bbd.discover_openapi(api_key=api_key, force=True)
    _assert(discovery.get("ok") is True, f"BBD OpenAPI discovery failed: {discovery.get('errors')}")
    operations = [row for row in bbd.openapi_operations(discovery) if row.get("pregameSafe")]
    _assert(bool(operations), "BBD OpenAPI has no pregame-safe MLB operations")

    spec = discovery["spec"]
    base_url = bbd._base_url(spec, discovery["url"])
    slate_date = datetime.now(timezone.utc).date().isoformat()
    sample_game = {
        "game_date_et": slate_date,
        "home_team": "New York Yankees",
        "away_team": "Boston Red Sox",
        "official_game_pk": "deployment-smoke",
    }
    successes: List[Dict[str, Any]] = []
    attempts: List[Dict[str, Any]] = []
    for operation in operations:
        required = {
            str(parameter.get("name") or "").lower()
            for parameter in operation.get("parameters") or []
            if parameter.get("required") is True
        }
        if any(any(token in name.replace("_", "") for token in ("eventid", "matchid", "gameid", "teamid")) or name == "id" for name in required):
            continue
        url, headers, unresolved = bbd._build_url(
            base_url,
            operation,
            spec=spec,
            api_key=api_key,
            slate_date=slate_date,
            game=sample_game,
            crosswalk={},
        )
        if not url:
            attempts.append({"path": operation["path"], "status": "unresolved", "unresolved": unresolved})
            continue
        try:
            payload = bbd._default_get_json(url, headers, bbd.DEFAULT_TIMEOUT_SECONDS)
            successes.append(
                {
                    "path": operation["path"],
                    "operationId": operation["operation"].get("operationId"),
                    "payloadType": type(payload).__name__,
                    "payloadFingerprint": bbd._fingerprint(payload),
                }
            )
            break
        except Exception as exc:
            attempts.append(
                {
                    "path": operation["path"],
                    "httpStatus": getattr(exc, "code", None),
                    "errorType": type(exc).__name__,
                    "error": str(exc)[:300],
                }
            )
            if getattr(exc, "code", None) in {401, 403}:
                break
        if len(attempts) >= 12:
            break
    _assert(bool(successes), f"BBD Pro live authentication/MLB smoke failed: {attempts}")
    return {
        "ok": True,
        "openapiUrl": discovery.get("url"),
        "openapiFingerprint": discovery.get("fingerprint"),
        "mlbPathCount": discovery.get("mlbPathCount"),
        "successfulOperation": successes[0],
        "attemptsBeforeSuccess": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    result: Dict[str, Any] = {
        "version": "MLB-THREE-SOURCE-AUTHORITY-VERIFIER-v1",
        "checkedAtUtc": datetime.now(timezone.utc).isoformat(),
        "static": _static_verify(),
        "liveBbd": None,
    }
    if args.require_live:
        result["liveBbd"] = _live_bbd_smoke()
    result["ok"] = bool(result["static"].get("ok") and (not args.require_live or (result["liveBbd"] or {}).get("ok")))
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        pathlib.Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
