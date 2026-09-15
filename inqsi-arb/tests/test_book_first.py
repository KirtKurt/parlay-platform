import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import validation  # noqa: F401
from app import lambda_handler
from provider_books import catalog_summary
from quote_store import put_snapshot, reset_memory
from ui_page import HTML


def fresh_ts():
    return datetime.now(timezone.utc).isoformat()


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
        "market": "h2h", "expected_outcomes": ["A", "B"],
        "quotes": [
            {"book": "draftkings", "outcome": "A", "decimal": 2.2, "last_update": fresh_ts()},
            {"book": "fanduel", "outcome": "B", "decimal": 2.2, "last_update": fresh_ts()},
            {"book": "pinnacle", "outcome": "B", "decimal": 3.0, "last_update": fresh_ts()},
        ],
    }], meta={"ok": True})
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
