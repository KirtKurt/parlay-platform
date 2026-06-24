import json
import os

import inqsi_pull_history as history


def main():
    sports = os.environ.get("INQSI_AUTO_BUILD_SPORTS", "mlb,wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis")
    build_fn = getattr(history, "auto_" + "parlay_builds")
    latest_fn = getattr(history, "latest_" + "parlay_build")
    result = build_fn({"sports": sports, "store": True})
    latest_mlb = latest_fn({"sport": "mlb"})
    built = [row.get("sport") for row in result.get("results", []) if row.get("buildStatus") == "BUILT"]
    report = {
        "ok": True,
        "sports": [s.strip() for s in sports.split(",") if s.strip()],
        "builtSports": built,
        "mlbBuilt": "mlb" in built,
        "mlbLatestBuild": latest_mlb,
        "result": result,
    }
    os.makedirs("runtime_reports", exist_ok=True)
    with open("runtime_reports/parlay_latest.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
        f.write("\n")
    print(json.dumps({"ok": report["ok"], "builtSports": built, "mlbBuilt": report["mlbBuilt"]}, indent=2))


if __name__ == "__main__":
    main()
