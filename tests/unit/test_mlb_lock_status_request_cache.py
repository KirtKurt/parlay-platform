from __future__ import annotations

import copy
import importlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

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


class _BatchResource:
    def __init__(self, table, *, handler=None):
        self.table = table
        self.handler = handler
        self.calls = []

    def batch_get_item(self, *, RequestItems):
        self.calls.append(copy.deepcopy(RequestItems))
        if self.handler is not None:
            return self.handler(RequestItems, len(self.calls))
        table_name, request = next(iter(RequestItems.items()))
        items = []
        for key in request.get("Keys") or []:
            item = self.table.items.get((key["PK"], key["SK"]))
            if item is not None:
                items.append(copy.deepcopy(item))
        return {
            "Responses": {table_name: items},
            "UnprocessedKeys": {},
        }


def _install_batch_reader(module):
    module.TABLE.name = "parlay_platform_snapshots"
    resource = _BatchResource(module.TABLE)
    module.DDB = resource
    module.history.DDB = resource
    return resource


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


def _live_shaped_duplicate_pulls(games):
    first = datetime.fromisoformat("2026-07-13T00:00:00+00:00")
    rows = []
    for slot in range(80):
        repeats = 3 if slot < 58 else 2
        for duplicate in range(repeats):
            pulled_at = first + timedelta(
                minutes=15 * slot + duplicate,
            )
            rows.append(
                pull(
                    pulled_at.isoformat(),
                    games,
                    f"live-{slot:03d}-{duplicate}",
                )
            )
    assert len(rows) == 218
    return rows


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
    monkeypatch,
):
    module = locked_scale_module
    resource = _install_batch_reader(module)
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
    assert cached_counts["getItem"] == 1
    assert cached_counts["query"] == GAME_COUNT
    assert len(resource.calls) == 3
    assert uncached_counts["getItem"] > cached_counts["getItem"] * 8


def test_prelock_status_15_games_65_pulls_has_bounded_reads():
    games = _games("2026-07-13T21:00:00+00:00")
    module = build_module(
        _pulls(games),
        "2026-07-13T15:00:00+00:00",
        seed=False,
    )
    resource = _install_batch_reader(module)

    with _count_table_reads(module.TABLE) as counts:
        payload = module._status_payload(SLATE)

    assert payload["gameCount"] == GAME_COUNT
    assert payload["pullCount"] == PULL_COUNT
    assert payload["lockedPredictionCount"] == 0
    assert len(payload["perGameStatus"]) == GAME_COUNT
    assert payload["operationalDefect"] is False
    assert counts["getItem"] <= 5
    assert counts["query"] == GAME_COUNT
    assert len(resource.calls) == 2


def test_batch_status_snapshot_failure_falls_back_without_changing_payload(
    locked_scale_module,
    monkeypatch,
):
    module = locked_scale_module
    _install_batch_reader(module)
    baseline = module._status_payload(SLATE)

    def fail_batch(_request_items, _call_number):
        raise TimeoutError("injected batch read failure")

    failing_resource = _BatchResource(module.TABLE, handler=fail_batch)
    with monkeypatch.context() as context:
        context.setattr(module, "DDB", failing_resource)
        context.setattr(module.history, "DDB", failing_resource)
        with _count_table_reads(module.TABLE) as fallback_counts:
            fallback = module._status_payload(SLATE)

    assert fallback == baseline
    assert len(failing_resource.calls) >= 1
    assert fallback_counts["getItem"] > 0


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
    identity = str(
        row.get("gameIdentity")
        or row.get("gameId")
        or row.get("game_id")
    )
    commence = str(row.get("commenceTime") or row.get("commence_time"))
    canonical_key = {
        "PK": f"GAME_WINNERS#mlb#{SLATE}",
        "SK": f"LOCKED#GAME#{commence}#{identity}",
    }

    for table, failed_key, reader in (
        (
            module.TABLE,
            stage_key,
            lambda: patch._get_stage(module, SLATE, target_game),
        ),
        (
            module.history.PULLS,
            canonical_key,
            lambda: patch._canonical_readback(module, row),
        ),
    ):
        original_get_item = table.get_item

        def failing_get_item(*, Key, ConsistentRead=False):
            if Key == failed_key:
                raise TimeoutError("injected strict read failure")
            return original_get_item(Key=Key, ConsistentRead=ConsistentRead)

        with monkeypatch.context() as context:
            context.setattr(table, "get_item", failing_get_item)
            with patch._status_read_scope():
                with pytest.raises(TimeoutError, match="injected strict read failure"):
                    reader()


def test_candidate_legacy_source_pull_reference_is_in_exact_inventory():
    primary_key = {"PK": "LOCKED_PICKS#mlb#2026-07-13", "SK": "STAGE"}
    pulled_at = "2026-07-13T16:45:00+00:00"
    item = {
        **primary_key,
        "record_type": patch.STAGE_RECORD_TYPE,
        "candidate_proof": {
            "pk": "LOCKED_PICKS#mlb#2026-07-13",
            "sk": "PRELOCK_CANDIDATE#1",
            "predictionSourcePullAtUtc": pulled_at,
            "predictionSourcePullId": "legacy-source",
        },
        "data": {"row": {}},
    }

    class Table:
        name = "parlay_platform_snapshots"
        items = {(primary_key["PK"], primary_key["SK"]): item}

        def get_item(self, *, Key, ConsistentRead):
            assert ConsistentRead is True
            stored = self.items.get((Key["PK"], Key["SK"]))
            return {"Item": copy.deepcopy(stored)} if stored else {}

    table = Table()
    resource = _BatchResource(table)
    with patch._status_read_scope():
        assert patch._prime_status_exact_items(
            table,
            resource,
            [primary_key],
        ) is True
        references = patch._status_authority_reference_keys(
            table,
            [primary_key],
            SLATE,
        )

    assert {
        "PK": f"PULLS#mlb#{SLATE}",
        "SK": f"PULL#{pulled_at}#legacy-source",
    } in references


def test_live_shaped_218_raw_rows_80_slots_stays_exact_key_bounded():
    games = _games("2026-07-13T22:00:00+00:00")
    module = build_module(
        _live_shaped_duplicate_pulls(games),
        "2026-07-13T15:00:00+00:00",
        seed=False,
    )
    original_query = module.history.query_pulls

    def canonical_query(sport, date=None, limit=500):
        raw = original_query(sport, date, 500)
        return history_contract.canonicalize_pull_slots(
            raw,
            sport=sport,
            slate=date,
        )[:limit]

    module.history.query_pulls = canonical_query
    resource = _install_batch_reader(module)

    with _count_table_reads(module.TABLE) as counts:
        payload = module._status_payload(SLATE)

    raw_pull_items = [
        item
        for item in module.TABLE.items.values()
        if item.get("record_type") == "pull_run"
    ]
    assert len(raw_pull_items) == 218
    assert payload["pullCount"] == 80
    assert payload["gameCount"] == GAME_COUNT
    assert payload["lockedPredictionCount"] == 0
    assert payload["operationalDefect"] is False
    assert counts["getItem"] <= 5
    assert counts["query"] == GAME_COUNT
    assert len(resource.calls) == 2
    assert sum(
        len(next(iter(call.values()))["Keys"])
        for call in resource.calls
    ) == GAME_COUNT * 8


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
    resource = _install_batch_reader(module)
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
    assert cached_counts["getItem"] <= 5
    assert cached_counts["query"] <= GAME_COUNT + 1
    assert len(resource.calls) == 3
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


def test_exact_batch_cache_chunks_native_keys_and_proves_absence():
    class Table:
        name = "parlay_platform_snapshots"

        def __init__(self):
            self.items = {
                ("PK", f"SK#{index:03d}"): {
                    "PK": "PK",
                    "SK": f"SK#{index:03d}",
                    "data": {"index": index},
                }
                for index in range(205)
            }
            self.get_calls = 0

        def get_item(self, *, Key, ConsistentRead):
            assert ConsistentRead is True
            self.get_calls += 1
            item = self.items.get((Key["PK"], Key["SK"]))
            return {"Item": copy.deepcopy(item)} if item else {}

    table = Table()
    resource = _BatchResource(table)
    keys = [
        {"PK": "PK", "SK": f"SK#{index:03d}"}
        for index in range(206)
    ]

    with patch._status_read_scope():
        assert patch._prime_status_exact_items(table, resource, keys) is True
        first = patch._consistent_item(table, keys[0])
        first["data"]["index"] = -1
        assert patch._consistent_item(table, keys[0])["data"]["index"] == 0
        assert patch._consistent_item(table, keys[-1]) is None

    assert table.get_calls == 0
    assert [
        len(next(iter(call.values()))["Keys"])
        for call in resource.calls
    ] == [100, 100, 6]
    assert all(
        next(iter(call.values()))["ConsistentRead"] is True
        for call in resource.calls
    )


def test_exact_batch_cache_uses_native_boto3_resource_response():
    import boto3
    from botocore.stub import Stubber

    resource = boto3.resource(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    table = resource.Table("parlay_platform_snapshots")
    key = {"PK": "PK", "SK": "SK"}
    stubber = Stubber(resource.meta.client)
    stubber.add_response(
        "batch_get_item",
        {
            "Responses": {
                table.name: [
                    {
                        "PK": {"S": "PK"},
                        "SK": {"S": "SK"},
                        "value": {"N": "2"},
                    }
                ]
            },
            "UnprocessedKeys": {},
        },
        {
            "RequestItems": {
                table.name: {
                    "Keys": [
                        {
                            "PK": "PK",
                            "SK": "SK",
                        }
                    ],
                    "ConsistentRead": True,
                }
            }
        },
    )

    with stubber, patch._status_read_scope():
        assert patch._prime_status_exact_items(table, resource, [key]) is True
        assert patch._consistent_item(table, key) == {
            "PK": "PK",
            "SK": "SK",
            "value": Decimal("2"),
        }
    stubber.assert_no_pending_responses()


def test_exact_batch_cache_retries_unprocessed_without_partial_publication(
    monkeypatch,
):
    class Table:
        name = "parlay_platform_snapshots"
        items = {
            ("PK", "A"): {"PK": "PK", "SK": "A", "value": 1},
            ("PK", "B"): {"PK": "PK", "SK": "B", "value": 2},
        }

    table = Table()
    keys = [{"PK": "PK", "SK": "A"}, {"PK": "PK", "SK": "B"}]

    def handler(request_items, call_number):
        request = request_items[table.name]
        assert request["ConsistentRead"] is True
        if call_number == 1:
            assert patch._STATUS_READ_CACHE.get()["consistentItems"] == {}
            return {
                "Responses": {table.name: [copy.deepcopy(table.items[("PK", "A")])]},
                "UnprocessedKeys": {
                    table.name: {
                        "Keys": [{"PK": "PK", "SK": "B"}],
                        "ConsistentRead": True,
                    }
                },
            }
        assert patch._STATUS_READ_CACHE.get()["consistentItems"] == {}
        assert request["Keys"] == [{"PK": "PK", "SK": "B"}]
        return {
            "Responses": {table.name: [copy.deepcopy(table.items[("PK", "B")])]},
            "UnprocessedKeys": {},
        }

    resource = _BatchResource(table, handler=handler)
    sleeps = []
    monkeypatch.setattr(patch.time, "sleep", sleeps.append)

    with patch._status_read_scope():
        assert patch._prime_status_exact_items(table, resource, keys) is True
        assert patch._consistent_item(table, keys[0])["value"] == 1
        assert patch._consistent_item(table, keys[1])["value"] == 2

    assert len(resource.calls) == 2
    assert sleeps == [patch._STATUS_BATCH_RETRY_DELAY_SECONDS]


def test_exact_batch_cache_consumes_progressive_single_partition_pages(
    monkeypatch,
):
    class Table:
        name = "parlay_platform_snapshots"

        def __init__(self):
            self.get_calls = 0
            self.items = {
                ("PULLS#mlb#2026-07-21", f"PULL#{index:03d}"): {
                    "PK": "PULLS#mlb#2026-07-21",
                    "SK": f"PULL#{index:03d}",
                    "value": index,
                }
                for index in range(65)
            }

        def get_item(self, *, Key, ConsistentRead):
            assert ConsistentRead is True
            self.get_calls += 1
            item = self.items.get((Key["PK"], Key["SK"]))
            return {"Item": copy.deepcopy(item)} if item else {}

    table = Table()
    keys = [
        {"PK": "PULLS#mlb#2026-07-21", "SK": f"PULL#{index:03d}"}
        for index in range(65)
    ]

    def handler(request_items, _call):
        request = request_items[table.name]
        assert request["ConsistentRead"] is True
        pending = list(request["Keys"])
        processed = pending[:8]
        unprocessed = pending[8:]
        return {
            "Responses": {
                table.name: [
                    copy.deepcopy(table.items[(key["PK"], key["SK"])])
                    for key in processed
                ]
            },
            "UnprocessedKeys": (
                {
                    table.name: {
                        "Keys": copy.deepcopy(unprocessed),
                        "ConsistentRead": True,
                    }
                }
                if unprocessed
                else {}
            ),
        }

    resource = _BatchResource(table, handler=handler)
    sleeps = []
    monkeypatch.setattr(patch.time, "sleep", sleeps.append)

    with patch._status_read_scope():
        assert patch._prime_status_exact_items(table, resource, keys) is True
        assert len(patch._STATUS_READ_CACHE.get()["consistentItems"]) == len(keys)
        assert patch._consistent_item(table, keys[0])["value"] == 0
        assert patch._consistent_item(table, keys[-1])["value"] == 64

    assert len(resource.calls) == 9
    assert [
        len(next(iter(call.values()))["Keys"])
        for call in resource.calls
    ] == [65, 57, 49, 41, 33, 25, 17, 9, 1]
    assert sleeps == [patch._STATUS_BATCH_RETRY_DELAY_SECONDS] * 8
    assert table.get_calls == 0


def test_exact_batch_cache_progressive_pages_obey_call_bound(
    monkeypatch,
):
    class Table:
        name = "parlay_platform_snapshots"

        def __init__(self):
            self.get_calls = 0
            self.items = {
                ("PK", f"SK#{index:03d}"): {
                    "PK": "PK",
                    "SK": f"SK#{index:03d}",
                    "value": index,
                }
                for index in range(10)
            }

        def get_item(self, *, Key, ConsistentRead):
            assert ConsistentRead is True
            self.get_calls += 1
            item = self.items.get((Key["PK"], Key["SK"]))
            return {"Item": copy.deepcopy(item)} if item else {}

    table = Table()
    keys = [
        {"PK": "PK", "SK": f"SK#{index:03d}"}
        for index in range(10)
    ]

    def handler(request_items, _call):
        pending = list(request_items[table.name]["Keys"])
        processed = pending[:1]
        unprocessed = pending[1:]
        return {
            "Responses": {
                table.name: [
                    copy.deepcopy(table.items[(key["PK"], key["SK"])])
                    for key in processed
                ]
            },
            "UnprocessedKeys": (
                {table.name: {"Keys": copy.deepcopy(unprocessed)}}
                if unprocessed
                else {}
            ),
        }

    resource = _BatchResource(table, handler=handler)
    monkeypatch.setattr(patch, "_STATUS_BATCH_MAX_CALLS_PER_PHASE", 4)
    monkeypatch.setattr(patch.time, "sleep", lambda _seconds: None)

    with patch._status_read_scope():
        assert patch._prime_status_exact_items(table, resource, keys) is False
        assert patch._STATUS_READ_CACHE.get()["consistentItems"] == {}
        assert patch._consistent_item(table, keys[0])["value"] == 0

    assert len(resource.calls) == 4
    assert table.get_calls == 1


@pytest.mark.parametrize(
    "malformed",
    (
        {"Responses": {}, "UnprocessedKeys": {}},
        {
            "Responses": {"parlay_platform_snapshots": None},
            "UnprocessedKeys": {},
        },
        {
            "Responses": {"parlay_platform_snapshots": []},
            "UnprocessedKeys": {"parlay_platform_snapshots": None},
        },
        {
            "Responses": {"parlay_platform_snapshots": []},
            "UnprocessedKeys": {
                "parlay_platform_snapshots": {"Keys": None},
            },
        },
        {
            "Responses": {
                "parlay_platform_snapshots": [
                    {"PK": "PK", "SK": "UNREQUESTED"},
                ]
            },
            "UnprocessedKeys": {},
        },
        {
            "Responses": {"parlay_platform_snapshots": []},
            "UnprocessedKeys": {"unexpected_table": {"Keys": []}},
        },
    ),
)
def test_malformed_exact_batch_never_publishes_cached_absence(malformed):
    key = {"PK": "PK", "SK": "A"}

    class Table:
        name = "parlay_platform_snapshots"

        def __init__(self):
            self.calls = 0

        def get_item(self, *, Key, ConsistentRead):
            assert Key == key
            assert ConsistentRead is True
            self.calls += 1
            return {"Item": {**key, "value": "point-read"}}

    table = Table()
    resource = _BatchResource(
        table,
        handler=lambda _request, _call: copy.deepcopy(malformed),
    )
    with patch._status_read_scope():
        assert patch._prime_status_exact_items(table, resource, [key]) is False
        assert patch._consistent_item(table, key)["value"] == "point-read"
    assert table.calls == 1


def test_residual_unprocessed_exact_batch_falls_back_without_cache(
    monkeypatch,
):
    key = {"PK": "PK", "SK": "A"}

    class Table:
        name = "parlay_platform_snapshots"

        def __init__(self):
            self.calls = 0

        def get_item(self, *, Key, ConsistentRead):
            self.calls += 1
            return {"Item": {**Key, "value": "fallback"}}

    table = Table()

    def handler(request_items, _call):
        return {
            "Responses": {table.name: []},
            "UnprocessedKeys": {
                table.name: {
                    "Keys": copy.deepcopy(request_items[table.name]["Keys"]),
                    "ConsistentRead": True,
                }
            },
        }

    resource = _BatchResource(table, handler=handler)
    monkeypatch.setattr(patch.time, "sleep", lambda _seconds: None)
    with patch._status_read_scope():
        assert patch._prime_status_exact_items(table, resource, [key]) is False
        assert patch._consistent_item(table, key)["value"] == "fallback"
    assert len(resource.calls) == patch._STATUS_BATCH_MAX_ATTEMPTS
    assert table.calls == 1


def test_exact_batch_phase_rejects_unbounded_key_inventory():
    class Table:
        name = "parlay_platform_snapshots"
        items = {}

    table = Table()
    resource = _BatchResource(table)
    keys = [
        {"PK": "PK", "SK": f"SK#{index}"}
        for index in range(patch._STATUS_BATCH_MAX_PHASE_KEYS + 1)
    ]
    with patch._status_read_scope():
        assert patch._prime_status_exact_items(table, resource, keys) is False
        assert patch._STATUS_READ_CACHE.get()["consistentItems"] == {}
    assert resource.calls == []
