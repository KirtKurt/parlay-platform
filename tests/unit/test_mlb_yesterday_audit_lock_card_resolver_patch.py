from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from hello_world import mlb_yesterday_audit_lock_card_resolver_patch as subject


class LockedEvidenceUnavailable(RuntimeError):
    pass


class FakeTable:
    def __init__(
        self,
        daily_keys,
        canonical_items=None,
        *,
        daily_query_error=None,
        canonical_query_error=None,
    ):
        self.daily_keys = list(daily_keys)
        self.canonical_items = list(canonical_items or [])
        self.daily_query_error = daily_query_error
        self.canonical_query_error = canonical_query_error

    def query(self, **kwargs):
        values = kwargs["ExpressionAttributeValues"]
        pk = values[":pk"]
        prefix = values[":prefix"]
        assert kwargs["ConsistentRead"] is True
        if pk == "LOCKED_PICKS#mlb#2026-08-03":
            assert prefix == "DAILY_LOCK#TMINUS"
            if self.daily_query_error is not None:
                raise self.daily_query_error
            return {"Items": [{"SK": key} for key in self.daily_keys]}
        if pk == "GAME_WINNERS#mlb#2026-08-03":
            assert prefix == "LOCKED#GAME#"
            if self.canonical_query_error is not None:
                raise self.canonical_query_error
            return {"Items": self.canonical_items}
        raise AssertionError((pk, prefix))


def _game_id(row):
    value = (
        row.get("providerEventId")
        or row.get("gameId")
        or row.get("game_id")
        or row.get("id")
        or ""
    )
    value = str(value)
    return value[len("provider:") :] if value.startswith("provider:") else value


def _module(
    valid_keys,
    discovered_keys,
    *,
    canonical_items=None,
    daily_query_error=None,
    canonical_query_error=None,
):
    module = SimpleNamespace()
    module.VERSION = "MLB-YESTERDAY-AUDIT-v2.1-test"
    module.DAILY_LOCK_SK = "DAILY_LOCK#TMINUS45"
    module.LockedEvidenceUnavailable = LockedEvidenceUnavailable
    module._game_id = _game_id
    module._identity = lambda row: str(
        row.get("gameIdentity") or row.get("gameId") or row.get("id") or ""
    )
    module._commence = lambda row: str(
        row.get("commenceTime") or row.get("commence_time") or ""
    )
    module.normalize_team = lambda value: " ".join(
        str(value or "").lower().split()
    )
    module.history = SimpleNamespace(
        PULLS=FakeTable(
            discovered_keys,
            canonical_items,
            daily_query_error=daily_query_error,
            canonical_query_error=canonical_query_error,
        )
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


def _canonical_row(game_id="event-1", *, commence="2026-08-03T23:10:00+00:00"):
    identity = f"provider:{game_id}"
    row = {
        "gameId": game_id,
        "providerEventId": game_id,
        "gameIdentity": identity,
        "commenceTime": commence,
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "lockedPrediction": True,
        "canonicalPerGameStageAuthority": {
            "stageFingerprint": f"stage-{game_id}",
        },
    }
    item = {
        "PK": "GAME_WINNERS#mlb#2026-08-03",
        "SK": f"LOCKED#GAME#{commence}#{identity}",
        "record_type": "mlb_immutable_locked_single_game_prediction",
        "slate_date": "2026-08-03",
        "game_id": game_id,
        "game_identity": identity,
        "predicted_winner": "Home Club",
        "immutable_locked": True,
        "stage_authority_verified": True,
        "stage_authority_version": "stage-authority-v2",
        "stage_fingerprint": f"stage-{game_id}",
        "immutable_locked_storage_version": "storage-v5",
        "selection_lock_verified": True,
        "data": row,
    }
    return row, item


def _install_contracts(monkeypatch, *, stage_errors=None, vector_errors=None):
    stage_errors = stage_errors or {}
    vector_errors = vector_errors or {}
    storage = SimpleNamespace(
        VERSION="storage-v5",
        AUTHORITY_VERSION="stage-authority-v2",
        validate_canonical_stage_authority=lambda _table, row: list(
            stage_errors.get(_game_id(row), [])
        ),
    )
    vector = SimpleNamespace(
        validate_selection_lock_vector_status=lambda row: list(
            vector_errors.get(_game_id(row), [])
        )
    )
    monkeypatch.setitem(sys.modules, "mlb_immutable_locked_storage_patch", storage)
    monkeypatch.setitem(
        sys.modules,
        "mlb_daily_lock_ml_vector_preservation_patch",
        vector,
    )


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
        daily_query_error=TimeoutError("ddb query timeout"),
    )
    subject.apply(module)

    result = module.load_locked_predictions("2026-08-03")

    assert result["acceptedCardSk"] == "DAILY_LOCK#TMINUS45"
    assert result["dailyLockCardResolution"]["queryError"].startswith(
        "TimeoutError:"
    )


def test_uses_direct_verified_canonical_rows_when_daily_card_is_absent(monkeypatch):
    _install_contracts(monkeypatch)
    row, item = _canonical_row()
    module = _module(set(), [], canonical_items=[item])
    subject.apply(module)

    result = module.load_locked_predictions("2026-08-03")

    assert result["rows"] == [row]
    assert result["dailyPicks"] == [row]
    authority = result["authority"]
    assert authority["authorityClass"] == (
        "CANONICAL_IMMUTABLE_PER_GAME_ROWS_DIRECT_QUERY"
    )
    assert authority["historicalPredictionsRecomputed"] is False
    assert authority["canonicalSingleGameRowsVerified"] is True
    assert authority["stageAuthorityValidated"] is True
    assert authority["selectionLockVectorStatusValidated"] is True
    assert authority["partitionFingerprint"]
    resolution = result["dailyLockCardResolution"]
    assert resolution["dailyCardPresent"] is False
    assert resolution["fallbackSource"] == (
        "canonical_immutable_per_game_partition"
    )
    assert resolution["historicalPredictionsRecomputed"] is False


def test_direct_canonical_stage_validation_error_fails_closed(monkeypatch):
    _install_contracts(
        monkeypatch,
        stage_errors={"event-1": ["stage_payload_fingerprint_mismatch"]},
    )
    _, item = _canonical_row()
    module = _module(set(), [], canonical_items=[item])
    subject.apply(module)

    with pytest.raises(
        LockedEvidenceUnavailable,
        match="CANONICAL_IMMUTABLE_PER_GAME_STAGE_INVALID",
    ):
        module.load_locked_predictions("2026-08-03")


def test_direct_canonical_selection_status_error_fails_closed(monkeypatch):
    _install_contracts(
        monkeypatch,
        vector_errors={"event-1": ["selection_lock_vector_status_invalid"]},
    )
    _, item = _canonical_row()
    module = _module(set(), [], canonical_items=[item])
    subject.apply(module)

    with pytest.raises(
        LockedEvidenceUnavailable,
        match="CANONICAL_IMMUTABLE_PER_GAME_SELECTION_STATUS_INVALID",
    ):
        module.load_locked_predictions("2026-08-03")


def test_direct_canonical_duplicate_provider_identity_fails_closed(monkeypatch):
    _install_contracts(monkeypatch)
    _, first = _canonical_row()
    _, second = _canonical_row(
        commence="2026-08-03T23:40:00+00:00"
    )
    module = _module(set(), [], canonical_items=[first, second])
    subject.apply(module)

    with pytest.raises(
        LockedEvidenceUnavailable,
        match="CANONICAL_IMMUTABLE_PER_GAME_DUPLICATE_IDENTITY",
    ):
        module.load_locked_predictions("2026-08-03")


def test_direct_canonical_metadata_mismatch_fails_closed(monkeypatch):
    _install_contracts(monkeypatch)
    _, item = _canonical_row()
    item["predicted_winner"] = "Away Club"
    module = _module(set(), [], canonical_items=[item])
    subject.apply(module)

    with pytest.raises(
        LockedEvidenceUnavailable,
        match="CANONICAL_IMMUTABLE_PER_GAME_METADATA_INVALID",
    ):
        module.load_locked_predictions("2026-08-03")


def test_zero_valid_cards_and_zero_canonical_rows_preserves_fail_closed_state(monkeypatch):
    _install_contracts(monkeypatch)
    module = _module(set(), ["DAILY_LOCK#TMINUS45", "DAILY_LOCK#TMINUS50"])
    subject.apply(module)

    with pytest.raises(
        LockedEvidenceUnavailable,
        match="IMMUTABLE_DAILY_LOCK_CARD_UNAVAILABLE",
    ):
        module.load_locked_predictions("2026-08-03")
