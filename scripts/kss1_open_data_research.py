"""Optional, non-commercial research adapter for StatsBomb Open Data.

Not a production data source. Current archive receipts cannot prove historical
availability. Raw data and fitted artifacts must not be published in the repo.
See https://github.com/statsbomb/open-data/blob/master/LICENSE.pdf.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen


def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"Accept-Encoding": "gzip"}), timeout=40) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if len(args.commit) != 40 or any(c not in "0123456789abcdef" for c in args.commit):
        raise ValueError("pin an immutable repository commit")
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    base = f"https://raw.githubusercontent.com/statsbomb/open-data/{args.commit}/"
    fixtures = []
    for competition, sport in [(2, "soccer_epl"), (11, "soccer_spain_la_liga"), (7, "soccer_france_ligue_one"), (12, "soccer_italy_serie_a")]:
        raw = fetch(base + f"data/matches/{competition}/27.json")
        receipt = hashlib.sha256(raw).hexdigest()
        for match in json.loads(raw):
            fixtures.append((match, sport, receipt))
    def convert(item):
        match, sport, receipt = item
        path = root / (str(match["match_id"]) + ".json")
        if path.exists():
            cached = json.loads(path.read_text())
            if cached.get("archive_commit") == args.commit and cached.get("source_receipt") == receipt:
                return cached
        url = base + f'data/events/{match["match_id"]}.json'
        raw = fetch(url)
        home_id, away_id = match["home_team"]["home_team_id"], match["away_team"]["away_team_id"]
        totals = {home_id: 0.0, away_id: 0.0}
        for event in json.loads(raw):
            if event.get("type", {}).get("name") == "Shot" and event.get("period", 99) <= 2:
                team = event["team"]["id"]
                if team not in totals or "statsbomb_xg" not in event.get("shot", {}):
                    raise ValueError("incomplete shot xG")
                totals[team] += float(event["shot"]["statsbomb_xg"])
        # Date-only experiment deliberately avoids claiming timezone-precise
        # historical source receipts. Two-day lag and research flag are retained.
        date = datetime.fromisoformat(match["match_date"]).replace(tzinfo=timezone.utc)
        row = {
            "event_key": f'STATSBOMB#{match["match_id"]}', "sport_key": sport,
            "home_team": match["home_team"]["home_team_name"], "away_team": match["away_team"]["away_team_name"],
            "commence_time": date.isoformat(), "available_at": (date + timedelta(days=2)).isoformat(),
            "home_score": match["home_score"], "away_score": match["away_score"],
            "home_xg": totals[home_id], "away_xg": totals[away_id],
            "xg_available_at": (date + timedelta(days=2)).isoformat(),
            "source_receipt": receipt, "xg_source_receipt": hashlib.sha256(raw).hexdigest(),
            "source_url": url, "archive_commit": args.commit,
            "archive_retrieved_at": datetime.now(timezone.utc).isoformat(),
            "provenance_mode": "DATE_ASSUMED_RESEARCH",
            "license_scope": "StatsBomb non-commercial research only",
        }
        path.write_text(json.dumps(row, sort_keys=True))
        return row
    rows = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, row in enumerate(pool.map(convert, fixtures), 1):
            rows.append(row)
            if i % 100 == 0:
                print(f"Validated {i}/{len(fixtures)} historical results and xG pairs", flush=True)
    (root / "history.json").write_text(json.dumps(rows, sort_keys=True))
    print(f"Completed {len(rows)} research rows; production use disabled", flush=True)


if __name__ == "__main__":
    main()
