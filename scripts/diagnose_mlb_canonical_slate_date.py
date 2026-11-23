from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def deployed_trainer_environment(deadline_seconds: int = 1200) -> tuple[dict, dict]:
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if not region:
        return {}, {}
    import boto3

    cf = boto3.client("cloudformation", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    deadline = time.monotonic() + max(1, deadline_seconds)
    while True:
        stack = cf.describe_stacks(StackName="parlay-platform-dev")["Stacks"][0]
        outputs = {
            row["OutputKey"]: row["OutputValue"]
            for row in stack.get("Outputs", [])
        }
        function_name = outputs["MLBMLTrainingFunctionArn"]
        config = lam.get_function_configuration(FunctionName=function_name)
        variables = dict(((config.get("Environment") or {}).get("Variables") or {}))
        if (
            variables.get("SNAPSHOTS_TABLE")
            and variables.get("OUTCOMES_TABLE")
            and config.get("LastUpdateStatus") == "Successful"
        ):
            return config, variables
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "MLB_TRAINER_TABLE_WIRING_NOT_DEPLOYED_WITHIN_DEADLINE"
            )
        time.sleep(15)


def fetch(date: str) -> dict:
    query = urllib.parse.urlencode(
        {"sportId": "1", "startDate": date, "endDate": date, "hydrate": "linescore"}
    )
    url = f"{SOURCE_URL}?{query}"
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": "inqsi-mlb-slate-diagnostic/1.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    games = []
    for date_row in payload.get("dates") or []:
        for raw in date_row.get("games") or []:
            teams = raw.get("teams") or {}
            away = teams.get("away") or {}
            home = teams.get("home") or {}
            status = raw.get("status") or {}
            games.append(
                {
                    "officialGamePk": str(raw.get("gamePk") or ""),
                    "gameDate": raw.get("gameDate"),
                    "awayTeam": ((away.get("team") or {}).get("name")),
                    "homeTeam": ((home.get("team") or {}).get("name")),
                    "awayScore": away.get("score"),
                    "homeScore": home.get("score"),
                    "completed": str(status.get("abstractGameState") or "").upper() == "FINAL",
                    "officialStatus": {
                        "abstractGameState": status.get("abstractGameState"),
                        "codedGameState": status.get("codedGameState"),
                        "statusCode": status.get("statusCode"),
                        "detailedState": status.get("detailedState"),
                        "reason": status.get("reason"),
                    },
                }
            )
    return {"url": url, "totalGames": payload.get("totalGames"), "games": games}


def safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(
                    token in str(key).lower()
                    for token in (
                        "secret",
                        "password",
                        "credential",
                        "api_key",
                        "apikey",
                        "authorization",
                    )
                )
                else safe(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [safe(item) for item in value]
    return value


def authoritative_settlement(date: str, variables: dict) -> tuple[dict, dict, dict]:
    if not variables:
        return {}, {}, {"initialized": False}
    for key, value in variables.items():
        os.environ[str(key)] = str(value)
    for name in (
        "mlb_canonical_final_labels_v1",
        "inqsi_pull_history",
        "mlb_rolling_24h_audit",
        "mlb_daily_per_game_lock_patch",
        "mlb_doubleheader_safe_audit_patch",
        "mlb_slate_coverage_patch",
    ):
        sys.modules.pop(name, None)
    labels = importlib.import_module("mlb_canonical_final_labels_v1")
    history = importlib.import_module("inqsi_pull_history")
    initialization = {
        "initialized": True,
        "snapshotsVariablePresent": bool(os.environ.get("SNAPSHOTS_TABLE")),
        "outcomesVariablePresent": bool(os.environ.get("OUTCOMES_TABLE")),
        "historySnapshotsNamePresent": bool(getattr(history, "SNAPSHOTS_TABLE", "")),
        "historyTableHandleReady": getattr(history, "PULLS", None) is not None,
        "outcomesTableHandleReady": getattr(labels, "outcomes_tbl", None) is not None,
    }
    settlement = labels.settle_mlb_slate(
        slate_date=date,
        fetch_scores=True,
        store=True,
    )
    training = labels.load_canonical_training_rows(slate_date=date)
    return settlement, training, initialization


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config, variables = deployed_trainer_environment()
    source = fetch(args.date)
    settlement, training, initialization = authoritative_settlement(
        args.date, variables
    )
    games = source["games"]
    finals = sum(game["completed"] is True for game in games)
    report = {
        "proofType": "MLB_CANONICAL_SLATE_DATE_DIAGNOSTIC",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "slateDateEt": args.date,
        "officialGameCount": len(games),
        "officialFinalCount": finals,
        "allFinal": bool(games and finals == len(games)),
        "games": games,
        "sourceUrl": source["url"],
        "trainerTableWiringObserved": bool(variables),
        "trainerHandler": config.get("Handler") if config else None,
        "trainerLastModified": config.get("LastModified") if config else None,
        "moduleInitialization": initialization,
        "canonicalSettlement": safe(settlement),
        "canonicalTrainingReadback": safe(training),
        "secretExposed": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
