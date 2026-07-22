from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from tests.unit.test_mlb_daily_per_game_lock import (
    build_module,
    game,
    persist_latest_prelock_candidates,
    pull,
)
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


def _ddb_value(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(key): _ddb_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_ddb_value(item) for item in value]
    return value


@contextmanager
def _batch_capable_table(
    table,
    *,
    fail_every_call: bool = False,
    malformed_first_response: bool = False,
    return_one_unprocessed_once: bool = False,
):
    serializer = TypeSerializer()
    deserializer = TypeDeserializer()
    counts = {
        "batchGetItem": 0,
        "keys": 0,
        "maxKeys": 0,
        "consistentReads": 0,
        "identities": set(),
    }

    class BatchClient:
        meta = SimpleNamespace(region_name="us-east-1")

        def __init__(self):
            self.returned_unprocessed = False

        def batch_get_item(self, *, RequestItems):
            counts["batchGetItem"] += 1
            if fail_every_call:
                raise TimeoutError("injected batch read failure")
            if malformed_first_response and counts["batchGetItem"] == 1:
                return {
                    "Responses": {
                        "parlay_platform_snapshots": [{"PK": {"S": "ambiguous"}}]
                    }
                }

            assert list(RequestItems) == ["parlay_platform_snapshots"]
            request = RequestItems["parlay_platform_snapshots"]
            assert request.get("ConsistentRead") is True
            wire_keys = list(request.get("Keys") or [])
            assert len(wire_keys) <= 100
            decoded_keys = [
                {
                    name: deserializer.deserialize(value)
                    for name, value in wire_key.items()
                }
                for wire_key in wire_keys
            ]
            identities = {(key["PK"], key["SK"]) for key in decoded_keys}
            assert len(identities) == len(decoded_keys)
            counts["keys"] += len(wire_keys)
            counts["maxKeys"] = max(counts["maxKeys"], len(wire_keys))
            counts["consistentReads"] += 1
            counts["identities"].update(identities)

            unprocessed = []
            if (
                return_one_unprocessed_once
                and wire_keys
                and not self.returned_unprocessed
            ):
                self.returned_unprocessed = True
                unprocessed = [wire_keys[-1]]
                wire_keys = wire_keys[:-1]

            items = []
            for wire_key in wire_keys:
                key = {
                    name: deserializer.deserialize(value)
                    for name, value in wire_key.items()
                }
                item = table.items.get((key["PK"], key["SK"]))
                if item is None:
                    continue
                items.append({
                    name: serializer.serialize(_ddb_value(value))
                    for name, value in item.items()
                })
            response = {"Responses": {"parlay_platform_snapshots": items}}
            if unprocessed:
                response["UnprocessedKeys"] = {
                    "parlay_platform_snapshots": {"Keys": unprocessed}
                }
            return response

    sentinel = object()
    previous_name = getattr(table, "name", sentinel)
    previous_meta = getattr(table, "meta", sentinel)
    table.name = "parlay_platform_snapshots"
    table.meta = SimpleNamespace(client=BatchClient())
    try:
        yield counts
    finally:
        if previous_name is sentinel:
            delattr(table, "name")
        else:
            table.name = previous_name
        if previous_meta is sentinel:
            delattr(table, "meta")
        else:
            table.meta = previous_meta


@pytest.fixture(scope="module")
def locked_scale_module():
    games = _games("2026-07-13T18:00:00+00:00")
    module = build_module(
        _pulls(games),
        "2026-07-13T17:15:05+00:00",
        seed=False,
    )
    # Mirror DynamoDB's numeric round trip before the immutable fingerprints
    # are created. The shared FakeHistory intentionally keeps raw Python
    # floats for most unit tests, while real resource reads return Decimal.
    module.history.ddb_safe = staticmethod(history_contract.ddb_safe)
    persist_latest_prelock_candidates(module, module.history.pulls)
    result = module.run_lock(SLATE)
    assert result["locked"] is True
    assert result["perGameLockProgress"]["lockedPredictionCount"] == GAME_COUNT
    return module


def test_locked_status_request_cache_preserves_payload_and_bounds_reads(
    locked_scale_module,
):
    module = locked_scale_module
    # This is the exact #57 request-cache behavior without BatchGet support.
    with _count_table_reads(module.TABLE) as sequential_counts:
        sequential = module._status_payload(SLATE)

    with _batch_capable_table(module.TABLE) as batch_counts:
        with _count_table_reads(module.TABLE) as cached_counts:
            cached = module._status_payload(SLATE)

    assert cached == sequential
    assert cached["gameCount"] == GAME_COUNT
    assert cached["pullCount"] == PULL_COUNT
    assert cached["lockedPredictionCount"] == GAME_COUNT
    assert cached_counts["getItem"] <= 35
    assert cached_counts["query"] == GAME_COUNT
    assert batch_counts["batchGetItem"] <= 5
    assert batch_counts["maxKeys"] <= 100
    assert batch_counts["keys"] >= GAME_COUNT * 8
    first_stage = next(
        item
        for item in module.TABLE.items.values()
        if item.get("record_type") == patch.STAGE_RECORD_TYPE
    )
    first_source = (first_stage.get("source_window") or {}).get("pulls")[0]
    assert (
        f"PULLS#mlb#{SLATE}",
        f"PULL#{first_source['pulledAtUtc']}#{first_source['pullId']}",
    ) in batch_counts["identities"]
    assert sequential_counts["getItem"] >= 200
    assert sequential_counts["getItem"] > cached_counts["getItem"] * 6


def test_prelock_status_15_games_65_pulls_has_bounded_reads():
    games = _games("2026-07-13T21:00:00+00:00")
    module = build_module(
        _pulls(games),
        "2026-07-13T15:00:00+00:00",
        seed=False,
    )

    with _batch_capable_table(module.TABLE) as batch_counts:
        with _count_table_reads(module.TABLE) as counts:
            payload = module._status_payload(SLATE)

    assert payload["gameCount"] == GAME_COUNT
    assert payload["pullCount"] == PULL_COUNT
    assert payload["lockedPredictionCount"] == 0
    assert len(payload["perGameStatus"]) == GAME_COUNT
    assert payload["operationalDefect"] is False
    assert counts["getItem"] <= 20
    assert counts["query"] == GAME_COUNT
    assert batch_counts["batchGetItem"] == 2
    assert batch_counts["keys"] == GAME_COUNT * 8


def test_batch_status_snapshot_failure_falls_back_without_changing_payload(
    locked_scale_module,
):
    module = locked_scale_module
    with _count_table_reads(module.TABLE) as baseline_counts:
        baseline = module._status_payload(SLATE)

    with _batch_capable_table(module.TABLE, fail_every_call=True) as batch_counts:
        with _count_table_reads(module.TABLE) as fallback_counts:
            fallback = module._status_payload(SLATE)

    assert fallback == baseline
    assert batch_counts["batchGetItem"] >= 1
    assert fallback_counts == baseline_counts


def test_batch_status_snapshot_retries_only_unprocessed_keys(
    locked_scale_module,
):
    module = locked_scale_module
    keys = [
        {"PK": item[0], "SK": item[1]}
        for item in list(module.TABLE.items)[:3]
    ]
    assert len(keys) == 3

    with _batch_capable_table(
        module.TABLE,
        return_one_unprocessed_once=True,
    ) as batch_counts:
        with _count_table_reads(module.TABLE) as direct_counts:
            with patch._status_read_scope():
                patch._prime_consistent_items(module.TABLE, keys)
                observed = [patch._consistent_item(module.TABLE, key) for key in keys]

    assert all(isinstance(item, dict) for item in observed)
    assert direct_counts["getItem"] == 0
    assert batch_counts["batchGetItem"] == 2
    assert batch_counts["keys"] == 4


def test_malformed_batch_response_is_not_cached_as_absence(
    locked_scale_module,
):
    module = locked_scale_module
    keys = [
        {"PK": item[0], "SK": item[1]}
        for item in list(module.TABLE.items)[:3]
    ]

    with _batch_capable_table(
        module.TABLE,
        malformed_first_response=True,
    ) as batch_counts:
        with _count_table_reads(module.TABLE) as direct_counts:
            with patch._status_read_scope():
                patch._prime_consistent_items(module.TABLE, keys)
                observed = [patch._consistent_item(module.TABLE, key) for key in keys]

    assert all(isinstance(item, dict) for item in observed)
    assert batch_counts["batchGetItem"] == 1
    assert direct_counts["getItem"] == len(keys)


def test_status_scope_preserves_strict_stage_and_canonical_read_failures(
    locked_scale_module,
    monkeypatch,
):
    module = locked_scale_module
    manifest = list(
        (module.history.pulls[-1].get("provider_schedule_manifest") or {}).get(
            "games"
        )
        or []
    )
    target_game = manifest[0]
    stage_key = patch._stage_key(module, SLATE, target_game)
    stage = module.TABLE.items[(stage_key["PK"], stage_key["SK"])]
    row = (stage.get("data") or {}).get("row") or {}
    canonical_key = patch._canonical_locked_key(row)
    original_get_item = module.TABLE.get_item

    for failed_key, reader in (
        (
            stage_key,
            lambda: patch._get_stage(module, SLATE, target_game),
        ),
        (
            canonical_key,
            lambda: patch._canonical_readback(module, row),
        ),
    ):
        def failing_get_item(*, Key, ConsistentRead=False):
            if Key == failed_key:
                raise TimeoutError("injected strict read failure")
            return original_get_item(Key=Key, ConsistentRead=ConsistentRead)

        with monkeypatch.context() as context:
            context.setattr(module.TABLE, "get_item", failing_get_item)
            with patch._status_read_scope():
                with pytest.raises(TimeoutError, match="injected strict read failure"):
                    reader()


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
