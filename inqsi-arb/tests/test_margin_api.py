import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app import lambda_handler


def _event(method, path, *, query=None, body=None):
    return {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": query or {},
        "body": json.dumps(body) if body is not None else None,
    }


def test_margins_payload_ranks_books():
    event = _event("POST", "/v1/arb/margins", body={
        "sport": "baseball_mlb",
        "events": [{
            "event": "A @ B",
            "market": "h2h",
            "expected_outcomes": ["A", "B"],
            "quotes": [
                {"outcome": "A", "book": "pinnacle", "american": -105},
                {"outcome": "B", "book": "pinnacle", "american": -105},
                {"outcome": "A", "book": "draftkings", "american": -110},
                {"outcome": "B", "book": "draftkings", "american": -110},
            ],
        }],
    })
    response = lambda_handler(event, None)
    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert payload["ok"] is True
    assert payload["source"] == "payload"
    assert payload["n_complete"] == 2
    assert payload["books"][0]["book"] == "pinnacle"
    assert payload["books"][0]["mean_hold_pct"] < payload["books"][-1]["mean_hold_pct"]


def test_health_advertises_margin_analysis():
    response = lambda_handler(_event("GET", "/v1/arb/health"), None)
    payload = json.loads(response["body"])
    assert payload["sportsbook_margin_analysis"] is True


def test_margins_rejects_bad_source():
    response = lambda_handler(_event("GET", "/v1/arb/margins", query={"source": "nope"}), None)
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"] == "INVALID_SOURCE"
