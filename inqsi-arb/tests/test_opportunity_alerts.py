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


def test_sns_client_initialization_failure_preserves_scan_and_does_not_claim(monkeypatch, capsys):
    class BrokenBoto:
        def client(self, name):
            assert name == "sns"
            raise RuntimeError("sns-client-not-configured")

    claimed = []
    monkeypatch.setenv("ARB_OPPORTUNITY_TOPIC_ARN", "arn:aws:sns:us-east-1:123:arb")
    monkeypatch.setattr(alerts, "boto3", BrokenBoto())
    monkeypatch.setattr(alerts, "claim_once", lambda *args, **kwargs: claimed.append(args) or True)
    result = alerts.publish_scan_opportunities({
        "hits": [_verified_row()],
        "detected_unverified": [{"event_id": "event-2", "market": "h2h", "reason": "RULE_UNKNOWN"}],
    }, audit_event_id="audit-1")
    assert result == {"published": 0, "deduped": 0, "errors": 2}
    assert claimed == []
    assert "ARB_SNS_CLIENT_INIT_FAILED" in capsys.readouterr().out


def test_malformed_held_back_legs_do_not_suppress_other_opportunity_alerts(monkeypatch):
    """Broken quote collections stay HELD BACK; neighboring alerts still publish."""
    sns = _setup(monkeypatch)
    mixed = _verified_row()
    mixed["event_id"] = "event-mixed-legs"
    mixed["legs"] = [None, {"outcome": "home", "book": "book-a", "american": 115}, "bad-leg"]
    wrong_type = _verified_row()
    wrong_type["event_id"] = "event-nonlist-legs"
    wrong_type["legs"] = {"book": "book-a", "outcome": "home", "american": 115}
    valid = _verified_row()

    assert not alerts._is_verified_executable(mixed)
    assert not alerts._is_verified_executable(wrong_type)
    result = alerts.publish_scan_opportunities({"hits": [mixed, wrong_type, valid]})
    assert result == {"published": 3, "deduped": 0, "errors": 0}
    payloads = [json.loads(call["Message"]) for call in sns.calls]
    messages = [json.loads(payload["default"]) for payload in payloads]
    assert [message["status"] for message in messages] == ["HELD_BACK", "HELD_BACK", "VERIFIED"]
    assert messages[0]["legs"] == [{"outcome": "home", "book": "book-a", "american": 115}]
    assert messages[1]["legs"] == []
    assert all(message["holdback_reason"] == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
               for message in messages[:2])
    assert all("HELD BACK" in payload["sms"] for payload in payloads[:2])
    assert "VERIFIED EXECUTABLE" in payloads[2]["sms"]
    assert mixed["legs"][0] is None  # audited source rows are never mutated


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
    messages = [json.loads(json.loads(call["Message"])["default"]) for call in sns.calls]
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
    msg = json.loads(json.loads(sns.calls[0]["Message"])["default"])
    assert msg["status"] == "HELD_BACK"
    assert msg["holdback_reason"] == "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"


def test_top_level_candidate_rejection_prevents_false_verified_sms(monkeypatch):
    """A contradictory candidate-level reason must not be labeled executable."""
    sns = _setup(monkeypatch)
    row = _verified_row()
    row["reason"] = "SPORTSBOOK_SETTLEMENT_RULES_UNVERIFIED"
    assert alerts._is_verified_executable(row) is False
    result = alerts.publish_scan_opportunities({"hits": [row]})
    assert result == {"published": 1, "deduped": 0, "errors": 0}
    payload = json.loads(sns.calls[0]["Message"])
    message = json.loads(payload["default"])
    assert message["status"] == "HELD_BACK"
    assert message["holdback_reason"] == "SPORTSBOOK_SETTLEMENT_RULES_UNVERIFIED"
    assert "HELD BACK" in payload["sms"]
    assert "VERIFIED EXECUTABLE" not in payload["sms"]


def test_top_level_holdback_reason_change_is_material():
    initial = _verified_row()
    initial["reason"] = "SPORTSBOOK_SETTLEMENT_RULES_UNVERIFIED"
    updated = _verified_row()
    updated["reason"] = "SETTLEMENT_STATE_MISMATCH"
    assert alerts._identity("HELD_BACK", initial) != alerts._identity("HELD_BACK", updated)


def test_missing_settlement_proof_is_held(monkeypatch):
    sns = _setup(monkeypatch)
    row = _verified_row()
    row["validation"].pop("settlement_states")
    alerts.publish_scan_opportunities({"hits": [row]})
    msg = json.loads(json.loads(sns.calls[0]["Message"])["default"])
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


def test_verified_alert_requires_positive_finite_profit_in_both_proofs():
    assert alerts._is_verified_executable(_verified_row())
    for value in (None, 0, -1, float("nan"), float("inf"), True, "12.5"):
        row = _verified_row()
        row["minimum_profit"] = value
        assert not alerts._is_verified_executable(row), ("candidate_profit", value)
        row = _verified_row()
        row["validation"]["settlement_states"]["minimum_net_pnl"] = value
        assert not alerts._is_verified_executable(row), ("settlement_profit", value)


def test_contradictory_settlement_evidence_cannot_be_verified():
    for field, value in (
        ("settlement_reason", "BOOK_RULES_MISMATCH"),
        ("settlement_state_reason", "PUSH_STATE_UNRESOLVED"),
        ("settlement_advisory", "SPORTSBOOK_RULES_MAY_AFFECT_SETTLEMENT"),
        ("missing_books", ["book-b"]),
    ):
        row = _verified_row()
        row["validation"][field] = value
        assert not alerts._is_verified_executable(row), field


def test_sns_held_back_alert_preserves_contradictory_reason(monkeypatch):
    sns = _setup(monkeypatch)
    advisory = _verified_row()
    advisory["validation"]["settlement_advisory"] = "SPORTSBOOK_RULES_MAY_AFFECT_SETTLEMENT"
    missing = _verified_row()
    missing["validation"]["missing_books"] = ["book-b"]
    missing["event_id"] = "event-2"
    zero_profit = _verified_row()
    zero_profit["minimum_profit"] = 0
    zero_profit["event_id"] = "event-3"
    out = alerts.publish_scan_opportunities({"hits": [advisory, missing, zero_profit]})
    assert out == {"published": 3, "deduped": 0, "errors": 0}
    messages = [json.loads(json.loads(call["Message"])["default"]) for call in sns.calls]
    assert [m["status"] for m in messages] == ["HELD_BACK"] * 3
    assert [m["holdback_reason"] for m in messages] == [
        "SPORTSBOOK_RULES_MAY_AFFECT_SETTLEMENT",
        "SPORTSBOOK_SETTLEMENT_RULES_MISSING",
        "EXECUTABLE_QUALIFICATION_NOT_PROVEN",
    ]


def test_identical_opportunity_is_deduped(monkeypatch):
    sns = _setup(monkeypatch)
    monkeypatch.setattr(alerts, "claim_once", lambda *args, **kwargs: False)
    out = alerts.publish_scan_opportunities({"hits": [_verified_row()]})
    assert out == {"published": 0, "deduped": 1, "errors": 0}
    assert sns.calls == []


def test_status_transition_changes_alert_identity():
    row = _verified_row()
    assert alerts._identity("HELD_BACK", row) != alerts._identity("VERIFIED", row)



def test_held_back_reason_change_realerts_without_price_change():
    """New settlement evidence must not be silenced by a stale dedupe key."""
    initial = _verified_row()
    initial["validation"]["rules_compatible"] = False
    initial["validation"]["qualification_reason"] = "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"
    changed = _verified_row()
    changed["validation"]["rules_compatible"] = False
    changed["validation"]["qualification_reason"] = "SETTLEMENT_STATE_MISMATCH"
    assert alerts._identity("HELD_BACK", initial) != alerts._identity("HELD_BACK", changed)

    same = _verified_row()
    same["validation"]["rules_compatible"] = False
    same["validation"]["qualification_reason"] = "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"
    same["detected_at"] = "2026-10-24T23:20:00Z"
    assert alerts._identity("HELD_BACK", initial) == alerts._identity("HELD_BACK", same)



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


def test_material_stake_and_profit_changes_realert_without_odds_movement():
    original = _verified_row()
    stake_changed = _verified_row()
    stake_changed["allocated_stake"] = 850.0
    profit_changed = _verified_row()
    profit_changed["minimum_profit"] = 10.25

    assert alerts._identity("VERIFIED", original) != alerts._identity("VERIFIED", stake_changed)
    assert alerts._identity("VERIFIED", original) != alerts._identity("VERIFIED", profit_changed)

    # Harmless sub-cent floating point noise is not a new opportunity.
    jitter = _verified_row()
    jitter["minimum_profit"] += 0.0000001
    jitter["allocated_stake"] += 0.0000001
    assert alerts._identity("VERIFIED", original) == alerts._identity("VERIFIED", jitter)



def test_displayed_fallback_stake_and_profit_changes_realert():
    """A displayed change must not be hidden by a dedupe key omitting fallback fields."""
    original = _verified_row()
    original.pop("allocated_stake")
    original["stake_total"] = 1000.0
    stake_changed = {**original, "stake_total": 850.0}
    assert alerts._message("VERIFIED", original, audit_event_id=None)["stake_total"] == 1000.0
    assert alerts._identity("VERIFIED", original) != alerts._identity("VERIFIED", stake_changed)

    # Unqualified candidates may report profit under a fallback field.
    held = _verified_row()
    held.pop("minimum_profit")
    held["profit"] = 12.50
    profit_changed = {**held, "profit": 10.25}
    assert not alerts._is_verified_executable(held)
    assert alerts._message("HELD_BACK", held, audit_event_id=None)["profit"] == 12.50
    assert alerts._identity("HELD_BACK", held) != alerts._identity("HELD_BACK", profit_changed)

    # Alternate fallback fields and harmless float jitter retain their semantics.
    alternate = {**held, "profit": None, "min_profit": 12.50, "bankroll": 500.0}
    alternate_changed = {**alternate, "min_profit": 11.00}
    assert alerts._identity("HELD_BACK", alternate) != alerts._identity("HELD_BACK", alternate_changed)
    jitter = {**original, "stake_total": 1000.0000001}
    assert alerts._identity("VERIFIED", original) == alerts._identity("VERIFIED", jitter)


def test_zero_line_is_not_confused_with_fallback_point():
    original = _verified_row()
    original["point"] = 1.5
    zero_line = _verified_row()
    zero_line["point"] = 1.5
    zero_line["line"] = 0
    assert alerts._identity("HELD_BACK", original) != alerts._identity("HELD_BACK", zero_line)


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
    msg = json.loads(json.loads(sns.calls[0]["Message"])["default"])
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



class DefiniteSnsRejection(Exception):
    response = {"Error": {"Code": "AuthorizationError"}}


def test_definite_sns_rejection_releases_only_own_claim(monkeypatch):
    sns = _setup(monkeypatch)
    releases = []
    monkeypatch.setattr(alerts, "release_claim", lambda *args, **kwargs: releases.append((args, kwargs)))
    def rejected(**kwargs):
        raise DefiniteSnsRejection("SNS rejected")
    sns.publish = rejected
    out = alerts.publish_scan_opportunities({"hits": [_verified_row()]})
    assert out == {"published": 0, "deduped": 0, "errors": 1}
    assert len(releases) == 1
    args, kwargs = releases[0]
    assert args[0] == "ARB_OPPORTUNITY"
    assert len(args[1]) == 64
    assert len(kwargs["claim_token"]) == 32


def test_ambiguous_sns_error_never_releases_claim(monkeypatch):
    sns = _setup(monkeypatch)
    releases = []
    monkeypatch.setattr(alerts, "release_claim", lambda *args, **kwargs: releases.append((args, kwargs)))
    def timeout(**kwargs):
        raise TimeoutError("unknown whether SNS accepted")
    sns.publish = timeout
    out = alerts.publish_scan_opportunities({"hits": [_verified_row()]})
    assert out == {"published": 0, "deduped": 0, "errors": 1}
    assert releases == []


def test_sns_sms_is_readable_and_default_remains_structured(monkeypatch):
    sns = _setup(monkeypatch)
    row = _verified_row()
    row["event"] = "Home @ Away"
    row["legs"][0]["last_update"] = "2026-10-25T19:00:00Z"
    result = alerts.publish_scan_opportunities({"hits": [row]})
    assert result == {"published": 1, "deduped": 0, "errors": 0}
    request = sns.calls[0]
    assert request["MessageStructure"] == "json"
    protocol_payload = json.loads(request["Message"])
    assert set(protocol_payload) == {"default", "sms"}
    structured = json.loads(protocol_payload["default"])
    assert structured["status"] == "VERIFIED"
    assert structured["legs"] == row["legs"]
    assert structured["places_bets"] is False
    sms = protocol_payload["sms"]
    assert "VERIFIED EXECUTABLE" in sms
    assert "Home @ Away" in sms
    assert "book-a home +115" in sms
    assert "book-b away +105" in sms
    assert "edge/margin 1.25%" in sms
    assert "profit 12.5" in sms
    assert "quote 2026-10-25T19:00:00Z" in sms
    assert len(sms) <= 1000


def test_held_back_sms_shows_reason_and_converts_decimal_odds(monkeypatch):
    sns = _setup(monkeypatch)
    row = _verified_row()
    row["validation"]["rules_compatible"] = False
    row["validation"]["qualification_reason"] = "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"
    row["legs"][1].pop("american")
    row["legs"][1]["decimal"] = 1.91
    result = alerts.publish_scan_opportunities({"hits": [row]})
    assert result["published"] == 1
    payload = json.loads(sns.calls[0]["Message"])
    assert "HELD BACK" in payload["sms"]
    assert "reason SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE" in payload["sms"]
    assert "book-b away -110" in payload["sms"]
    assert json.loads(payload["default"])["holdback_reason"] == "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"



def test_malformed_first_candidate_does_not_suppress_later_verified_sms(monkeypatch):
    """A broken quote must fail closed without aborting the remaining scan."""
    sns = _setup(monkeypatch)
    malformed = _verified_row()
    malformed["event_id"] = "event-malformed"
    malformed["legs"] = ["not-a-quote"]
    valid = _verified_row()
    result = alerts.publish_scan_opportunities({"hits": [malformed, valid]})
    assert result == {"published": 2, "deduped": 0, "errors": 0}
    assert len(sns.calls) == 2
    messages = [json.loads(json.loads(call["Message"])["default"]) for call in sns.calls]
    assert messages[0]["event_id"] == "event-malformed"
    assert messages[0]["status"] == "HELD_BACK"
    assert messages[0]["holdback_reason"] == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
    assert messages[0]["legs"] == []
    assert messages[1]["event_id"] == "event-1"
    assert messages[1]["status"] == "VERIFIED"


def test_non_object_candidates_do_not_abort_later_valid_sms(monkeypatch):
    """Invalid provider rows fail closed without suppressing valid opportunities."""
    sns = _setup(monkeypatch)
    held = {
        "sport": "basketball_nba", "event_id": "event-held",
        "market": "spreads", "reason": "RULE_UNKNOWN", "legs": [],
    }
    result = alerts.publish_scan_opportunities({
        "hits": [None, _verified_row(), "invalid-row"],
        "detected_unverified": [False, held],
    })
    assert result == {"published": 2, "deduped": 0, "errors": 3}
    assert len(sns.calls) == 2
    messages = [json.loads(json.loads(call["Message"])["default"]) for call in sns.calls]
    assert [(m["event_id"], m["status"]) for m in messages] == [
        ("event-1", "VERIFIED"), ("event-held", "HELD_BACK"),
    ]

def test_local_sms_formatting_failure_releases_claim_without_sending(monkeypatch):
    """A local error must not poison dedupe or suppress a later valid SMS."""
    sns = _setup(monkeypatch)
    claimed = []
    released = []
    monkeypatch.setattr(alerts, "claim_once", lambda *args, **kwargs: claimed.append((args, kwargs)) or True)
    monkeypatch.setattr(alerts, "release_claim", lambda *args, **kwargs: released.append((args, kwargs)))
    original_sms_text = alerts._sms_text

    def fail_first(status, row, message):
        if row.get("event_id") == "event-bad":
            raise ValueError("malformed local SMS field")
        return original_sms_text(status, row, message)

    monkeypatch.setattr(alerts, "_sms_text", fail_first)
    bad = _verified_row()
    bad["event_id"] = "event-bad"
    out = alerts.publish_scan_opportunities({"hits": [bad, _verified_row()]})

    assert out == {"published": 1, "deduped": 0, "errors": 1}
    assert len(claimed) == 2
    assert len(released) == 1
    assert released[0][0][1] == claimed[0][0][1]
    assert released[0][1]["claim_token"] == claimed[0][1]["claim_token"]
    assert len(sns.calls) == 1
    msg = json.loads(json.loads(sns.calls[0]["Message"])["default"])
    assert msg["event_id"] == "event-1"


def test_malformed_candidate_collections_do_not_suppress_valid_opportunities(monkeypatch):
    """A malformed hits container cannot crash the scan or suppress held alerts."""
    sns = _setup(monkeypatch)
    held = {
        "sport": "basketball_nba", "event_id": "held-event",
        "market": "spreads", "reason": "RULE_UNKNOWN", "legs": [],
    }
    result = alerts.publish_scan_opportunities({
        "hits": 42,
        "detected_unverified": [held],
    })
    assert result == {"published": 1, "deduped": 0, "errors": 1}
    message = json.loads(json.loads(sns.calls[0]["Message"])["default"])
    assert message["event_id"] == "held-event"
    assert message["status"] == "HELD_BACK"

    sns.calls.clear()
    result = alerts.publish_scan_opportunities({
        "hits": [_verified_row()],
        "detected_unverified": {"unexpected": "object"},
    })
    assert result == {"published": 1, "deduped": 0, "errors": 1}
    message = json.loads(json.loads(sns.calls[0]["Message"])["default"])
    assert message["status"] == "VERIFIED"


def test_invalid_dedupe_ttl_fails_closed_without_claims_or_sns(monkeypatch, capsys):
    """Invalid TTL must not crash a completed scan or publish without dedupe."""
    sns = _setup(monkeypatch)
    claims = []
    monkeypatch.setattr(alerts, "claim_once", lambda *args, **kwargs: claims.append(args) or True)
    for ttl in ("not-a-number", "0", "-5"):
        monkeypatch.setenv("ARB_OPPORTUNITY_ALERT_DEDUPE_SECONDS", ttl)
        result = alerts.publish_scan_opportunities({"hits": [_verified_row()]})
        assert result == {"published": 0, "deduped": 0, "errors": 1}
    assert sns.calls == []
    assert claims == []
    assert capsys.readouterr().out.count("ARB_SNS_DEDUPE_CONFIG_INVALID") == 3


def test_unavailable_claim_store_is_error_not_duplicate_or_publish(monkeypatch):
    """SMS failure is visible, never silently classified as deduplication."""
    import audit_store
    sns = _setup(monkeypatch)
    monkeypatch.setattr(audit_store, "_table", lambda: None)
    monkeypatch.setattr(alerts, "claim_once", audit_store.claim_once)
    out = alerts.publish_scan_opportunities({"hits": [_verified_row()]})
    assert out == {"published": 0, "deduped": 0, "errors": 1}
    assert sns.calls == []


def test_reordered_provider_legs_are_one_opportunity_but_price_changes_realert(monkeypatch):
    """Stable identity ignores provider ordering, not sportsbook prices."""
    sns = _setup(monkeypatch)
    seen = set()

    def claim_once(_kind, identity, **_kwargs):
        if identity in seen:
            return False
        seen.add(identity)
        return True

    monkeypatch.setattr(alerts, "claim_once", claim_once)
    first = _verified_row()
    reordered = _verified_row()
    reordered["legs"].reverse()
    assert alerts._identity("VERIFIED", first) == alerts._identity("VERIFIED", reordered)

    result = alerts.publish_scan_opportunities({"hits": [first, reordered]})
    assert result == {"published": 1, "deduped": 1, "errors": 0}
    assert len(sns.calls) == 1

    changed = _verified_row()
    changed["legs"].reverse()
    changed["legs"][1]["american"] = 120
    assert alerts._identity("VERIFIED", first) != alerts._identity("VERIFIED", changed)
    result = alerts.publish_scan_opportunities({"hits": [changed]})
    assert result == {"published": 1, "deduped": 0, "errors": 0}
    assert len(sns.calls) == 2


def test_doubleheader_games_do_not_share_an_sms_dedupe_identity(monkeypatch):
    """Distinct provider markets with identical teams and odds need two alerts."""
    sns = _setup(monkeypatch)
    seen = set()

    def claim_once(_kind, identity, **_kwargs):
        if identity in seen:
            return False
        seen.add(identity)
        return True

    monkeypatch.setattr(alerts, "claim_once", claim_once)
    first = _verified_row()
    first.pop("event_id")
    first["event"] = "Yankees @ Red Sox"
    first["market_id"] = "game-one:h2h"
    first["commence_time"] = "2026-10-27T17:00:00Z"
    second = {**first, "market_id": "game-two:h2h"}
    moved_start = {**first, "commence_time": "2026-10-27T20:00:00Z"}
    retry = {**first, "detected_at": "2026-10-27T16:59:59Z"}

    assert alerts._identity("VERIFIED", first) != alerts._identity("VERIFIED", second)
    assert alerts._identity("VERIFIED", first) != alerts._identity("VERIFIED", moved_start)
    assert alerts._identity("VERIFIED", first) == alerts._identity("VERIFIED", retry)
    result = alerts.publish_scan_opportunities({"hits": [first, second, retry]})
    assert result == {"published": 2, "deduped": 1, "errors": 0}
    assert len(sns.calls) == 2


def test_zero_fallback_alert_values_are_preserved_and_deduped_correctly():
    """A real zero cannot be silently replaced by a lower-priority field."""
    row = _verified_row()
    row.pop("margin_pct")
    row.pop("minimum_profit")
    row.pop("allocated_stake")
    row.update({"margin": 0, "edge": 1.25, "profit": 0, "min_profit": 12.5,
                "stake_total": 0, "bankroll": 1000})
    message = alerts._message("HELD_BACK", row, audit_event_id=None)
    assert message["margin"] == 0
    assert message["profit"] == 0
    assert message["stake_total"] == 0
    changed = {**row, "margin": 1.25}
    assert alerts._identity("HELD_BACK", row) != alerts._identity("HELD_BACK", changed)
    assert alerts._identity("HELD_BACK", row) == alerts._identity("HELD_BACK", dict(row))


def test_incomplete_or_ambiguous_legs_cannot_claim_verified_executable(monkeypatch):
    """Optimistic upstream flags cannot override missing/ambiguous bet legs."""
    sns = _setup(monkeypatch)
    missing = _verified_row()
    missing["event_id"] = "missing-legs"
    missing["legs"] = []

    unnamed = _verified_row()
    unnamed["event_id"] = "unnamed-book"
    unnamed["legs"][0]["book"] = " "

    duplicate = _verified_row()
    duplicate["event_id"] = "duplicate-outcome"
    duplicate["legs"][1]["outcome"] = "home"

    valid = _verified_row()
    for row in (missing, unnamed, duplicate):
        assert not alerts._is_verified_executable(row)
    assert alerts._is_verified_executable(valid)

    result = alerts.publish_scan_opportunities({
        "hits": [missing, unnamed, duplicate, valid],
    })
    assert result == {"published": 4, "deduped": 0, "errors": 0}
    messages = [
        json.loads(json.loads(call["Message"])["default"])
        for call in sns.calls
    ]
    assert [message["status"] for message in messages] == [
        "HELD_BACK", "HELD_BACK", "HELD_BACK", "VERIFIED",
    ]
    assert [message["holdback_reason"] for message in messages[:3]] == [
        "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS",
        "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS",
        "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS",
    ]


def test_sns_failure_diagnostics_redact_sensitive_exception_messages(monkeypatch, capsys):
    """Failed SNS attempts are diagnosable without leaking exception payloads."""
    sns = _setup(monkeypatch)
    released = []
    monkeypatch.setattr(alerts, "release_claim", lambda *args, **kwargs: released.append(args))

    class Rejected(Exception):
        response = {"Error": {"Code": "AuthorizationError"}}

    def rejected(**kwargs):
        raise Rejected("secret-token-never-log")

    sns.publish = rejected
    result = alerts.publish_scan_opportunities({"hits": [_verified_row()]})
    assert result == {"published": 0, "deduped": 0, "errors": 1}
    output = capsys.readouterr().out
    assert "secret-token-never-log" not in output
    assert "ARB_SNS_PUBLISH_CONFIRMED" not in output
    event = json.loads(output.strip().splitlines()[-1])
    assert event["event"] == "ARB_SNS_PUBLISH_FAILED"
    assert event["status"] == "VERIFIED"
    assert event["error_type"] == "Rejected"
    assert event["error_code"] == "AuthorizationError"
    assert event["publish_attempted"] is True
    assert event["claim_released"] is True
    assert len(event["identity"]) == 64
    assert len(released) == 1


def test_ambiguous_sns_failure_is_logged_without_releasing_claim(monkeypatch, capsys):
    sns = _setup(monkeypatch)
    released = []
    monkeypatch.setattr(alerts, "release_claim", lambda *args, **kwargs: released.append(args))

    def timeout(**kwargs):
        raise TimeoutError("sensitive-network-detail")

    sns.publish = timeout
    result = alerts.publish_scan_opportunities({"hits": [_verified_row()]})
    assert result == {"published": 0, "deduped": 0, "errors": 1}
    output = capsys.readouterr().out
    assert "sensitive-network-detail" not in output
    event = json.loads(output.strip().splitlines()[-1])
    assert event["event"] == "ARB_SNS_PUBLISH_FAILED"
    assert event["error_type"] == "TimeoutError"
    assert event["error_code"] == ""
    assert event["publish_attempted"] is True
    assert event["claim_released"] is False
    assert released == []


def test_sms_verified_requires_real_priced_legs_and_declared_outcome_universe(monkeypatch):
    """Contradictory upstream VERIFIED flags cannot conceal unpriced or missing outcomes."""
    sns = _setup(monkeypatch)
    rows = []
    for index, invalid in enumerate((None, 0, 99, -99, float("nan"), "invalid", True)):
        row = _verified_row()
        row["event_id"] = f"bad-odds-{index}"
        row["legs"][0]["american"] = invalid
        assert not alerts._is_verified_executable(row)
        assert alerts._holdback_reason(row) == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
        rows.append(row)

    missing_draw = _verified_row()
    missing_draw["event_id"] = "three-way-missing-draw"
    missing_draw["outcomes"] = ["home", "away", "draw"]
    assert not alerts._is_verified_executable(missing_draw)
    rows.append(missing_draw)

    decimal_only = _verified_row()
    decimal_only["event_id"] = "valid-decimal"
    decimal_only["legs"][0].pop("american")
    decimal_only["legs"][0]["decimal"] = 2.15
    decimal_only["outcomes"] = ["away", "home"]
    assert alerts._is_verified_executable(decimal_only)

    result = alerts.publish_scan_opportunities({"hits": rows + [decimal_only]})
    assert result == {"published": len(rows) + 1, "deduped": 0, "errors": 0}
    messages = [json.loads(json.loads(call["Message"])["default"]) for call in sns.calls]
    assert all(message["status"] == "HELD_BACK" for message in messages[:-1])
    assert all(message["holdback_reason"] == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
               for message in messages[:-1])
    assert messages[-1]["status"] == "VERIFIED"
    assert "book-a home +115" in json.loads(sns.calls[-1]["Message"])["sms"]


def test_sms_quote_age_requires_all_valid_provider_timestamps():
    """Unknown, malformed, or future timestamps must never claim a known age."""
    from datetime import datetime, timezone
    now = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)
    row = _verified_row()
    assert alerts._oldest_quote_age_seconds(row, as_of=now) is None
    row["legs"][0]["last_update"] = "2026-10-27T13:59:30Z"
    row["legs"][1]["last_update"] = "2026-10-27T13:58:00+00:00"
    assert alerts._oldest_quote_age_seconds(row, as_of=now) == 120
    row["legs"][1]["last_update"] = "2026-10-27T14:00:01Z"
    assert alerts._oldest_quote_age_seconds(row, as_of=now) is None
    row["legs"][1]["last_update"] = "2026-10-27T13:58:00"
    assert alerts._oldest_quote_age_seconds(row, as_of=now) is None
    row["legs"][1]["last_update"] = "bad timestamp"
    assert alerts._oldest_quote_age_seconds(row, as_of=now) is None


def test_sns_sms_and_structured_payload_include_conservative_oldest_quote_age(monkeypatch):
    """Quote age is informative only; never changes verified status or identity."""
    from datetime import datetime, timedelta, timezone
    sns = _setup(monkeypatch)
    now = datetime.now(timezone.utc)
    row = _verified_row()
    row["legs"][0]["last_update"] = (now - timedelta(seconds=60)).isoformat()
    row["legs"][1]["last_update"] = (now - timedelta(seconds=135)).isoformat()
    baseline_identity = alerts._identity("VERIFIED", row)
    result = alerts.publish_scan_opportunities({"hits": [row]})
    assert result == {"published": 1, "deduped": 0, "errors": 0}
    payload = json.loads(sns.calls[0]["Message"])
    message = json.loads(payload["default"])
    assert message["status"] == "VERIFIED"
    assert message["quote_last_updates"] == [
        row["legs"][0]["last_update"], row["legs"][1]["last_update"],
    ]
    assert 134 <= message["quote_age_seconds"] <= 150
    assert "oldest quote age " in payload["sms"]
    assert alerts._identity("VERIFIED", row) == baseline_identity
    assert len(payload["sms"]) <= 1000


def test_declared_three_way_market_cannot_verify_two_outcome_legs(monkeypatch):
    """An optimistic upstream complete flag cannot hide a missing draw leg."""
    sns = _setup(monkeypatch)
    rows = []
    for index, market in enumerate(("h2h_3_way", "h2h_3_way_h1", "corners_1x2", "1x2", "3-way")):
        row = _verified_row()
        row["event_id"] = f"three-way-{index}"
        row["market"] = market
        # Deliberately omit the expected-outcomes inventory: this is the
        # contradiction the upstream coverage flag alone cannot resolve.
        row.pop("outcomes", None)
        assert not alerts._is_verified_executable(row)
        assert alerts._holdback_reason(row) == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
        rows.append(row)

    result = alerts.publish_scan_opportunities({"hits": rows})
    assert result == {"published": len(rows), "deduped": 0, "errors": 0}
    payloads = [json.loads(json.loads(call["Message"])["default"]) for call in sns.calls]
    assert all(item["status"] == "HELD_BACK" for item in payloads)
    assert all(item["holdback_reason"] == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
               for item in payloads)


def test_declared_three_way_market_accepts_three_distinct_priced_legs():
    """Three-way proof is additive; ordinary two-way and draw-no-bet survive."""
    row = _verified_row()
    row["market"] = "h2h_3_way"
    row["legs"].append({"book": "book-c", "outcome": "draw", "american": 230})
    assert alerts._verified_legs_present(row)
    row["market"] = "corners_1x2"
    assert alerts._verified_legs_present(row)
    row["legs"].pop()
    assert not alerts._verified_legs_present(row)
    row["market"] = "draw_no_bet"
    assert alerts._verified_legs_present(row)
    row["market"] = "h2h"
    assert alerts._verified_legs_present(row)


def test_sms_preserves_event_start_and_handicap_per_leg(monkeypatch):
    """Do not hide the priced point on spread/total opportunities."""
    sns = _setup(monkeypatch)
    row = _verified_row()
    row["market"] = "spreads"
    row["commence_time"] = "2026-10-28T19:30:00Z"
    row["legs"][0]["point"] = -1.5
    row["legs"][1]["point"] = 1.5
    assert alerts._is_verified_executable(row)
    assert alerts.publish_scan_opportunities({"hits": [row]})["published"] == 1
    payload = json.loads(sns.calls[0]["Message"])
    structured = json.loads(payload["default"])
    assert structured["commence_time"] == "2026-10-28T19:30:00Z"
    assert "starts 2026-10-28T19:30:00Z" in payload["sms"]
    assert "book-a home line -1.5 +115" in payload["sms"]
    assert "book-b away line +1.5 +105" in payload["sms"]


def test_sms_formats_decimal_string_odds_as_american_without_weakening_proof(monkeypatch):
    sns = _setup(monkeypatch)
    row = _verified_row()
    row["legs"][0].pop("american")
    row["legs"][0]["decimal"] = "2.15"
    assert alerts._is_verified_executable(row)
    assert alerts.publish_scan_opportunities({"hits": [row]})["published"] == 1
    payload = json.loads(sns.calls[0]["Message"])
    assert "book-a home +115" in payload["sms"]
    assert "odds?" not in payload["sms"]


def test_sms_omits_invalid_points_without_affecting_held_back_classification(monkeypatch):
    sns = _setup(monkeypatch)
    row = _verified_row()
    row["legs"][0]["point"] = float("nan")
    row["validation"]["rules_compatible"] = False
    assert not alerts._is_verified_executable(row)
    assert alerts.publish_scan_opportunities({"hits": [row]})["published"] == 1
    payload = json.loads(sns.calls[0]["Message"])
    assert "HELD BACK" in payload["sms"]
    assert "line nan" not in payload["sms"].lower()


def test_explicit_invalid_decimal_cannot_be_verified_even_with_valid_american(monkeypatch):
    """Both supplied price representations must be valid before an executable alert."""
    sns = _setup(monkeypatch)
    rows = []
    for index, decimal in enumerate((0, 1, -1, float("nan"), float("inf"), True, "not-a-price")):
        row = _verified_row()
        row["event_id"] = f"event-bad-decimal-{index}"
        row["legs"][0]["decimal"] = decimal
        assert not alerts._is_verified_executable(row)
        rows.append(row)
    valid = _verified_row()
    valid["event_id"] = "event-valid-dual-price"
    valid["legs"][0]["decimal"] = 2.15
    assert alerts._is_verified_executable(valid)
    rows.append(valid)

    out = alerts.publish_scan_opportunities({"hits": rows})
    assert out == {"published": len(rows), "deduped": 0, "errors": 0}
    messages = [json.loads(json.loads(call["Message"])["default"]) for call in sns.calls]
    assert [m["status"] for m in messages] == ["HELD_BACK"] * (len(rows) - 1) + ["VERIFIED"]
    assert all(m["holdback_reason"] == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
               for m in messages[:-1])


def test_invalid_explicit_betting_line_is_held_back_despite_optimistic_flags(monkeypatch):
    """Nonfinite or malformed spread points cannot be VERIFIED EXECUTABLE."""
    sns = _setup(monkeypatch)
    rows = []
    for index, point in enumerate((float("nan"), float("inf"), True, "not-a-line")):
        row = _verified_row()
        row["event_id"] = f"invalid-point-{index}"
        row["market"] = "spreads"
        row["legs"][0]["point"] = point
        row["legs"][1]["point"] = 1.5
        assert not alerts._is_verified_executable(row)
        assert alerts._holdback_reason(row) == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
        rows.append(row)

    valid = _verified_row()
    valid["event_id"] = "valid-spread-points"
    valid["market"] = "spreads"
    valid["legs"][0]["point"] = "-1.5"
    valid["legs"][1]["point"] = 1.5
    assert alerts._is_verified_executable(valid)
    rows.append(valid)

    result = alerts.publish_scan_opportunities({"hits": rows})
    assert result == {"published": len(rows), "deduped": 0, "errors": 0}
    messages = [json.loads(json.loads(call["Message"])["default"]) for call in sns.calls]
    assert [item["status"] for item in messages] == ["HELD_BACK"] * 4 + ["VERIFIED"]
    assert all(item["holdback_reason"] == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
               for item in messages[:4])


def test_contradictory_dual_format_odds_are_held_back_despite_optimistic_flags(monkeypatch):
    """A valid American price plus a different valid decimal is not a proven bet."""
    sns = _setup(monkeypatch)
    rows = []
    for index, (american, decimal) in enumerate(((115, 1.50), (-120, 2.15),
                                                  ("+105", "1.75"))):
        row = _verified_row()
        row["event_id"] = f"contradictory-dual-price-{index}"
        row["legs"][0]["american"] = american
        row["legs"][0]["decimal"] = decimal
        assert not alerts._is_verified_executable(row)
        assert alerts._holdback_reason(row) == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
        rows.append(row)

    result = alerts.publish_scan_opportunities({"hits": rows})
    assert result == {"published": len(rows), "deduped": 0, "errors": 0}
    messages = [json.loads(json.loads(call["Message"])["default"]) for call in sns.calls]
    assert all(message["status"] == "HELD_BACK" for message in messages)
    assert all(message["holdback_reason"] == "OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS"
               for message in messages)


def test_consistent_dual_format_odds_allow_provider_rounding():
    """Standard American/decimal conversion and two-decimal rounding stay valid."""
    for american, decimal in ((115, 2.15), (-120, 1.83),
                              ("+105", "2.05"), (-105, 1.95)):
        row = _verified_row()
        row["legs"][0]["american"] = american
        row["legs"][0]["decimal"] = decimal
        assert alerts._is_verified_executable(row)
