"""Atomic SNS alert claim and safe retry contracts (no AWS calls)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import audit_store


class ConditionalCheckFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, **kwargs):
        item = kwargs["Item"]
        key = (item["pk"], item["sk"])
        old = self.items.get(key)
        condition = kwargs["ConditionExpression"]
        values = kwargs.get("ExpressionAttributeValues", {})
        if "attribute_not_exists" in condition:
            if old is not None and old["ttl"] >= values[":now"]:
                raise ConditionalCheckFailed()
        elif condition == "claim_token = :token":
            if old is None or old.get("claim_token") != values[":token"]:
                raise ConditionalCheckFailed()
        else:
            raise AssertionError(f"Unexpected conditional expression: {condition}")
        self.items[key] = dict(item)


def test_claim_release_requires_owner_token_and_allows_retry(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(audit_store, "_table", lambda: table)
    monkeypatch.setattr(audit_store.time, "time", lambda: 1000)
    assert audit_store.claim_once("ARB_OPPORTUNITY", "id-1", ttl_seconds=60, claim_token="owner-a")
    assert not audit_store.claim_once("ARB_OPPORTUNITY", "id-1", claim_token="owner-b")
    assert not audit_store.release_claim("ARB_OPPORTUNITY", "id-1", claim_token="owner-b")
    assert audit_store.release_claim("ARB_OPPORTUNITY", "id-1", claim_token="owner-a")
    assert audit_store.claim_once("ARB_OPPORTUNITY", "id-1", claim_token="owner-b")
    assert not audit_store.release_claim("ARB_OPPORTUNITY", "id-1", claim_token="owner-a")
    assert table.items[("CLAIM#ARB_OPPORTUNITY", "id-1")]["claim_token"] == "owner-b"


def test_expired_claim_can_be_reclaimed_before_dynamodb_ttl_cleanup(monkeypatch):
    table = FakeTable()
    clock = [1000]
    monkeypatch.setattr(audit_store, "_table", lambda: table)
    monkeypatch.setattr(audit_store.time, "time", lambda: clock[0])
    assert audit_store.claim_once("ARB_OPPORTUNITY", "id-2", ttl_seconds=60, claim_token="old")
    clock[0] = 1061
    assert audit_store.claim_once("ARB_OPPORTUNITY", "id-2", claim_token="new")
    assert not audit_store.release_claim("ARB_OPPORTUNITY", "id-2", claim_token="old")
    assert table.items[("CLAIM#ARB_OPPORTUNITY", "id-2")]["claim_token"] == "new"


def test_claim_release_fails_closed_without_token_or_table(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(audit_store, "_table", lambda: table)
    assert not audit_store.release_claim("ARB_OPPORTUNITY", "id-3", claim_token="")
    assert not audit_store.release_claim("ARB_OPPORTUNITY", "", claim_token="token")
    assert not audit_store.release_claim("ARB_OPPORTUNITY", "id-3", claim_token="token")
    monkeypatch.setattr(audit_store, "_table", lambda: None)
    assert not audit_store.claim_once("ARB_OPPORTUNITY", "id-3", claim_token="token")
    assert not audit_store.release_claim("ARB_OPPORTUNITY", "id-3", claim_token="token")
