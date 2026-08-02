from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SOURCE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def fetch(date: str) -> dict:
    query = urllib.parse.urlencode(
        {"sportId": "1", "startDate": date, "endDate": date, "hydrate": "linescore"}
    )
    url = f"{SOURCE_URL}?{query}"
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": "inqsi-mlb-slate-diagnostic/1.0"},
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = fetch(args.date)
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
        "secretExposed": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
