from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from tests.unit.test_mlb_daily_per_game_lock import build_module, game, pull
import inqsi_pull_history as history_contract
import mlb_daily_per_game_lock_patch as patch
import mlb_slate_coverage_patch as coverage
import mlb_slate_prediction_lock as slate_lock


SLATE = "2026-07-13"
GAME_COUNT = 15
PULL_COUNT = 65


def _games(start: str):
    return [game(f"scale-{index}", start) for index in range(GAME_COUNT)]


def _pulls(games):
    first = datetime.fromisoformat("2026-07-13T01:00:00+00:00")
    return [
        pull(
            (first + timedelta(minutes=15 * index)).isoformat(),
            games,
            f"scale-{index:03d}",
        )
        for index in range(PULL_COUNT)
    ]


@contextmanager
def _count_table_reads(table):
    counts = {"getItem": 0, "query": 0}
    original_get_item = table.get_item
    original_query = table.query

    def counted_get_item(*args, **kwargs):
        counts["getItem"] += 1
        return original_get_item(*args, **kwargs)

    def counted_query(*args, **kwargs):
        counts["query"] += 1
        return original_query(*args, **kwargs)

    table.get_item = counted_get_item
    table.query = counted_query
    try:
        yield counts
    finally:
        table.get_item = original_get_item
        table.query = original_query


@contextmanager
def _without_status_read_cache():
    yield None


@pytest.fixture(scope="module")
def locked_scale_module():
    games = _games("2026-07-13T18:00:00+00:00")
    module = build_module(
        _pulls(games),
        "2026-07-13T17:15:05+00:00",
        seed=True,
    )
    result = module.run_lock(SLATE)
    assert result["locked"] is True
    assert result["perGameLockProgress"]["lockedPredictionCount"] == GAME_COUNT
    return module


def test_locked_status_request_cache_preserves_payload_and_bounds_reads(
    locked_scale_module,
    monkeypatch,
):
    module = locked_scale_module
    with monkeypatch.context() as context:
        context.setattr(patch, "_status_read_scope", _without_status_read_cache)
        with _count_table_reads(module.TABLE) as uncached_counts:
            uncached = module._status_payload(SLATE)

    with _count_table_reads(module.TABLE) as cached_counts:
        cached = module._status_payload(SLATE)

    assert cached == uncached
    assert cached["gameCount"] == GAME_COUNT
    assert cached["pullCount"] == PULL_COUNT
    assert cached["lockedPredictionCount"] == GAME_COUNT
    assert cached_counts["getItem"] <= 250
    assert cached_counts["query"] == GAME_COUNT
    assert uncached_counts["getItem"] > cached_counts["getItem"] * 8


def test_prelock_status_15_games_65_pulls_has_bounded_reads():
    games = _games("2026-07-13T21:00:00+00:00")
    module = build_module(
        _pulls(games),
        "2026-07-13T15:00:00+00:00",
        seed=False,
    )

    with _count_table_reads(module.TABLE) as counts:
        payload = module._status_payload(SLATE)

    assert payload["gameCount"] == GAME_COUNT
    assert payload["pullCount"] == PULL_COUNT
    assert payload["lockedPredictionCount"] == 0
    assert len(payload["perGameStatus"]) == GAME_COUNT
    assert payload["operationalDefect"] is False
    assert counts["getItem"] <= 65
    assert counts["query"] == GAME_COUNT


def test_persisted_prediction_read_scope_canonicalizes_large_pull_set_once(
    locked_scale_module,
    monkeypatch,
):
    module = locked_scale_module
    pulls = module.history.query_pulls("mlb", SLATE, 500)
    manifest = list(
        (pulls[-1].get("provider_schedule_manifest") or {}).get("games") or []
    )

    class LockModule:
        LOCK_MINUTES = 45
        _parse_dt = staticmethod(coverage._parse_dt)

    calls = 0
    original = history_contract.canonicalize_pull_slots

    def counted_canonicalize(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        history_contract,
        "canonicalize_pull_slots",
        counted_canonicalize,
    )

    uncached = coverage._persisted_prelock_by_identity(
        module.mlb_game_winner_engine,
        LockModule,
        pulls,
        manifest,
        SLATE,
        module.now,
    )
    uncached_calls = calls

    calls = 0
    with patch._status_read_scope():
        cached = coverage._persisted_prelock_by_identity(
            module.mlb_game_winner_engine,
            LockModule,
            pulls,
            manifest,
            SLATE,
            module.now,
        )

    assert cached == uncached
    assert len(cached[0]) == GAME_COUNT
    assert cached[1] == {}
    assert calls == 1
    assert uncached_calls == GAME_COUNT


def test_full_persisted_prediction_route_has_bounded_large_slate_reads(
    locked_scale_module,
    monkeypatch,
):
    module = locked_scale_module
    engine = module.mlb_game_winner_engine
    for source_pull in module.history.pulls:
        monkeypatch.setitem(source_pull, "source", "the_odds_api")
    monkeypatch.setattr(history_contract, "PULLS", module.TABLE)

    coverage_module = importlib.reload(coverage)
    lock_module = importlib.reload(slate_lock)
    monkeypatch.setattr(
        coverage_module,
        "_now_utc",
        lambda: datetime(2026, 7, 13, 17, 16, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        engine,
        "_INQSI_MLB_PERSISTED_PRELOCK_PUBLIC_AUTHORITY_ENABLED",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        engine,
        "_INQSI_MLB_PREDICTION_PROBABILITY_CONTRACT_V1_APPLIED",
        True,
        raising=False,
    )
    coverage_module.apply(lock_module)
    lock_module.apply(engine)
    coverage_module.install_public_authority(engine, lock_module)

    with _count_table_reads(module.TABLE) as uncached_counts:
        uncached = engine.predict_all(SLATE, store=False, limit=500)
    with _count_table_reads(module.TABLE) as cached_counts:
        cached = engine.read_persisted_predictions(SLATE, store=False, limit=500)

    assert cached == uncached
    assert cached["readAuthority"] == "persisted_prelock_and_canonical_locked_only"
    assert len(cached["predictions"]) == GAME_COUNT
    assert cached_counts["getItem"] <= 200
    assert cached_counts["query"] <= GAME_COUNT + 1
    assert uncached_counts["getItem"] <= 200
    assert uncached_counts["query"] <= GAME_COUNT + 1


def test_status_cache_shares_absence_but_retries_transport_errors():
    key = {"PK": "PULLS#mlb#2026-07-13", "SK": "PULL#missing"}

    class NamedTable:
        name = "parlay_platform_snapshots"

        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = 0

        def get_item(self, **_kwargs):
            self.calls += 1
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    absent = NamedTable([{}, {"Item": {"PK": key["PK"], "SK": key["SK"]}}])
    with patch._status_read_scope():
        assert patch._consistent_item(absent, key) is None
        assert patch._consistent_item(absent, key) is None
    assert absent.calls == 1

    # The next request gets a fresh snapshot rather than retaining the absence.
    with patch._status_read_scope():
        assert patch._consistent_item(absent, key) == {
            "PK": key["PK"],
            "SK": key["SK"],
        }
    assert absent.calls == 2

    flaky = NamedTable([
        TimeoutError("transient read failure"),
        {"Item": {"PK": key["PK"], "SK": key["SK"]}},
    ])
    with patch._status_read_scope():
        assert patch._consistent_item(flaky, key) is None
        assert patch._consistent_item(flaky, key) == {
            "PK": key["PK"],
            "SK": key["SK"],
        }
    assert flaky.calls == 2


def test_status_cache_is_nested_exception_safe_and_returns_independent_copies():
    key = {"PK": "PULLS#mlb#2026-07-13", "SK": "PULL#immutable"}

    class Table:
        name = "parlay_platform_snapshots"

        def __init__(self):
            self.calls = 0

        def get_item(self, **_kwargs):
            self.calls += 1
            return {
                "Item": {
                    "PK": key["PK"],
                    "SK": key["SK"],
                    "data": {"value": 1},
                }
            }

    table = Table()
    assert patch._STATUS_READ_CACHE.get() is None
    with pytest.raises(RuntimeError, match="injected read failure"):
        with patch._status_read_scope() as outer:
            with patch._status_read_scope() as inner:
                assert inner is outer
                first = patch._consistent_item(table, key)
                first["data"]["value"] = 99
                second = patch._consistent_item(table, key)
                assert second["data"]["value"] == 1
                raise RuntimeError("injected read failure")

    assert table.calls == 1
    assert patch._STATUS_READ_CACHE.get() is None
