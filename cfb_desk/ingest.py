"""ESPN FBS ingest for the college engine only."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen


USER_AGENT = "cfb-desk/1.0"
ERRORS: list[str] = []


def fetch_json(url: str) -> dict | None:
    request = Request(url, headers={"accept": "application/json", "user-agent": USER_AGENT, "referer": "https://www.espn.com/college-football/scoreboard"})
    try:
        with urlopen(request, timeout=20) as response:
            if response.status != 200:
                if len(ERRORS) < 3:
                    ERRORS.append(f"HTTP_{response.status}")
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        if len(ERRORS) < 3:
            ERRORS.append(f"{type(exc).__name__}:{str(exc)[:160]}")
        return None
    return payload if isinstance(payload, dict) else None


def parse_payload(payload: dict | None) -> list[dict]:
    games: list[dict] = []
    for event in (payload or {}).get("events") or []:
        competitions = event.get("competitions") or []
        competition = competitions[0] if competitions else None
        if not competition:
            continue
        competitors = competition.get("competitors") or []
        home = next((team for team in competitors if team.get("homeAway") == "home"), None)
        away = next((team for team in competitors if team.get("homeAway") == "away"), None)
        season = (event.get("season") or {}).get("year")
        week = (event.get("week") or {}).get("number")
        if not home or not away or not event.get("id") or not event.get("date") or not season or not week:
            continue
        completed = ((competition.get("status") or {}).get("type") or {}).get("completed") is True
        def score_of(team: dict) -> float | None:
            if not completed:
                return None
            try:
                value = float(team.get("score"))
            except (TypeError, ValueError):
                return None
            return value if value == value else None
        home_team = home.get("team") or {}
        away_team = away.get("team") or {}
        games.append({
            "id": str(event["id"]),
            "season": int(season),
            "week": int(week),
            "kickoff": str(event["date"]),
            "home": home_team.get("abbreviation") or home_team.get("shortDisplayName") or "HOME",
            "away": away_team.get("abbreviation") or away_team.get("shortDisplayName") or "AWAY",
            "homeName": home_team.get("shortDisplayName") or home_team.get("displayName") or "Home",
            "awayName": away_team.get("shortDisplayName") or away_team.get("displayName") or "Away",
            "homeScore": score_of(home),
            "awayScore": score_of(away),
            "completed": completed,
            "neutral": competition.get("neutralSite") is True,
            "homeConference": str(home_team.get("conferenceId") or ""),
            "awayConference": str(away_team.get("conferenceId") or ""),
        })
    return games


def load_cfb_season() -> dict:
    ERRORS.clear()
    current = fetch_json("https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80&limit=400")
    season = int(((current or {}).get("season") or {}).get("year") or 2026)
    week = int(((current or {}).get("week") or {}).get("number") or 1)
    jobs: list[tuple[int, int]] = []
    for year in (2024, 2025, 2026):
        last = week if year == season else 15
        for index in range(1, last + 1):
            jobs.append((year, index))
    def page(job: tuple[int, int]) -> list[dict]:
        year, index = job
        payload = fetch_json(
            "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
            f"?seasontype=2&week={index}&dates={year}&groups=80&limit=400"
        )
        return parse_payload(payload)

    games: list[dict] = []
    seen: set[str] = set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        pages = pool.map(page, jobs)
    for page_games in pages:
        for game in page_games:
            if game["id"] in seen:
                continue
            seen.add(game["id"])
            games.append(game)
    return {"games": games, "season": season, "week": week, "errors": list(ERRORS)}
