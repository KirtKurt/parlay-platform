from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import mlb_canonical_final_labels_v1 as labels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    schedule = labels.fetch_official_schedule(args.date, timeout=30)
    report = {
        "proofType": "MLB_CANONICAL_SLATE_DATE_DIAGNOSTIC",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "slateDateEt": args.date,
        "officialGameCount": schedule.get("officialGameCount"),
        "officialFinalCount": schedule.get("officialFinalCount"),
        "allFinal": bool(schedule.get("officialGameCount") and schedule.get("officialGameCount") == schedule.get("officialFinalCount")),
        "games": [
            {
                "officialGamePk": game.get("officialGamePk"),
                "gameDate": game.get("gameDate"),
                "awayTeam": game.get("awayTeam"),
                "homeTeam": game.get("homeTeam"),
                "awayScore": game.get("awayScore"),
                "homeScore": game.get("homeScore"),
                "completed": game.get("completed"),
                "officialStatus": game.get("officialStatus"),
            }
            for game in schedule.get("games") or []
        ],
        "sourceUrl": schedule.get("sourceUrl"),
        "secretExposed": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
