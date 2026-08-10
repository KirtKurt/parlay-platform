from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_results_scheduler as subject


def _body(response):
    return json.loads(response["body"])


def test_direct_event_slate_date_alias_binds_exact_canonical_settlement(monkeypatch):
    calls = []

    def settle_mlb_slate(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "slateDateEt": kwargs["slate_date"],
            "slateFinalized": True,
            "settledLabelCount": 8,
        }

    monkeypatch.setattr(
        subject.canonical_settlement,
        "settle_mlb_slate",
        settle_mlb_slate,
    )
    monkeypatch.setattr(
        subject,
        "build_signal_learning_report",
        lambda **kwargs: {"ok": True, "slateDateEt": kwargs.get("slate_date")},
    )
    monkeypatch.setattr(
        subject,
        "build_result_signals",
        lambda slate_date, **kwargs: {"ok": True, "slateDateEt": slate_date},
    )

    response = subject.lambda_handler(
        {
            "sport": "mlb",
            "run": "prospective_backlog_settlement_v4",
            "slate_date": "2026-08-04",
            "days_from": 0,
        },
        None,
    )

    assert response["statusCode"] == 200
    assert calls == [
        {
            "slate_date": "2026-08-04",
            "days_from": 3,
            "fetch_scores": True,
            "store": True,
        }
    ]
    assert _body(response)["slateDateEt"] == "2026-08-04"


def test_canonical_date_precedence_is_stable_across_event_aliases():
    payload = subject._payload(
        {
            "slate_date_et": "2026-08-03",
            "slate_date": "2026-08-04",
            "date": "2026-08-05",
            "days_from": 1,
        }
    )

    assert subject._settlement_args(payload) == {
        "slate_date": "2026-08-03",
        "days_from": 1,
        "fetch_scores": True,
    }


def test_query_or_body_date_cannot_be_overwritten_by_top_level_legacy_alias():
    payload = subject._payload(
        {
            "queryStringParameters": {"date": "2026-08-06"},
            "slate_date": "2026-08-04",
        }
    )

    assert subject._settlement_args(payload)["slate_date"] == "2026-08-04"
    assert payload["date"] == "2026-08-06"
