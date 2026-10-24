"""Regression tests for SNS opportunity qualification, identity, and deduplication."""
import json
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
        return {"MessageId": "harmless-test-id"}


class FakeBoto:
    def __init__(self, client):
        self._client = client

    def client(self, name):
        assert name == "sns"
        return self._client


def _verified_row():
    return {
        "sport": "baseball_mlb",
        "event_id": "event-1",
        "market": "h2h",
        "arb": True,
        "math_arb": True,
        "executable": True,
        "margin_pct": 1.25,
        "minimum_profit": 12.50,
        "allocated_stake": 1000.00,
        "legs": [
            {"outcome": "home", "book": "book-a", "american": 115},
            {"outcome": "away", "book": "book-b", "american": 105},
        ],
        "validation": {
            "outcome_coverage": "complete",
            "rules_compatible": True,
            "executable": True,
            "settlement_states": {"strict_arbitrage": True, "minimum_net_pnl": 12.50},
        },
    }


def _setup(monkeypatch):
    sns = FakeSns()
    monkeypatch.setenv("ARB_OPPORTUNITY_TOPIC_ARN", "arn:aws:sns:us-east-1:123:arb")
    monkeypatch.setattr(alerts, "boto3", FakeBoto(sns))
    monkeypatch.setattr(alerts, "claim_once", lambda *args, **kwargs: True)
    return sns


def test_verified_and_held_candidates_publish_immediately(monkeypatch):
    sns = _setup(monkeypatch)
    held = {
        "sport": "basketball_nba", "event_id": "event-2",
        "market": "spreads", "reason": "RULE_UNKNOWN", "legs": [],
    }
    out = alerts.publish_scan_opportunities(
        {"hits": [_verified_row()], "detected_unverified": [held]},
        audit_event_id="audit-1",
    )
    assert out == {"published": 2, "deduped": 0, "errors": 0}
    messages = [json.loads(call["Message"]) for call in sns.calls]
    assert messages[0]["status"] == "VERIFIED"
    assert messages[0]["audit_event_id"] == "audit-1"
    assert messages[0]["margin"] == 1.25
    assert messages[0]["profit"] == 12.50
    assert messages[0]["stake_total"] == 1000.00
    assert messages[1]["status"] == "HELD_BACK"
    assert messages[1]["holdback_reason"] == "RULE_UNKNOWN"
    assert all(msg["places_bets"] is False for msg in messages)


def test_unverified_hit_is_held_even_if_math_positive(monkeypatch):
    sns = _setup(monkeypatch)
    row = _verified_row()
    row["validation"]["rules_compatible"] = False
    row["validation"]["qualification_reason"] = "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"
    out = alerts.publish_scan_opportunities({"hits": [row]})
    assert out["published"] == 1
    msg = json.loads(sns.calls[0]["Message"])
    assert msg["status"] == "HELD_BACK"
    assert msg["holdback_reason"] == "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"


def test_missing_settlement_proof_is_held(monkeypatch):
    sns = _setup(monkeypatch)
    row = _verified_row()
    row["validation"].pop("settlement_states")
    alerts.publish_scan_opportunities({"hits": [row]})
    msg = json.loads(sns.calls[0]["Message"])
    assert msg["status"] == "HELD_BACK"
    assert msg["holdback_reason"] == "EXECUTABLE_QUALIFICATION_NOT_PROVEN"


def test_non_executable_or_incomplete_proof_is_held():
    for field, value in [
        ("arb", False),
        ("math_arb", False),
        ("executable", False),
    ]:
        row = _verified_row()
        row[field] = value
        assert not alerts._is_verified_executable(row)
    row = _verified_row()
    row["validation"]["outcome_coverage"] = "incomplete"
    assert not alerts._is_verified_executable(row)
    row = _verified_row()
    row["validation"]["executable"] = False
    assert not alerts._is_verified_executable(row)
    row = _verified_row()
    row["validation"]["settlement_states"]["strict_arbitrage"] = False
    assert not alerts._is_verified_executable(row)
    row = _verified_row()
    row["validation"]["qualification_reason"] = "RULE_UNKNOWN"
    assert not alerts._is_verified_executable(row)


def test_identical_opportunity_is_deduped(monkeypatch):
    sns = _setup(monkeypatch)
    monkeypatch.setattr(alerts, "claim_once", lambda *args, **kwargs: False)
    out = alerts.publish_scan_opportunities({"hits": [_verified_row()]})
    assert out == {"published": 0, "deduped": 1, "errors": 0}
    assert sns.calls == []


def test_status_transition_changes_alert_identity():
    row = _verified_row()
    assert alerts._identity("HELD_BACK", row) != alerts._identity("VERIFIED", row)


def test_native_american_odds_and_margin_changes_realert():
    original = _verified_row()
    same = _verified_row()
    assert alerts._identity("VERIFIED", original) == alerts._identity("VERIFIED", same)
    price_changed = _verified_row()
    price_changed["legs"][0]["american"] = 120
    assert alerts._identity("VERIFIED", original) != alerts._identity("VERIFIED", price_changed)
    margin_changed = _verified_row()
    margin_changed["margin_pct"] = 1.5
    assert alerts._identity("VERIFIED", original) != alerts._identity("VERIFIED", margin_changed)


def test_zero_native_fields_are_not_discarded():
    row = _verified_row()
    row["margin_pct"] = 0
    row["minimum_profit"] = 0
    row["allocated_stake"] = 0
    row["legs"][0]["american"] = 0
    msg = alerts._message("HELD_BACK", row, audit_event_id=None)
    assert msg["margin"] == 0
    assert msg["profit"] == 0
    assert msg["stake_total"] == 0
    assert alerts._identity("HELD_BACK", row) != alerts._identity("HELD_BACK", _verified_row())


def test_scan_sport_and_detection_time_survive_alert_emission(monkeypatch):
    """Engine rows omit sport; preserve scan context in SMS and dedupe identity."""
    sns = _setup(monkeypatch)
    row = _verified_row()
    row.pop("sport")
    row["legs"][0]["last_update"] = "2026-10-24T21:00:00Z"
    out = alerts.publish_scan_opportunities({"hits": [row]}, sport="baseball_mlb")
    assert out == {"published": 1, "deduped": 0, "errors": 0}
    msg = json.loads(sns.calls[0]["Message"])
    assert msg["sport"] == "baseball_mlb"
    assert msg["detected_at"]
    assert msg["quote_last_updates"] == ["2026-10-24T21:00:00Z"]
    assert "sport" not in row  # no mutation of the auditable scan candidate
    assert alerts._identity("VERIFIED", {**row, "sport": "baseball_mlb"}) != alerts._identity(
        "VERIFIED", {**row, "sport": "tennis_atp"}
    )



def test_sns_message_id_is_logged_as_publish_proof(monkeypatch, capsys):
    sns = _setup(monkeypatch)
    out = alerts.publish_scan_opportunities({"hits": [_verified_row()]})
    assert out == {"published": 1, "deduped": 0, "errors": 0}
    log = capsys.readouterr().out
    proof = json.loads(log.strip().splitlines()[-1])
    assert proof["event"] == "ARB_SNS_PUBLISH_CONFIRMED"
    assert proof["message_id"] == "harmless-test-id"
    assert proof["status"] == "VERIFIED"
    assert len(proof["identity"]) == 64
    assert len(sns.calls) == 1


def test_missing_sns_message_id_is_not_reported_as_success(monkeypatch, capsys):
    sns = _setup(monkeypatch)
    sns.publish = lambda **kwargs: {}
    out = alerts.publish_scan_opportunities({"hits": [_verified_row()]})
    assert out == {"published": 0, "deduped": 0, "errors": 1}
    assert "ARB_SNS_PUBLISH_CONFIRMED" not in capsys.readouterr().out
