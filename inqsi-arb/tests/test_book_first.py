import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import validation  # noqa: F401
from app import lambda_handler
from provider_books import catalog_summary, regions_for_books
from quote_store import put_snapshot, reset_memory
from ui_page import HTML


def fresh_ts():
    return datetime.now(timezone.utc).isoformat()


def future_ts():
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def test_catalog_is_books_not_states():
    summary = catalog_summary()
    assert summary["product_filter"] == "books"
    assert "licensed_states" not in summary["books"][0]
    assert "n_licensed_states" not in summary["books"][0]
    assert "state packs" not in summary["policy"].lower()
    assert "users choose sportsbooks" in summary["policy"].lower()


def test_books_route_has_no_state_taxonomy():
    response = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/books"}, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["product_filter"] == "books"
    assert all("licensed_states" not in row for row in body["books"])
    assert any(row["key"] == "draftkings" for row in body["books"])


def test_health_declares_book_first_desk():
    body = json.loads(lambda_handler({"httpMethod": "GET", "path": "/v1/arb/health"}, None)["body"])
    assert body["book_first_desk"] is True
    assert body["product_filter"] == "books"
    assert body["user_book_filter"] is True
    assert body["default_jurisdiction"] == "*"


def test_licensed_and_jurisdiction_do_not_filter_books(monkeypatch):
    observed = {}

    def fake_scan(sport, **kwargs):
        observed.update({"sport": sport, **kwargs})
        return {"bankroll": kwargs["bankroll"], "events": [], "status": {"ok": True}}

    reset_memory()
    monkeypatch.setattr("app.scan_sport_payload", fake_scan)
    response = lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb",
            "markets": "h2h",
            "jurisdiction": "fl",
            "licensed": "true",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert "pack" not in body
    assert body["product_filter"] == "books"
    assert body["books"] is None
    assert observed["bookmakers"] is None
    assert observed["regions"] == "us,us2,us_dfs,us_ex,uk,eu,fr,se,au"


def test_books_query_is_the_only_product_filter(monkeypatch):
    observed = {}

    def fake_scan(sport, **kwargs):
        observed.update({"sport": sport, **kwargs})
        return {"bankroll": kwargs["bankroll"], "events": [], "status": {"ok": True}}

    reset_memory()
    monkeypatch.setattr("app.scan_sport_payload", fake_scan)
    response = lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb",
            "markets": "h2h",
            "books": "draftkings,fanduel,pinnacle",
            "licensed": "true",
            "jurisdiction": "ny",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["books"] == "draftkings,fanduel,pinnacle"
    assert observed["bookmakers"] == "draftkings,fanduel,pinnacle"
    assert "pack" not in body


def test_store_scan_respects_user_book_filter():
    reset_memory()
    put_snapshot("baseball_mlb", [{
        "id": "stored", "event_id": "stored", "event": "A @ B", "sport": "baseball_mlb",
        "market": "h2h", "commence_time": future_ts(), "expected_outcomes": ["A", "B"],
        "quotes": [
            {"book": "draftkings", "outcome": "A", "decimal": 2.2, "last_update": fresh_ts()},
            {"book": "fanduel", "outcome": "B", "decimal": 2.2, "last_update": fresh_ts()},
            {"book": "pinnacle", "outcome": "B", "decimal": 3.0, "last_update": fresh_ts()},
        ],
    }], meta={"ok": True, "markets": ["h2h"], "regions": "us,us2,us_dfs,us_ex,uk,eu,fr,se,au"})
    open_body = json.loads(lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {"sport": "baseball_mlb", "markets": "h2h", "source": "store"},
    }, None)["body"])
    filtered = json.loads(lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb", "markets": "h2h", "source": "store",
            "books": "draftkings,fanduel",
        },
    }, None)["body"])
    open_books = {leg["book"] for row in (open_body.get("detected_unverified") or []) for leg in row.get("legs") or []}
    filtered_books = {leg["book"] for row in (filtered.get("detected_unverified") or []) for leg in row.get("legs") or []}
    assert "pinnacle" in open_books
    assert filtered_books == {"draftkings", "fanduel"}
    assert filtered["books"] == "draftkings,fanduel"
    reset_memory()


def test_ui_is_book_picker_not_state_picker():
    assert "Settlement scope" not in HTML
    assert "State books only" not in HTML
    assert "Arizona" not in HTML
    assert "New York" not in HTML
    assert "/v1/arb/books" in HTML
    assert "selectedBooks()" in HTML
    assert "p.set('books',selected.join(','))" in HTML
    assert 'id="bookKeys"' in HTML
    assert "checked.concat(manual)" in HTML


def test_multi_region_book_uses_region_alternatives():
    assert regions_for_books("betonlineag") == [{"us", "eu"}]
    reset_memory()
    put_snapshot("baseball_mlb", [], meta={"ok": True, "markets": ["h2h"], "regions": "us,us2"})
    response = lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb", "markets": "h2h", "source": "store",
            "books": "betonlineag",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["source"] == "store"
    reset_memory()


def test_explicit_region_is_required_for_selected_book_snapshot(monkeypatch):
    observed = {}
    reset_memory()
    put_snapshot("baseball_mlb", [], meta={"ok": True, "markets": ["h2h"], "regions": "eu"})

    def fake_scan(sport, **kwargs):
        observed.update({"sport": sport, **kwargs})
        return {"bankroll": kwargs["bankroll"], "events": [], "status": {"ok": True}}

    monkeypatch.setattr("app.scan_sport_payload", fake_scan)
    response = lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb", "markets": "h2h", "source": "auto",
            "books": "betonlineag", "regions": "us",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["source"] == "live"
    assert observed["regions"] == "us"
    reset_memory()


def test_explicit_region_must_intersect_selected_book_region(monkeypatch):
    observed = {}
    reset_memory()
    put_snapshot("baseball_mlb", [], meta={
        "ok": True, "markets": ["h2h"], "regions": "us,eu",
    })

    def fake_scan(sport, **kwargs):
        observed.update({"sport": sport, **kwargs})
        return {"bankroll": kwargs["bankroll"], "events": [], "status": {"ok": True}}

    monkeypatch.setattr("app.scan_sport_payload", fake_scan)
    response = lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb", "markets": "h2h", "source": "auto",
            "books": "pinnacle", "regions": "us",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["source"] == "live"
    assert observed["bookmakers"] == "pinnacle"
    assert observed["regions"] == "us"
    reset_memory()


def test_auto_bypasses_us_snapshot_for_international_book(monkeypatch):
    observed = {}
    reset_memory()
    put_snapshot("baseball_mlb", [], meta={"ok": True, "markets": ["h2h"], "regions": "us,us2"})

    def fake_scan(sport, **kwargs):
        observed.update({"sport": sport, **kwargs})
        return {"bankroll": kwargs["bankroll"], "events": [], "status": {"ok": True}}

    monkeypatch.setattr("app.scan_sport_payload", fake_scan)
    response = lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb", "markets": "h2h", "source": "auto",
            "books": "pinnacle",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["source"] == "live"
    assert observed["bookmakers"] == "pinnacle"
    assert observed["regions"] == "us,us2,us_dfs,us_ex,uk,eu,fr,se,au"


def test_auto_bypasses_us_snapshot_for_unfiltered_worldwide_scan(monkeypatch):
    observed = {}
    reset_memory()
    put_snapshot("baseball_mlb", [], meta={"ok": True, "markets": ["h2h"], "regions": "us,us2"})

    def fake_scan(sport, **kwargs):
        observed.update({"sport": sport, **kwargs})
        return {"bankroll": kwargs["bankroll"], "events": [], "status": {"ok": True}}

    monkeypatch.setattr("app.scan_sport_payload", fake_scan)
    response = lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {"sport": "baseball_mlb", "markets": "h2h", "source": "auto"},
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["source"] == "live"
    assert observed["bookmakers"] is None


def test_all_sports_auto_falls_live_when_any_snapshot_region_is_ineligible(monkeypatch):
    calls = []
    reset_memory()
    monkeypatch.setattr("app.list_sports", lambda all_sports=False: ([
        {"key": "sport_one"}, {"key": "sport_two"},
    ], {"ok": True}))
    put_snapshot("sport_one", [], meta={"ok": True, "markets": ["h2h"], "regions": "us,us2,us_dfs,us_ex,uk,eu,fr,se,au"})
    put_snapshot("sport_two", [], meta={"ok": True, "markets": ["h2h"], "regions": "us,us2"})

    def fake_scan(sport, **kwargs):
        calls.append(sport)
        return {"bankroll": kwargs["bankroll"], "events": [], "status": {"ok": True}}

    monkeypatch.setattr("app.scan_sport_payload", fake_scan)
    response = lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {"sport": "all", "markets": "h2h", "source": "auto"},
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["source"] == "live"
    assert calls == ["sport_one", "sport_two"]
    reset_memory()


def test_ui_restores_entity_escaping():
    assert "'&':'&amp;'" in HTML
    assert "'<':'&lt;'" in HTML
    static = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "'&':'&amp;'" in static
    assert "'<':'&lt;'" in static


def test_ui_disambiguates_book_keys_and_history_jurisdiction():
    assert "b.key+(b.regions?.length" in HTML
    assert "jurisdiction '+esc(jurisdiction)" in HTML
    static = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "b.key+(b.regions?.length" in static
    assert "Settlement scope" not in static
    assert 'id="jurisdiction"' not in static
    assert "jurisdiction:$('jurisdiction').value" not in static
    assert "provider_only" in static


def test_store_reports_missing_market_dimension_separately():
    reset_memory()
    put_snapshot("baseball_mlb", [], meta={
        "ok": True, "markets": ["h2h"], "regions": "us",
    })
    response = lambda_handler({
        "httpMethod": "GET", "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb", "markets": "totals", "regions": "us",
            "source": "store",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 503
    assert body["error"] == "QUOTE_SNAPSHOT_MARKET_MISMATCH"
    assert body["requested_markets"] == ["totals"]
    assert body["snapshot_markets"] == ["h2h"]
    reset_memory()


def test_book_picker_storage_is_best_effort():
    static = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    for page in (HTML, static):
        assert "function savedBooks(){try{" in page
        assert "Array.isArray(value)?value:[]" in page
        assert "try{localStorage.setItem" in page
        assert "catch(_){}" in page


def test_selected_books_are_filtered_before_settlement_validation(monkeypatch):
    observed = []

    def capture(rows, *, jurisdiction):
        observed.extend(rows)
        return []

    monkeypatch.setattr("app.validate_events", capture)
    monkeypatch.setattr("app.scan_sport_payload", lambda *args, **kwargs: {
        "events": [{
            "id": "mixed", "market": "h2h", "quotes": [
                {"book": "draftkings", "outcome": "A", "decimal": 2.2},
                {"book": "pinnacle", "outcome": "B", "decimal": 2.2},
            ],
        }],
        "status": {"ok": True},
    })
    response = lambda_handler({
        "httpMethod": "GET", "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb", "markets": "h2h", "source": "live",
            "books": "draftkings",
        },
    }, None)
    assert response["statusCode"] == 200
    assert [quote["book"] for quote in observed[0]["quotes"]] == ["draftkings"]


def test_posted_books_are_filtered_before_settlement_validation(monkeypatch):
    observed = []

    def capture(rows, *, jurisdiction):
        observed.extend(rows)
        return []

    monkeypatch.setattr("app.validate_events", capture)
    response = lambda_handler({
        "httpMethod": "POST", "path": "/v1/arb/scan",
        "body": json.dumps({
            "books": "draftkings,fanduel",
            "events": [{
                "id": "mixed", "market": "h2h", "quotes": [
                    {"book": "draftkings", "outcome": "A", "decimal": 2.2},
                    {"book": "fanduel", "outcome": "B", "decimal": 2.2},
                    {"book": "unknown", "outcome": "B", "decimal": 3.0},
                ],
            }],
        }),
    }, None)
    assert response["statusCode"] == 200
    assert [quote["book"] for quote in observed[0]["quotes"]] == ["draftkings", "fanduel"]
    assert json.loads(response["body"])["books"] == "draftkings,fanduel"


def test_saved_books_restore_synchronously_before_catalog_fetch():
    static = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    for page in (HTML, static):
        assert "function restoreSavedBooks()" in page
        assert page.rfind("restoreSavedBooks();") < page.rfind("loadBooks();")
