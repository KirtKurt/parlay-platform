import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import sportsbook_catalog
import sportsbook_api


def test_audit_deduplicates_books_and_tracks_sports(monkeypatch):
    sports = [
        {"key": "sport_a", "title": "Sport A", "has_outrights": False},
        {"key": "sport_b", "title": "Sport B", "has_outrights": False},
    ]
    monkeypatch.setattr(sportsbook_catalog, "list_sports", lambda all_sports=False: (sports, {"ok": True}))

    def fake_scan(sport, *, regions, markets):
        if sport["key"] == "sport_a":
            books = [{"key": "book_one", "title": "Book One"}, {"key": "book_two", "title": "Book Two"}]
        else:
            books = [{"key": "book_one", "title": "Book One"}, {"key": "book_three", "title": "Book Three"}]
        return {"ok": True, "sport": sport["key"], "title": sport["title"], "sportsbooks": books}

    monkeypatch.setattr(sportsbook_catalog, "_scan_one_sport", fake_scan)
    books, meta = sportsbook_catalog.audit_sportsbooks(regions="us,uk", workers=2)
    assert meta["complete"] is True
    assert meta["sport_count"] == 2
    assert len(books) == 3
    one = next(row for row in books if row["key"] == "book_one")
    assert one["sport_count"] == 2
    assert one["sports"] == ["sport_a", "sport_b"]


def test_audit_is_incomplete_when_any_sport_fails(monkeypatch):
    sports = [
        {"key": "sport_a", "title": "Sport A", "has_outrights": False},
        {"key": "sport_b", "title": "Sport B", "has_outrights": False},
    ]
    monkeypatch.setattr(sportsbook_catalog, "list_sports", lambda all_sports=False: (sports, {"ok": True}))

    def fake_scan(sport, *, regions, markets):
        if sport["key"] == "sport_b":
            return {"ok": False, "sport": "sport_b", "error": "FAIL"}
        return {"ok": True, "sport": "sport_a", "sportsbooks": [{"key": "book_one", "title": "Book One"}]}

    monkeypatch.setattr(sportsbook_catalog, "_scan_one_sport", fake_scan)
    books, meta = sportsbook_catalog.audit_sportsbooks(workers=2)
    assert len(books) == 1
    assert meta["complete"] is False
    assert meta["sports_failed"] == 1


def test_api_returns_count_and_complete_flag(monkeypatch):
    monkeypatch.setattr(sportsbook_api, "audit_sportsbooks", lambda **kwargs: (
        [{"key": "a", "title": "A", "sports": ["x"], "sport_count": 1}],
        {"ok": True, "complete": True, "regions": "us", "sport_count": 1, "sports_scanned": 1, "sports_failed": 0},
    ))
    response = sportsbook_api.lambda_handler({"httpMethod": "GET", "queryStringParameters": None}, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["sportsbook_count"] == 1
    assert body["sportsbooks"][0]["key"] == "a"
