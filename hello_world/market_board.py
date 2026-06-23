import html
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

try:
    import inqsi_pull_history
except Exception:
    inqsi_pull_history = None

DEFAULT_SPORTS = ["nfl", "cfb", "mlb", "college_baseball_men", "nba", "wnba", "ncaam", "nhl", "tennis", "soccer"]

DEFAULT_SLATE_WINDOW_DAYS = {
    "mlb": 2,
    "college_baseball_men": 2,
    "nba": 2,
    "wnba": 2,
    "ncaam": 2,
    "ncaaw": 2,
    "nhl": 2,
    "nfl": 7,
    "cfb": 7,
    "college_football_men": 7,
    "soccer": 14,
    "tennis": 7,
}
ACTIVE_WINDOW_BACK_BUFFER_HOURS = 6
CANONICAL_ALIASES = {
    "ncaaf": "cfb",
    "college_football": "cfb",
    "college_football_men": "cfb",
    "college_fb": "cfb",
    "ncaa_football": "cfb",
}


def clean(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def response(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body": json.dumps(clean(body)),
    }


def html_response(status: int, body: str) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "text/html; charset=utf-8", "access-control-allow-origin": "*"},
        "body": body,
    }


def params(event: Dict[str, Any]) -> Dict[str, Any]:
    return event.get("queryStringParameters") or {}


def sport_key(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in CANONICAL_ALIASES:
        return CANONICAL_ALIASES[raw]
    if raw == "cfb":
        return "cfb"
    if inqsi_pull_history is not None:
        try:
            return inqsi_pull_history.sport_key(raw)
        except Exception:
            pass
    return raw


def sports_from(value: Any) -> List[str]:
    if not value:
        return DEFAULT_SPORTS
    return [sport_key(s) for s in str(value).split(",") if s.strip()]


def parse_time(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def active_window(sport: str) -> Tuple[datetime, datetime, int]:
    sport = sport_key(sport)
    days = int(DEFAULT_SLATE_WINDOW_DAYS.get(sport, 2))
    current = datetime.now(timezone.utc)
    return current - timedelta(hours=ACTIVE_WINDOW_BACK_BUFFER_HOURS), current + timedelta(days=days), days


def game_in_active_window(game: Dict[str, Any], sport: str) -> bool:
    start, end, _ = active_window(sport)
    commence = parse_time(game.get("commence_time") or game.get("commenceTime"))
    if commence is None:
        return False
    return start <= commence <= end


def latest_pull_for(sport: str, slate_date: Optional[str] = None) -> Dict[str, Any]:
    if inqsi_pull_history is None:
        return {"ok": False, "sport": sport, "error": "pull_history_module_unavailable"}
    sport = sport_key(sport)
    pulls = inqsi_pull_history.query_pulls(sport, slate_date, 500)
    latest_visible = None
    for pull in reversed(pulls):
        games = pull.get("games") or []
        if any(game_in_active_window(g, sport) for g in games):
            latest_visible = pull
            break
    return {"ok": True, "sport": sport, "pullCount": len(pulls), "latestPull": latest_visible, "rawLatestPull": pulls[-1] if pulls else None}


def book_market(book_name: str, book: Dict[str, Any]) -> Dict[str, Any]:
    ml = book.get("ml") or book.get("moneyline") or {}
    return {
        "book": book_name,
        "moneyline": {
            "home": ml.get("home"),
            "away": ml.get("away"),
        },
        "spread": book.get("spread"),
        "total": book.get("total"),
        "overUnder": book.get("total"),
    }


def game_row(game: Dict[str, Any]) -> Dict[str, Any]:
    books = game.get("books") or {}
    markets = [book_market(name, data) for name, data in sorted(books.items()) if isinstance(data, dict)]
    return {
        "gameId": game.get("game_id") or game.get("id"),
        "gameKey": game.get("game_key"),
        "homeTeam": game.get("home_team"),
        "awayTeam": game.get("away_team"),
        "commenceTime": game.get("commence_time"),
        "league": game.get("league"),
        "level": game.get("level"),
        "gender": game.get("gender"),
        "providerSportKey": game.get("provider_sport_key"),
        "bookCount": len(markets),
        "books": markets,
    }


def board_for_sport(sport: str, slate_date: Optional[str] = None) -> Dict[str, Any]:
    sport = sport_key(sport)
    latest = latest_pull_for(sport, slate_date)
    if not latest.get("ok"):
        return latest
    pull = latest.get("latestPull") or {}
    raw_latest = latest.get("rawLatestPull") or {}
    start, end, days = active_window(sport)
    active_games = [g for g in (pull.get("games") or []) if game_in_active_window(g, sport)]
    games = [game_row(g) for g in active_games]
    return {
        "ok": True,
        "sport": sport,
        "slate_date": pull.get("slate_date") or slate_date,
        "pullCount": latest.get("pullCount", 0),
        "latestPulledAt": pull.get("pulled_at"),
        "rawLatestPulledAt": raw_latest.get("pulled_at"),
        "source": pull.get("source"),
        "providerSportKey": pull.get("provider_sport_key"),
        "gameCount": len(games),
        "activeWindowStart": start.isoformat(),
        "activeWindowEnd": end.isoformat(),
        "slateWindowDays": days,
        "marketTypes": ["moneyline", "spread", "total", "over_under"],
        "games": games,
        "note": "Market board applies active-slate filtering and reads only playable-window provider pulls. Member-uploaded slips are not included.",
    }


def board_all(p: Dict[str, Any]) -> Dict[str, Any]:
    slate_date = p.get("slate_date")
    sports = sports_from(p.get("sports") or p.get("sport"))
    boards = [board_for_sport(s, slate_date) for s in sports]
    return {
        "ok": True,
        "board": "market_board_active_slate_latest_pull",
        "sportsChecked": sports,
        "sportsWithGames": sum(1 for b in boards if (b.get("gameCount") or 0) > 0),
        "memberSlipsIncluded": False,
        "boards": boards,
    }


def render_book(book: Dict[str, Any]) -> str:
    ml = book.get("moneyline") or {}
    spread = book.get("spread") or {}
    total = book.get("total") or {}
    return f"""
    <div class='book'>
      <div class='book-name'>{html.escape(str(book.get('book') or 'book'))}</div>
      <div class='odds-row'><span>ML Home</span><b>{html.escape(str(ml.get('home') or '-'))}</b></div>
      <div class='odds-row'><span>ML Away</span><b>{html.escape(str(ml.get('away') or '-'))}</b></div>
      <div class='odds-row'><span>Spread</span><b>{html.escape(str(spread or '-'))}</b></div>
      <div class='odds-row'><span>O/U</span><b>{html.escape(str(total or '-'))}</b></div>
    </div>
    """


def render_page(board: Dict[str, Any]) -> str:
    sport = html.escape(str(board.get("sport") or "all"))
    cards = []
    for game in board.get("games", []) or []:
        books = "".join(render_book(b) for b in game.get("books", [])[:24])
        cards.append(f"""
        <section class='card'>
          <div class='game-top'><span>{html.escape(str(game.get('awayTeam') or 'Away'))} @ {html.escape(str(game.get('homeTeam') or 'Home'))}</span><small>{html.escape(str(game.get('commenceTime') or ''))}</small></div>
          <div class='meta'>Provider: {html.escape(str(game.get('providerSportKey') or '-'))} · Books: {html.escape(str(game.get('bookCount') or 0))}</div>
          <div class='books'>{books}</div>
        </section>
        """)
    body = "".join(cards) or "<div class='empty'>No active-slate games yet for this sport.</div>"
    return f"""
    <!doctype html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Inqis Market Board</title>
    <style>
      body{{margin:0;background:#080b12;color:#f4f7fb;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}}
      .wrap{{max-width:1120px;margin:0 auto;padding:24px}}
      h1{{margin:0 0 8px;font-size:28px}} .sub{{color:#8b98aa;margin-bottom:18px}}
      .card{{background:#111827;border:1px solid #263244;border-radius:18px;padding:16px;margin:14px 0;box-shadow:0 20px 60px rgba(0,0,0,.22)}}
      .game-top{{display:flex;justify-content:space-between;gap:12px;font-weight:800;font-size:18px}}
      .game-top small{{color:#8b98aa;font-weight:600;font-size:12px;text-align:right}}
      .meta{{color:#9aa7b8;font-size:12px;margin:8px 0 14px}}
      .books{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}
      .book{{background:#0b1020;border:1px solid #263244;border-radius:14px;padding:12px}}
      .book-name{{font-weight:800;margin-bottom:8px;text-transform:capitalize}}
      .odds-row{{display:flex;justify-content:space-between;color:#aeb9c8;font-size:13px;padding:4px 0}}
      .odds-row b{{color:#f7fafc}} .empty{{padding:24px;background:#111827;border-radius:16px;color:#9aa7b8}}
    </style></head><body><div class='wrap'>
      <h1>Inqis Market Board · {sport}</h1>
      <div class='sub'>Latest active pull: {html.escape(str(board.get('latestPulledAt') or 'none'))} · Games: {html.escape(str(board.get('gameCount') or 0))}</div>
      {body}
    </div></body></html>
    """


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/markets") or path.startswith("/v1/markets")):
        return response(200, {"ok": True})
    if method != "GET":
        return None
    p = params(event)
    if path in {"/v1/inqsi/markets/board", "/v1/markets/board"}:
        return response(200, board_all(p))
    if path in {"/v1/inqsi/markets/board/page", "/v1/markets/board/page"}:
        sport = sport_key(p.get("sport") or "mlb")
        return html_response(200, render_page(board_for_sport(sport, p.get("slate_date"))))
    return None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    routed = route(event)
    return routed or response(404, {"ok": False, "error": "not_found"})
