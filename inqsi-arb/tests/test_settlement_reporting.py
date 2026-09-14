import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import app
from app import _audit_scan_payload, _default_jurisdiction, _regions
from arb_engine import scan_all
from ui_page import HTML
from validation import validate_event


def fresh_ts():
    return datetime.now(timezone.utc).isoformat()


def test_exchange_lay_market_is_not_mispriced_as_sportsbook_arb():
    result = scan_all({
        "bankroll": 1000,
        "events": [{
            "id": "game|h2h_lay",
            "event": "A @ B",
            "market": "h2h_lay",
            "rules_status": "unknown",
            "expected_outcomes": ["A", "B"],
            "quotes": [
                {"book": "exchange", "outcome": "A", "american": 10900},
                {"book": "exchange", "outcome": "B", "american": 10900},
            ],
        }],
    })

    assert result["n_markets"] == 1
    assert result["n_arbs"] == 0
    assert result["n_detected_unverified"] == 0
    assert result["n_exchange_pending"] == 1
    assert result["exchange_pending"][0]["validation"]["qualification_reason"] == (
        "EXCHANGE_LAY_REQUIRES_BACK_LAY_ENGINE"
    )
    assert "margin_pct" not in result["exchange_pending"][0]


def test_reviewed_profile_preserves_original_outcomes_after_books_removed():
    ts = fresh_ts()
    event = {
        "sport": "baseball_mlb",
        "market": "spreads",
        "id": "spread",
        "event": "Away @ Home",
        "expected_outcomes": ["Away +1.5", "Home -1.5", "Away +7", "Home -7"],
        "quotes": [
            {"book": "draftkings", "outcome": "Away +1.5", "decimal": 2.2, "last_update": ts},
            {"book": "fanduel", "outcome": "Home -1.5", "decimal": 2.2, "last_update": ts},
            {"book": "unknownbook", "outcome": "Away +7", "decimal": 9.0, "last_update": ts},
            {"book": "unknownbook", "outcome": "Home -7", "decimal": 9.0, "last_update": ts},
        ],
    }

    rows = validate_event(event, jurisdiction="ny")
    compatible = [row for row in rows if row["rules_status"] == "compatible"]
    assert len(compatible) == 1
    assert compatible[0]["expected_outcomes"] == [
        "Away +1.5", "Home -1.5", "Away +7", "Home -7",
    ]
    result = scan_all({"bankroll": 1000, "events": rows})
    assert result["n_arbs"] == 0
    assert result["n_rejected"] >= 1


def test_stale_third_outcome_cannot_be_erased_from_three_way_market():
    now = datetime.now(timezone.utc)
    event = {
        "sport": "baseball_mlb",
        "market": "h2h_3_way_1st_5_innings",
        "id": "three-way",
        "event": "Away @ Home",
        "expected_outcomes": ["Away", "Home", "Tie"],
        "quotes": [
            {"book": "fanatics", "outcome": "Away", "decimal": 3.2, "last_update": now.isoformat()},
            {"book": "fanatics", "outcome": "Home", "decimal": 3.2, "last_update": now.isoformat()},
            {"book": "fanatics", "outcome": "Tie", "decimal": 3.2,
             "last_update": now.replace(year=now.year - 1).isoformat()},
        ],
    }

    rows = validate_event(event, jurisdiction="ny")
    assert rows[0]["expected_outcomes"] == ["Away", "Home", "Tie"]
    result = scan_all({"bankroll": 1000, "events": rows})
    assert result["n_arbs"] == 0
    assert result["n_rejected"] == 1
    assert result["rejected"][0]["validation"]["missing_outcomes"] == ["Tie"]


def test_audit_payload_retains_held_candidate_legs_and_exact_reason():
    candidate = {
        "market_id": "game|h2h",
        "event": "A @ B",
        "market": "h2h",
        "commence_time": "2030-01-01T00:00:00Z",
        "math_arb": True,
        "arb": False,
        "sum_implied": 0.98,
        "margin_pct": 2.0408,
        "minimum_profit": 20.41,
        "validation": {
            "rules_status": "unknown",
            "rules_compatible": False,
            "qualification_reason": "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE",
            "settlement_reason": "UNREVIEWED_OR_MISSING_RULE",
            "missing_books": ["book-a"],
        },
        "legs": [
            {"outcome": "A", "book": "book-a", "american": 110, "stake": 500},
            {"outcome": "B", "book": "book-b", "american": 110, "stake": 500},
        ],
    }
    result = {
        "n_markets": 1,
        "n_arbs": 0,
        "n_detected_unverified": 1,
        "n_rejected": 0,
        "n_exchange_pending": 0,
        "hits": [],
        "detected_unverified": [candidate],
        "rejected": [],
        "exchange_pending": [],
    }

    payload = _audit_scan_payload(result, sport="baseball_mlb", jurisdiction="ny")
    saved = payload["detected_unverified"][0]
    assert payload["jurisdiction"] == "ny"
    assert saved["validation"]["settlement_reason"] == "UNREVIEWED_OR_MISSING_RULE"
    assert saved["validation"]["missing_books"] == ["book-a"]
    assert [(leg["book"], leg["american"]) for leg in saved["legs"]] == [
        ("book-a", 110), ("book-b", 110),
    ]


def test_audit_payload_reports_omitted_n_way_legs():
    candidate = {
        "market_id": "n-way", "event": "Field winner", "market": "outrights",
        "math_arb": True, "arb": False, "validation": {"rules_status": "unknown"},
        "legs": [
            {"outcome": f"runner-{i}", "book": f"book-{i}", "decimal": 10, "stake": 10}
            for i in range(7)
        ],
    }
    result = {
        "n_markets": 1, "n_arbs": 0, "n_detected_unverified": 1,
        "n_rejected": 0, "n_exchange_pending": 0, "hits": [],
        "detected_unverified": [candidate], "rejected": [], "exchange_pending": [],
    }

    saved = _audit_scan_payload(result, sport="golf", jurisdiction="ny")["detected_unverified"][0]
    assert saved["n_legs"] == 7
    assert len(saved["legs"]) == 4
    assert saved["omitted_legs"] == 3


def test_audit_payload_is_bounded_below_dynamodb_item_limit():
    long = "x" * 500
    leg = {
        "outcome": long, "book": long, "american": 110, "decimal": 2.1,
        "net_decimal": 2.1, "stake": 1, "payout_if_wins": 2,
        "profit_if_wins": 1, "last_update": long, "provider": long,
        "link": long, "limit": 1,
    }
    candidate = {
        "market_id": long, "event": long, "market": long,
        "commence_time": long, "math_arb": True, "arb": False,
        "validation": {"missing_books": [long] * 100}, "legs": [leg] * 10,
    }
    result = {
        "n_markets": 50, "n_arbs": 0, "n_detected_unverified": 50,
        "n_rejected": 0, "n_exchange_pending": 0, "hits": [],
        "detected_unverified": [candidate] * 50, "rejected": [],
        "exchange_pending": [],
    }

    payload = _audit_scan_payload(result, sport="baseball_mlb", jurisdiction="ny")
    assert len(json.dumps(payload).encode("utf-8")) < 300_000
    assert sum(len(payload[name]) for name in (
        "hits", "detected_unverified", "rejected", "exchange_pending"
    )) <= 25
    assert payload["truncated"]["detected_unverified"] == 25


def test_audit_payload_bounds_top_level_request_strings():
    result = {
        "n_markets": 0, "n_arbs": 0, "n_detected_unverified": 0,
        "n_rejected": 0, "n_held_unverified": 0, "n_exchange_pending": 0,
        "hits": [], "detected_unverified": [], "rejected": [], "exchange_pending": [],
    }
    payload = _audit_scan_payload(result, sport="s" * 500_000, jurisdiction="j" * 500_000)
    assert len(payload["sport"]) == 256
    assert len(payload["jurisdiction"]) == 256
    assert len(json.dumps(payload).encode("utf-8")) < 10_000


def test_worldwide_default_uses_all_configured_provider_regions(monkeypatch):
    monkeypatch.delenv("ARB_DEFAULT_JURISDICTION", raising=False)
    monkeypatch.delenv("ARB_REGIONS", raising=False)
    assert _default_jurisdiction() == "*"
    assert _regions("*") == "us,us2,us_dfs,us_ex,uk,eu,fr,se,au"
    assert _regions("ny") == "us,us2"
    assert _regions("ny", "us") == "us"


def test_get_scan_applies_worldwide_default_without_bookmaker_filter(monkeypatch):
    observed = {}

    def fake_scan(sport, **kwargs):
        observed.update({"sport": sport, **kwargs})
        return {"bankroll": kwargs["bankroll"], "events": [], "status": {"ok": True}}

    monkeypatch.setattr(app, "scan_sport_payload", fake_scan)
    response = app.lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {"sport": "baseball_mlb", "markets": "h2h"},
    }, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["jurisdiction"] == "*"
    assert observed["regions"] == "us,us2,us_dfs,us_ex,uk,eu,fr,se,au"
    assert observed["bookmakers"] is None


def test_embedded_ui_reports_held_and_exchange_candidates():
    assert '<option value="*" selected>Worldwide</option>' in HTML
    assert "Held-back mathematical opportunities" in HTML
    assert "exchange lay market(s) routed away" in HTML
    assert "Inspect held-back opportunities" in HTML
    assert "(j.rejected||[]).filter(x=>x.math_arb)" in HTML
    assert "el('held').innerHTML=''" in HTML
    assert "j.n_held_unverified??held.length" in HTML
