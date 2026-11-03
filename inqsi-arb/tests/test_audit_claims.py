"""Regression proof for atomic ARB alert claim expiry and safe release."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import audit_store


class FakeTable:
    def __init__(self):
        self.put = None
        self.delete = None

    def put_item(self, **kwargs):
        self.put = kwargs
        return {}

    def delete_item(self, **kwargs):
        self.delete = kwargs
        return {}


class ConditionalFailure(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


def test_expired_claim_can_be_reclaimed_atomically(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(audit_store, "_table", lambda: table)
    monkeypatch.setattr(audit_store.time, "time", lambda: 1000)
    assert audit_store.claim_once("ARB_OPPORTUNITY", "identity", ttl_seconds=90,
                                  claim_token="owned-token")
    assert table.put["Item"]["ttl"] == 1090
    assert table.put["Item"]["claim_token"] == "owned-token"
    assert table.put["ConditionExpression"] == "attribute_not_exists(pk) OR #ttl <= :now"
    assert table.put["ExpressionAttributeValues"] == {":now": 1000}
    assert table.put["ExpressionAttributeNames"] == {"#ttl": "ttl"}


def test_release_requires_matching_claim_token(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(audit_store, "_table", lambda: table)
    assert audit_store.release_claim("ARB_OPPORTUNITY", "identity", claim_token="owned-token")
    assert table.delete["Key"] == {"pk": "CLAIM#ARB_OPPORTUNITY", "sk": "identity"}
    assert table.delete["ConditionExpression"] == "claim_token = :token"
    assert table.delete["ExpressionAttributeValues"] == {":token": "owned-token"}


def test_release_fails_closed_on_stolen_or_missing_claim(monkeypatch):
    class LostClaimTable(FakeTable):
        def delete_item(self, **kwargs):
            raise ConditionalFailure("claim belongs to another publisher")

    monkeypatch.setattr(audit_store, "_table", lambda: LostClaimTable())
    assert audit_store.release_claim("ARB_OPPORTUNITY", "identity", claim_token="old-token") is False
    assert audit_store.release_claim("ARB_OPPORTUNITY", "identity", claim_token="") is False


def test_claim_contention_dedupes(monkeypatch):
    class ContendedTable(FakeTable):
        def put_item(self, **kwargs):
            raise ConditionalFailure("already claimed")

    monkeypatch.setattr(audit_store, "_table", lambda: ContendedTable())
    assert audit_store.claim_once("ARB_OPPORTUNITY", "identity", claim_token="new-token") is False


def test_unavailable_claim_store_is_not_a_duplicate(monkeypatch):
    """An absent audit table cannot masquerade as an existing alert claim."""
    monkeypatch.setattr(audit_store, "_table", lambda: None)
    import pytest
    with pytest.raises(RuntimeError, match="ARB_OPPORTUNITY_CLAIM_STORE_UNAVAILABLE"):
        audit_store.claim_once("ARB_OPPORTUNITY", "valid-identity", claim_token="token")
