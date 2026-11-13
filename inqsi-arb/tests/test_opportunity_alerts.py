import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import opportunity_alerts as alerts


class FakeSns:
    def __init__(self):
        self.calls = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": "m1"}


class FakeBoto:
    def __init__(self, client):
        self._client = client

    def client(self, name):
        assert name == "sns"
        return self._client


def test_verified_and_held_candidates_publish_immediately(monkeypatch):
    sns = FakeSns()
    monkeypatch.setenv("ARB_OPPORTUNITY_TOPIC_ARN", "arn:aws:sns:us-east-1:123:arb")
    monkeypatch.setattr(alerts, "boto3", FakeBoto(sns))
    monkeypatch.setattr(alerts, "claim_once", lambda *args, **kwargs: True)
    result = {
        "hits": [{"sport": "baseball_mlb", "event_id": "e1", "market": "h2h", "margin": 0.02, "arb": True, "executable": True, "validation": {"rules_compatible": True, "outcome_coverage": "complete", "settlement_states": {"strict_arbitrage": True}}, "legs": []}],
        "detected_unverified": [{"sport": "basketball_nba", "event_id": "e2", "market": "spreads", "reason": "RULE_UNKNOWN", "legs": []}],
    }
    out = alerts.publish_scan_opportunities(result, audit_event_id="audit-1")
    assert out == {"published": 2, "deduped": 0, "errors": 0}
    assert '"status": "VERIFIED"' in sns.calls[0]["Message"]
    assert '"audit_event_id": "audit-1"' in sns.calls[0]["Message"]
    assert '"status": "HELD_BACK"' in sns.calls[1]["Message"]


def test_identical_opportunity_is_deduped(monkeypatch):
    sns = FakeSns()
    monkeypatch.setenv("ARB_OPPORTUNITY_TOPIC_ARN", "arn:aws:sns:us-east-1:123:arb")
    monkeypatch.setattr(alerts, "boto3", FakeBoto(sns))
    monkeypatch.setattr(alerts, "claim_once", lambda *args, **kwargs: False)
    out = alerts.publish_scan_opportunities({"hits": [{"event_id": "e1", "market": "h2h"}]})
    assert out == {"published": 0, "deduped": 1, "errors": 0}
    assert sns.calls == []


def test_status_transition_changes_alert_identity():
    row = {"event_id": "e1", "market": "h2h", "margin": 0.01, "legs": [{"book": "a", "price": 110}]}
    assert alerts._identity("HELD_BACK", row) != alerts._identity("VERIFIED", row)


def test_unverified_engine_hit_is_held_back_not_claimed_verified(monkeypatch):
    sns = FakeSns()
    monkeypatch.setenv("ARB_OPPORTUNITY_TOPIC_ARN", "arn:aws:sns:us-east-1:123:arb")
    monkeypatch.setattr(alerts, "boto3", FakeBoto(sns))
    monkeypatch.setattr(alerts, "claim_once", lambda *args, **kwargs: True)
    row = {
        "event": "A @ B", "market_id": "e1|h2h", "market": "h2h",
        "arb": True, "math_arb": True, "executable": True,
        "margin_pct": 2.34, "minimum_profit": 2.34, "allocated_stake": 100,
        "validation": {
            "rules_compatible": False, "outcome_coverage": "complete",
            "qualification_reason": "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE",
            "settlement_states": {"strict_arbitrage": True},
        },
        "legs": [{"outcome": "A", "book": "draftkings", "american": 120, "last_update": "2026-10-23T09:00:00Z"}],
    }
    assert alerts._status("hits", row) == "HELD_BACK"
    out = alerts.publish_scan_opportunities({"hits": [row]}, sport="baseball_mlb")
    assert out == {"published": 1, "deduped": 0, "errors": 0}
    import json
    msg = json.loads(sns.calls[0]["Message"])
    assert msg["classification"] == "HELD_BACK"
    assert msg["sport"] == "baseball_mlb"
    assert msg["holdback_reason"] == "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"
    assert msg["margin_pct"] == 2.34
    assert msg["profit"] == 2.34
    assert msg["stake_total"] == 100
    assert msg["legs"][0]["american"] == 120
    assert msg["quote_timestamps"] == ["2026-10-23T09:00:00Z"]


def test_real_engine_odds_and_margin_change_realerts():
    row = {
        "event": "A @ B", "market_id": "e1|h2h", "market": "h2h",
        "margin_pct": 1.25,
        "legs": [{"outcome": "A", "book": "draftkings", "american": 120}],
    }
    original = alerts._identity("HELD_BACK", row)
    assert original == alerts._identity("HELD_BACK", dict(row))
    assert original != alerts._identity("HELD_BACK", {**row, "margin_pct": 1.5})
    assert original != alerts._identity("HELD_BACK", {
        **row, "legs": [{"outcome": "A", "book": "draftkings", "american": 125}]
    })
    assert original != alerts._identity("VERIFIED", row)


def test_no_verification_without_explicit_proofs():
    row = {"arb": True, "executable": True, "validation": {
        "rules_compatible": True, "outcome_coverage": "complete"
    }}
    assert alerts._status("hits", row) == "HELD_BACK"
    row["validation"]["settlement_states"] = {"strict_arbitrage": True}
    assert alerts._status("hits", row) == "VERIFIED"
    row["validation"]["rules_compatible"] = False
    assert alerts._status("hits", row) == "HELD_BACK"


def test_sns_publish_without_message_id_is_not_counted_as_delivered(monkeypatch):
    class NoAckSns(FakeSns):
        def publish(self, **kwargs):
            self.calls.append(kwargs)
            return {}

    sns = NoAckSns()
    monkeypatch.setenv("ARB_OPPORTUNITY_TOPIC_ARN", "arn:aws:sns:us-east-1:123:arb")
    monkeypatch.setattr(alerts, "boto3", FakeBoto(sns))
    monkeypatch.setattr(alerts, "claim_once", lambda *args, **kwargs: True)
    out = alerts.publish_scan_opportunities({"detected_unverified": [
        {"event_id": "e1", "market": "h2h", "margin_pct": 1.2}
    ]})
    assert out == {"published": 0, "deduped": 0, "errors": 1}
    assert len(sns.calls) == 1
