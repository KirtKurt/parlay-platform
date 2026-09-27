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
        "hits": [{"sport": "baseball_mlb", "event_id": "e1", "market": "h2h", "margin": 0.02, "legs": []}],
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
