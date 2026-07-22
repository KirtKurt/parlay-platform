#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one exact replacement, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Make canonicalization reuse fingerprints already computed by the strongly
# consistent status query. The default public behavior remains unchanged.
replace_once(
    "hello_world/inqsi_pull_history.py",
    '''def _slot_input_metadata(pull: Dict[str, Any]) -> Dict[str, Any]:
    metadata = pull.get("canonicalPullSlot") or {}
    if metadata.get("version") != PULL_SLOT_VERSION:
        fingerprint = pull_payload_fingerprint(pull)
''',
    '''def _slot_input_metadata(
    pull: Dict[str, Any],
    *,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    metadata = pull.get("canonicalPullSlot") or {}
    if metadata.get("version") != PULL_SLOT_VERSION:
        fingerprint = fingerprint or pull_payload_fingerprint(pull)
''',
)
replace_once(
    "hello_world/inqsi_pull_history.py",
    '''def canonicalize_pull_slots(
    pulls: Iterable[Dict[str, Any]],
    *,
    sport: Optional[str] = None,
    slate: Optional[str] = None,
) -> List[Dict[str, Any]]:
''',
    '''def canonicalize_pull_slots(
    pulls: Iterable[Dict[str, Any]],
    *,
    sport: Optional[str] = None,
    slate: Optional[str] = None,
    _precomputed_fingerprints: Optional[Dict[int, str]] = None,
) -> List[Dict[str, Any]]:
''',
)
replace_once(
    "hello_world/inqsi_pull_history.py",
    '''    attached metadata makes any historical duplicate contamination explicit to
    scorers and the T-minus-45 training gate.
    """
    grouped: Dict[
''',
    '''    attached metadata makes any historical duplicate contamination explicit to
    scorers and the T-minus-45 training gate.

    ``_precomputed_fingerprints`` is an internal read-path optimization. Values
    are keyed by object identity and are accepted only for the exact objects
    supplied by the caller; all other rows are fingerprinted normally.
    """
    fingerprint_cache = dict(_precomputed_fingerprints or {})

    def fingerprint_for(pull: Dict[str, Any]) -> str:
        token = id(pull)
        if token not in fingerprint_cache:
            fingerprint_cache[token] = pull_payload_fingerprint(pull)
        return fingerprint_cache[token]

    grouped: Dict[
''',
)
replace_once(
    "hello_world/inqsi_pull_history.py",
    '''    raw_variants_by_slot: Dict[str, List[Dict[str, Any]]] = {}
''',
    '''    raw_variants_by_slot: Dict[str, List[Tuple[Dict[str, Any], str]]] = {}
''',
)
replace_once(
    "hello_world/inqsi_pull_history.py",
    '''        slot_text = slot.isoformat()
        inherited = _slot_input_metadata(raw)
        raw_count_by_slot[slot_text] = raw_count_by_slot.get(slot_text, 0) + int(
            inherited["rawPullCount"]
        )
        raw_ids_by_slot.setdefault(slot_text, []).extend(inherited["rawPullIds"])
        raw_fingerprints_by_slot.setdefault(slot_text, []).extend(
            inherited["rawPullFingerprints"]
        )
        inherited_variants = raw.get("_canonicalSlotRawPulls")
        variants = (
            inherited_variants
            if isinstance(inherited_variants, list) and inherited_variants
            else [raw]
        )
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            raw_variant = copy.deepcopy(variant)
            raw_variant.pop("canonicalPullSlot", None)
            raw_variant.pop("_canonicalSlotRawPulls", None)
            raw_variants_by_slot.setdefault(slot_text, []).append(raw_variant)
''',
    '''        slot_text = slot.isoformat()
        raw_fingerprint = fingerprint_for(raw)
        inherited = _slot_input_metadata(raw, fingerprint=raw_fingerprint)
        raw_count_by_slot[slot_text] = raw_count_by_slot.get(slot_text, 0) + int(
            inherited["rawPullCount"]
        )
        raw_ids_by_slot.setdefault(slot_text, []).extend(inherited["rawPullIds"])
        raw_fingerprints_by_slot.setdefault(slot_text, []).extend(
            inherited["rawPullFingerprints"]
        )
        inherited_variants = raw.get("_canonicalSlotRawPulls")
        variants = (
            inherited_variants
            if isinstance(inherited_variants, list) and inherited_variants
            else [raw]
        )
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            variant_fingerprint = (
                raw_fingerprint if variant is raw else fingerprint_for(variant)
            )
            raw_variant = copy.deepcopy(variant)
            raw_variant.pop("canonicalPullSlot", None)
            raw_variant.pop("_canonicalSlotRawPulls", None)
            raw_variants_by_slot.setdefault(slot_text, []).append(
                (raw_variant, variant_fingerprint)
            )
''',
)
replace_once(
    "hello_world/inqsi_pull_history.py",
    '''        fingerprint = pull_payload_fingerprint(raw)
        grouped.setdefault(slot_text, []).append(
''',
    '''        fingerprint = raw_fingerprint
        grouped.setdefault(slot_text, []).append(
''',
)
replace_once(
    "hello_world/inqsi_pull_history.py",
    '''        canonical["_canonicalSlotRawPulls"] = sorted(
            raw_variants_by_slot.get(slot_text, []),
            key=lambda pull: (
                _parse_utc(pull.get("pulled_at"))
                or datetime.min.replace(tzinfo=timezone.utc),
                str(pull.get("pull_id") or ""),
                pull_payload_fingerprint(pull),
            ),
        )
''',
    '''        canonical["_canonicalSlotRawPulls"] = [
            variant
            for variant, _ in sorted(
                raw_variants_by_slot.get(slot_text, []),
                key=lambda entry: (
                    _parse_utc(entry[0].get("pulled_at"))
                    or datetime.min.replace(tzinfo=timezone.utc),
                    str(entry[0].get("pull_id") or ""),
                    entry[1],
                ),
            )
        ]
''',
)

# Extend the request-local status snapshot with exact immutable pull hashes and
# a production-only strongly consistent query path that also primes item reads.
replace_once(
    "hello_world/mlb_daily_per_game_lock_patch.py",
    '''    token = _STATUS_READ_CACHE.set({
        "consistentItems": {},
        "canonicalPulls": {},
    })
''',
    '''    token = _STATUS_READ_CACHE.set({
        "consistentItems": {},
        "canonicalPulls": {},
        "pullFingerprints": {},
    })
''',
)
replace_once(
    "hello_world/mlb_daily_per_game_lock_patch.py",
    '''    return False, None


def _status_batch_resource(table: Any, *owners: Any) -> Optional[Any]:
''',
    '''    return False, None


def _status_pull_payload_fingerprint(
    table: Any,
    key: Dict[str, str],
    pull: Dict[str, Any],
) -> str:
    """Hash one immutable pull at most once within a read-only status request."""
    request_cache = _STATUS_READ_CACHE.get()
    fingerprint_cache = (
        request_cache.get("pullFingerprints")
        if isinstance(request_cache, dict)
        else None
    )
    cache_key = _status_item_cache_key(table, key)
    if isinstance(fingerprint_cache, dict) and cache_key in fingerprint_cache:
        return str(fingerprint_cache[cache_key])
    fingerprint = history_contract.pull_payload_fingerprint(pull)
    if isinstance(fingerprint_cache, dict):
        fingerprint_cache[cache_key] = fingerprint
    return fingerprint


def _status_query_pulls(module: Any, slate: str) -> List[Dict[str, Any]]:
    """Read and canonicalize one exact pull snapshot without rereading its rows.

    Production's private item reader performs a strongly consistent DynamoDB
    query. Its exact outer items are safe to publish into this request's item
    cache, while their data rows are canonicalized with one precomputed hash per
    raw pull. Injected adapters without that reader retain the original path.
    """
    request_cache = _STATUS_READ_CACHE.get()
    item_cache = (
        request_cache.get("consistentItems")
        if isinstance(request_cache, dict)
        else None
    )
    canonical_cache = (
        request_cache.get("canonicalPulls")
        if isinstance(request_cache, dict)
        else None
    )
    fingerprint_cache = (
        request_cache.get("pullFingerprints")
        if isinstance(request_cache, dict)
        else None
    )
    table = getattr(module.history, "PULLS", None)
    item_reader = getattr(module.history, "_query_pull_items", None)
    canonicalize = getattr(module.history, "canonicalize_pull_slots", None)
    if (
        not isinstance(item_cache, dict)
        or table is None
        or not callable(item_reader)
        or not callable(canonicalize)
    ):
        return module._pulls_for_date(slate)

    raw_items = item_reader("mlb", slate, 500)
    raw_pulls: List[Dict[str, Any]] = []
    precomputed: Dict[int, str] = {}
    filter_pull = getattr(module.history, "_filter_mlb_model_pull", None)
    parser = getattr(module.history, "_parse_utc", None)
    for item in raw_items:
        if not isinstance(item, dict) or item.get("record_type") != "pull_run":
            continue
        pull = copy.deepcopy(item.get("data") or {})
        if not isinstance(pull, dict) or not pull:
            continue
        key = {
            "PK": str(item.get("PK") or ""),
            "SK": str(item.get("SK") or ""),
        }
        if not key["PK"] or not key["SK"]:
            continue

        persisted_item = copy.deepcopy(item)
        persisted_pull = persisted_item.get("data") or {}
        if isinstance(persisted_pull, dict):
            persisted_pull.pop("canonicalPullStorage", None)
        item_cache[_status_item_cache_key(table, key)] = persisted_item

        filtered = filter_pull(pull) if callable(filter_pull) else pull
        if not isinstance(filtered, dict):
            continue
        fingerprint = history_contract.pull_payload_fingerprint(filtered)
        precomputed[id(filtered)] = fingerprint
        raw_pulls.append(filtered)
        if isinstance(fingerprint_cache, dict):
            fingerprint_cache[_status_item_cache_key(table, key)] = (
                fingerprint
                if filtered is pull
                else history_contract.pull_payload_fingerprint(persisted_pull)
            )

    def pulled_at(value: Dict[str, Any]) -> datetime:
        parsed = parser(value.get("pulled_at")) if callable(parser) else None
        return parsed or datetime.min.replace(tzinfo=timezone.utc)

    raw_pulls.sort(
        key=lambda pull: (
            pulled_at(pull),
            str(pull.get("pull_id") or ""),
            precomputed[id(pull)],
        )
    )
    canonical = canonicalize(
        raw_pulls,
        sport="mlb",
        slate=slate,
        _precomputed_fingerprints=precomputed,
    )[:500]
    if isinstance(canonical_cache, dict):
        canonical_cache[id(canonical)] = canonical
    return canonical


def _status_batch_resource(table: Any, *owners: Any) -> Optional[Any]:
''',
)
replace_once(
    "hello_world/mlb_daily_per_game_lock_patch.py",
    '''        scoped = copy.deepcopy(pull)
        scoped["games"] = [copy.deepcopy(matching)]
        selected.append(scoped)
''',
    '''        if isinstance(request_cache, dict):
            # Status validation never selects a new candidate from raw variants.
            # Keep exact slot/storage/provenance fields, but avoid copying every
            # full-slate game and raw duplicate once per game in the manifest.
            scoped = {
                key: copy.deepcopy(value)
                for key, value in pull.items()
                if key not in {"games", "_canonicalSlotRawPulls"}
            }
        else:
            scoped = copy.deepcopy(pull)
        scoped["games"] = [copy.deepcopy(matching)]
        selected.append(scoped)
''',
)
replace_once(
    "hello_world/mlb_daily_per_game_lock_patch.py",
    '''        source_item = _consistent_item(table, source_key)
        source_pull = (source_item or {}).get("data") or {}
        if not source_item:
            errors.append("candidate_source_pull_readback_missing")
        elif (
            source_item.get("record_type") != "pull_run"
            or str(source_pull.get("pull_id") or "") != source_id
            or _parse_iso(source_pull.get("pulled_at")) != _parse_iso(source_at_text)
            or history_contract.pull_payload_fingerprint(source_pull)
            != str(proof.get("predictionSourcePullFingerprint") or "")
        ):
''',
    '''        source_item = _consistent_item(table, source_key)
        source_pull = (source_item or {}).get("data") or {}
        if not source_item:
            errors.append("candidate_source_pull_readback_missing")
        elif (
            source_item.get("record_type") != "pull_run"
            or str(source_pull.get("pull_id") or "") != source_id
            or _parse_iso(source_pull.get("pulled_at")) != _parse_iso(source_at_text)
            or _status_pull_payload_fingerprint(
                table,
                source_key,
                source_pull,
            )
            != str(proof.get("predictionSourcePullFingerprint") or "")
        ):
''',
)
replace_once(
    "hello_world/mlb_daily_per_game_lock_patch.py",
    '''        pull_item = _consistent_item(table, {
            "PK": entry.get("pullStoragePk") or f"PULLS#mlb#{item.get('slate_date')}",
            "SK": entry.get("pullStorageSk") or f"PULL#{entry.get('pulledAtUtc')}#{pull_id}",
        })
''',
    '''        pull_key = {
            "PK": entry.get("pullStoragePk") or f"PULLS#mlb#{item.get('slate_date')}",
            "SK": entry.get("pullStorageSk") or f"PULL#{entry.get('pulledAtUtc')}#{pull_id}",
        }
        pull_item = _consistent_item(table, pull_key)
''',
)
replace_once(
    "hello_world/mlb_daily_per_game_lock_patch.py",
    '''        if history_contract.pull_payload_fingerprint(pull) != str(
            entry.get("canonicalPullFingerprint") or ""
        ):
''',
    '''        if _status_pull_payload_fingerprint(table, pull_key, pull) != str(
            entry.get("canonicalPullFingerprint") or ""
        ):
''',
)
replace_once(
    "hello_world/mlb_daily_per_game_lock_patch.py",
    '''        pulls = sorted(module._pulls_for_date(slate), key=lambda pull: _pull_at(module, pull) or datetime.min.replace(tzinfo=timezone.utc))
        manifest = module._latest_games_for_date(slate, pulls)
''',
    '''        pulls = sorted(
            _status_query_pulls(module, slate),
            key=lambda pull: _pull_at(module, pull)
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        manifest = module._latest_games_for_date(slate, pulls)
''',
)

# Add exact semantic and request-snapshot regressions.
replace_once(
    "tests/unit/test_mlb_lock_status_request_cache.py",
    '''from decimal import Decimal

import pytest
''',
    '''from decimal import Decimal
from types import SimpleNamespace

import pytest
''',
)
replace_once(
    "tests/unit/test_mlb_lock_status_request_cache.py",
    '''def test_status_cache_shares_absence_but_retries_transport_errors():
''',
    '''def test_precomputed_slot_fingerprints_preserve_exact_canonical_output(
    monkeypatch,
):
    games = _games("2026-07-13T22:00:00+00:00")
    raw = [
        pull("2026-07-13T01:00:00+00:00", games, "raw-a"),
        pull("2026-07-13T01:01:00+00:00", games, "raw-b"),
        pull("2026-07-13T01:15:00+00:00", games, "raw-c"),
    ]
    original = history_contract.pull_payload_fingerprint
    expected = history_contract.canonicalize_pull_slots(
        raw,
        sport="mlb",
        slate=SLATE,
    )
    precomputed = {id(row): original(row) for row in raw}
    calls = 0

    def counted(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(history_contract, "pull_payload_fingerprint", counted)
    actual = history_contract.canonicalize_pull_slots(
        raw,
        sport="mlb",
        slate=SLATE,
        _precomputed_fingerprints=precomputed,
    )

    assert actual == expected
    assert calls == 0


def test_status_query_reuses_strongly_consistent_pull_rows_and_hashes_once(
    monkeypatch,
):
    games = _games("2026-07-13T22:00:00+00:00")
    raw = [
        pull("2026-07-13T01:00:00+00:00", games, "raw-a"),
        pull("2026-07-13T01:01:00+00:00", games, "raw-b"),
        pull("2026-07-13T01:15:00+00:00", games, "raw-c"),
    ]

    class Table:
        name = "parlay_platform_snapshots"

        def __init__(self):
            self.get_calls = 0

        def get_item(self, **_kwargs):
            self.get_calls += 1
            raise AssertionError("strongly consistent query rows must be reused")

    table = Table()
    items = []
    keys = []
    for index, row in enumerate(raw):
        key = {
            "PK": f"PULLS#mlb#{SLATE}",
            "SK": f"PULL#legacy#{index}",
        }
        keys.append(key)
        stored = copy.deepcopy(row)
        stored["canonicalPullStorage"] = {
            "pk": key["PK"],
            "sk": key["SK"],
            "recordType": "pull_run",
        }
        items.append({
            **key,
            "record_type": "pull_run",
            "data": stored,
        })

    monkeypatch.setattr(history_contract, "PULLS", table)
    monkeypatch.setattr(
        history_contract,
        "_query_pull_items",
        lambda sport, slate, limit: copy.deepcopy(items),
    )
    original = history_contract.pull_payload_fingerprint
    calls = 0

    def counted(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(history_contract, "pull_payload_fingerprint", counted)
    module = SimpleNamespace(
        history=history_contract,
        _pulls_for_date=lambda _slate: (_ for _ in ()).throw(
            AssertionError("optimized status query unexpectedly fell back")
        ),
    )

    with patch._status_read_scope():
        canonical = patch._status_query_pulls(module, SLATE)
        assert len(canonical) == 2
        assert patch._STATUS_READ_CACHE.get()["canonicalPulls"][id(canonical)] is canonical
        for key in keys:
            cached = patch._consistent_item(table, key)
            assert cached["record_type"] == "pull_run"
            assert "canonicalPullStorage" not in cached["data"]
            first = patch._status_pull_payload_fingerprint(table, key, cached["data"])
            second = patch._status_pull_payload_fingerprint(table, key, cached["data"])
            assert first == second

    assert calls == len(raw)
    assert table.get_calls == 0


def test_status_scoring_compacts_raw_variants_but_writer_keeps_them(
    locked_scale_module,
):
    module = locked_scale_module
    pulls = history_contract.canonicalize_pull_slots(
        module.history.pulls,
        sport="mlb",
        slate=SLATE,
    )
    manifest = list(
        (module.history.pulls[-1].get("provider_schedule_manifest") or {}).get(
            "games"
        )
        or []
    )
    target_game = manifest[0]

    writer_scoring = patch._scoring_pulls(module, pulls, target_game)
    assert writer_scoring
    assert "_canonicalSlotRawPulls" in writer_scoring[0]

    with patch._status_read_scope() as request_cache:
        request_cache["canonicalPulls"][id(pulls)] = pulls
        status_scoring = patch._scoring_pulls(module, pulls, target_game)

    assert status_scoring
    assert all("_canonicalSlotRawPulls" not in row for row in status_scoring)
    assert all(len(row.get("games") or []) == 1 for row in status_scoring)
    assert patch._source_window_entries(
        module,
        writer_scoring,
        target_game,
    ) == patch._source_window_entries(
        module,
        status_scoring,
        target_game,
    )


def test_status_cache_shares_absence_but_retries_transport_errors():
''',
)

print("Applied MLB lock-status request snapshot hotpath")
