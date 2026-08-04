from __future__ import annotations

from types import SimpleNamespace

import pytest

from hello_world import mlb_yesterday_audit_lock_card_resolver_patch as subject


class LockedEvidenceUnavailable(RuntimeError):
    pass


class FakeTable:
    def __init__(self, keys, *, query_error=None):
        self.keys = list(keys)
        self.query_error = query_error

    def query(self, **kwargs):
        assert kwargs["ExpressionAttributeValues"][":pk"] == "LOCKED_PICKS#mlb#2026-08-03"
        assert kwargs["ExpressionAttributeValues"][":prefix"] == "DAILY_LOCK#TMINUS"
        if self.query_error is not None:
            raise self.query_error
        return {"Items": [{"SK": key} for key in self.keys]}


def _module(valid_keys, discovered_keys, *, query_error=None):
    module = SimpleNamespace()
    module.DAILY_LOCK_SK = "DAILY_LOCK#TMINUS45"
    module.LockedEvidenceUnavailable = LockedEvidenceUnavailable
    module.history = SimpleNamespace(
        PULLS=FakeTable(discovered_keys, query_error=query_error)
    )

    def load_locked_predictions(slate_date):
        assert slate_date == "2026-08-03"
        selected = module.DAILY_LOCK_SK
        if selected not in valid_keys:
            raise LockedEvidenceUnavailable(
                f"IMMUTABLE_DAILY_LOCK_CARD_UNAVAILABLE:{selected}"
            )
        return {
            "ok": True,
            "slate_date": slate_date,
            "lockedGameCount": 15,
            "acceptedCardSk": selected,
        }

    module.load_locked_predictions = load_locked_predictions
    return module


def test_discovers_configured_authoritative_card_without_weakening_loader(monkeypatch):
    monkeypatch.setenv("MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME", "50")
    module = _module(
        {"DAILY_LOCK#TMINUS50"},
        ["DAILY_LOCK#TMINUS50"],
    )
    subject.apply(module)

    result = module.load_locked_predictions("2026-08-03")

    assert result["acceptedCardSk"] == "DAILY_LOCK#TMINUS50"
    proof = result["dailyLockCardResolution"]
    assert proof["selectedSk"] == "DAILY_LOCK#TMINUS50"
    assert proof["preferredSk"] == "DAILY_LOCK#TMINUS45"
    assert proof["authority"] == "original_immutable_yesterday_audit_loader"
    assert proof["discoveryIsAuthority"] is False
    assert module.DAILY_LOCK_SK == "DAILY_LOCK#TMINUS45"


def test_multiple_fully_valid_cards_fail_closed(monkeypatch):
    monkeypatch.delenv("MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME", raising=False)
    module = _module(
        {"DAILY_LOCK#TMINUS45", "DAILY_LOCK#TMINUS50"},
        ["DAILY_LOCK#TMINUS45", "DAILY_LOCK#TMINUS50"],
    )
    subject.apply(module)

    with pytest.raises(
        LockedEvidenceUnavailable,
        match="MULTIPLE_AUTHORITATIVE_DAILY_LOCK_CARDS",
    ):
        module.load_locked_predictions("2026-08-03")


def test_query_failure_does_not_hide_valid_preferred_card():
    module = _module(
        {"DAILY_LOCK#TMINUS45"},
        [],
        query_error=TimeoutError("ddb query timeout"),
    )
    subject.apply(module)

    result = module.load_locked_predictions("2026-08-03")

    assert result["acceptedCardSk"] == "DAILY_LOCK#TMINUS45"
    assert result["dailyLockCardResolution"]["queryError"].startswith("TimeoutError:")


def test_zero_valid_cards_preserves_fail_closed_state():
    module = _module(set(), ["DAILY_LOCK#TMINUS45", "DAILY_LOCK#TMINUS50"])
    subject.apply(module)

    with pytest.raises(
        LockedEvidenceUnavailable,
        match="IMMUTABLE_DAILY_LOCK_CARD_UNAVAILABLE",
    ):
        module.load_locked_predictions("2026-08-03")
