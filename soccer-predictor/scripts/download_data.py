#!/usr/bin/env python3
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from _common import ROOT, fail_main
from soccer_predictor.settings import sources
from soccer_predictor.io import TeamNames, csv_records, normalize_results, atomic_bytes, write_json, sha

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)


def download_csv(url: str, timeout: int = 30, attempts: int = 3) -> bytes:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            request = Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
            })
            with urlopen(request, timeout=timeout) as response:
                body = response.read()
            if not body:
                raise RuntimeError(f"Empty response from {url}")
            head = body[:256].lstrip().lower()
            if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
                raise RuntimeError(f"Expected CSV but received HTML from {url}")
            return body
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
    raise RuntimeError(f"Download failed after {attempts} attempts: {url}: {last_error}") from last_error


def main():
    parser = argparse.ArgumentParser(description="Download football-data.co.uk domestic CSV history; no API key required")
    parser.add_argument("--refresh", action="store_true", help="Refresh archives as well as the active season")
    parser.add_argument("--out", default=str(ROOT / "data/raw"))
    args = parser.parse_args()
    target = Path(args.out)
    target.mkdir(parents=True, exist_ok=True)
    names = TeamNames.load(ROOT / "config/teams.yaml")
    manifest = []
    for season, div in sources():
        url = f"https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
        path = target / season / f"{div}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        cached = path.exists() and path.stat().st_size > 0 and season != "2627" and not args.refresh
        if cached:
            body = path.read_bytes()
        else:
            print(f"Downloading {url}")
            body = download_csv(url)
        rows = normalize_results(csv_records(body), names, div, season)
        if not cached:
            atomic_bytes(path, body)
        manifest.append({"season": season, "division": div, "url": url, "sha256": sha(body), "completed_rows": len(rows), "cache_hit": cached})
        print(f"{season}/{div}: {len(rows)} completed matches ({'cache' if cached else 'download'})")
    write_json(target / "manifest.json", {"fetched_at": datetime.now(timezone.utc).isoformat(), "expected_files": len(sources()), "files": manifest})
    print(f"Validated {len(manifest)} CSV files. Raw files are ignored by git.")


if __name__ == "__main__":
    fail_main(main)
