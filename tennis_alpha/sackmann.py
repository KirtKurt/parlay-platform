from __future__ import annotations

import csv
import io
import urllib.request
from typing import Iterator

ARCHIVE = "https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/main"
ATP_MIRROR = "https://raw.githubusercontent.com/elitemajik-ship-it/tennis_atp/master"
INCOMPLETE_TAGS = ("RET", "W/O", "WALKOVER", "DEF", "ABD", "DEFAULT")


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "tennis-alpha/1.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def _urls(tour: str, year: int) -> list[str]:
    tour = tour.lower()
    if tour == "atp":
        return [
            f"{ARCHIVE}/atp/atp_matches_{year}.csv",
            f"{ATP_MIRROR}/atp_matches_{year}.csv",
        ]
    if tour == "wta":
        return [f"{ARCHIVE}/wta/wta_matches_{year}.csv"]
    raise ValueError("tour must be atp or wta")


def is_complete_match(row: dict) -> bool:
    score = str(row.get("score") or "").strip().upper()
    if not score:
        return False
    compact = score.replace(" ", "").replace(".", "")
    if compact in {"WO", "W/O"} or score in {"W/O", "WO"}:
        return False
    if "W/O" in score or compact.endswith("WO"):
        return False
    return not any(tag in score for tag in INCOMPLETE_TAGS)


def tour_matches(tour: str, year: int) -> list[dict]:
    last_err: Exception | None = None
    for url in _urls(tour, year):
        try:
            text = _fetch(url)
            return [row for row in csv.DictReader(io.StringIO(text)) if is_complete_match(row)]
        except Exception as exc:
            last_err = exc
            continue
    raise RuntimeError(f"failed to load {tour} {year}: {last_err}")


def iter_years(tour: str, start: int, end: int) -> Iterator[dict]:
    for year in range(start, end + 1):
        try:
            rows = tour_matches(tour, year)
        except Exception:
            continue
        for row in rows:
            row["_tour"] = tour
            row["_year"] = year
            yield row
