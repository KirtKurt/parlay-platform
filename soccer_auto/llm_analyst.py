"""Soccer-only LLM analyst for bounded autonomous experiment proposals.

The LLM never emits match probabilities, writes predictions, promotes models,
or changes infrastructure.  It analyzes isolated soccer coverage/model reports
and proposes a small hyperparameter search.  Deterministic validation clamps
every proposal before the chronological ML trainer may evaluate it.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import timedelta
from typing import Any, Mapping, Sequence

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .api import _coverage_summary_universe, _latest_summary_coverage
from .canonical import canonical_json, digest, iso_utc, parse_utc
from .market_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from .storage import SoccerStore, ddb_safe, now_utc, plain


ANALYSIS_VERSION = "soccer-auto-llm-analyst-v3"
ANALYSIS_ORIGIN = "BEDROCK_CONVERSE"
ALLOWED_MODEL_IDS = frozenset(
    {
        "us.amazon.nova-2-lite-v1:0",
        "mistral.ministral-3-14b-instruct",
        "us.meta.llama4-scout-17b-instruct-v1:0",
        "us.meta.llama4-maverick-17b-instruct-v1:0",
        "global.amazon.nova-2-lite-v1:0",
        "us.amazon.nova-pro-v1:0",
        "us.amazon.nova-lite-v1:0",
        "us.amazon.nova-micro-v1:0",
    }
)
MODEL_ID = os.getenv("SOCCER_AUTO_LLM_MODEL_ID", "").strip()
FALLBACK_MODEL_IDS = tuple(
    model_id.strip()
    for model_id in os.getenv(
        "SOCCER_AUTO_LLM_FALLBACK_MODEL_IDS",
        "mistral.ministral-3-14b-instruct,"
        "us.meta.llama4-scout-17b-instruct-v1:0,"
        "us.meta.llama4-maverick-17b-instruct-v1:0,"
        "global.amazon.nova-2-lite-v1:0,us.amazon.nova-pro-v1:0,"
        "us.amazon.nova-lite-v1:0,us.amazon.nova-micro-v1:0",
    ).split(",")
    if model_id.strip()
)
MAX_TRIALS = 2
ANALYSIS_MAX_AGE_HOURS = int(os.getenv("SOCCER_AUTO_LLM_ANALYSIS_MAX_AGE_HOURS", "36"))
DAILY_TOKEN_RETRY_HOURS = 6
TRANSIENT_RETRY_MINUTES = 15
ATTEMPT_RETENTION_DAYS = 30
MAX_BEDROCK_ERROR_MESSAGE_LENGTH = 300
DIAGNOSTIC_SCAN_PAGE_LIMIT = int(os.getenv("SOCCER_AUTO_LLM_DIAGNOSTIC_SCAN_PAGE_LIMIT", "4"))
DIAGNOSTIC_ROW_LIMIT = int(os.getenv("SOCCER_AUTO_LLM_DIAGNOSTIC_ROW_LIMIT", "2000"))
MAX_CONTEXT_CANONICAL_BYTES = 3_000
MAX_BEDROCK_REQUEST_BYTES = 4_800
BEDROCK_MAX_OUTPUT_TOKENS = 384
BEDROCK_CONNECT_TIMEOUT_SECONDS = 3
BEDROCK_READ_TIMEOUT_SECONDS = 30
BEDROCK_CLIENT_CONFIG = Config(
    connect_timeout=BEDROCK_CONNECT_TIMEOUT_SECONDS,
    read_timeout=BEDROCK_READ_TIMEOUT_SECONDS,
    retries={"mode": "standard", "total_max_attempts": 1},
)
MAX_ANALYSIS_SUMMARY_CHARS = 240
MAX_ANALYSIS_LIST_ITEMS = 3
MAX_ANALYSIS_ITEM_CHARS = 160
MAX_ANALYSIS_RATIONALE_CHARS = 160
BOOKMAKER_SAMPLE_LIMIT = 8
MARKET_SAMPLE_LIMIT = 12
EVENT_CYCLE_SAMPLE_LIMIT = 8
MISSING_PAIR_SAMPLE_LIMIT = 10
FAILURE_SAMPLE_LIMIT = 6
MODEL_REPORT_SAMPLE_LIMIT = 4
LIVENESS_FAILURE_SAMPLE_LIMIT = 8
BASELINE_TRIALS: tuple[dict[str, Any], ...] = (
    {"learning_rate": 0.01, "l2": 0.0005, "epochs": 40},
    {"learning_rate": 0.03, "l2": 0.001, "epochs": 60},
    {"learning_rate": 0.05, "l2": 0.005, "epochs": 80},
)

SYSTEM_PROMPT = """You are the research analyst for an autonomous soccer odds model.
You may analyze only the supplied soccer_auto diagnostics. Never invent games,
results, bookmakers, markets, injuries, player statistics, or external facts.
Never recommend using post-kickoff observations for pre-match training. Never
recommend changing MLB, tennis, shared deployment resources, labels, holdouts,
promotion gates, or the immutable T-45 feature lock. The production target is
three-class home/draw/away probability and must outperform de-vigged market
consensus on untouched chronological evidence. Return JSON only.
"""


def _prompt(context: Mapping[str, Any]) -> str:
    schema = {
        "summary": "one string, at most 240 characters",
        "coverage_findings": ["at most 3 strings, each at most 160 characters"],
        "warnings": ["at most 3 strings, each at most 160 characters"],
        "recommended_trials": [
            {
                "learning_rate": "number from 0.005 through 0.1",
                "l2": "number from 0.00001 through 0.1",
                "epochs": "integer from 20 through 120",
                "rationale": "at most 160 characters, grounded only in context",
            }
        ],
    }
    context_json = canonical_json(context)
    if len(context_json.encode("utf-8")) > MAX_CONTEXT_CANONICAL_BYTES:
        raise ValueError("soccer analyst context exceeds its hard byte budget")
    return (
        "Review this soccer-only context and propose at most two distinct "
        "residual-softmax training trials. Do not propose new data sources or "
        "features that are absent from the supplied feature schema. Keep the "
        "entire response under 1,400 characters. Required JSON schema:\n"
        f"{canonical_json(schema)}\nCONTEXT:\n{context_json}"
    )


def _bedrock_request(
    context: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, Any]], int]:
    """Build the exact bounded prompt envelope used by Converse."""
    prompt_text = _prompt(context)
    system = [{"text": SYSTEM_PROMPT}]
    messages = [{"role": "user", "content": [{"text": prompt_text}]}]
    request_byte_count = len(
        canonical_json({"system": system, "messages": messages}).encode("utf-8")
    )
    return system, messages, request_byte_count


def _bounded_text(value: Any, maximum: int = 500) -> str:
    return str(value or "").strip()[:maximum]


def validate_analysis(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Treat all model output as untrusted and admit only bounded trials."""
    trials: list[dict[str, Any]] = []
    seen: set[tuple[float, float, int]] = set()
    raw_trials = payload.get("recommended_trials")
    if not isinstance(raw_trials, list):
        raw_trials = []
    for raw in raw_trials[:MAX_TRIALS]:
        if not isinstance(raw, Mapping):
            continue
        try:
            learning_rate = float(raw["learning_rate"])
            l2 = float(raw["l2"])
            epochs = int(raw["epochs"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0.005 <= learning_rate <= 0.1:
            continue
        if not 0.00001 <= l2 <= 0.1:
            continue
        if not 20 <= epochs <= 120:
            continue
        identity = (round(learning_rate, 8), round(l2, 10), epochs)
        if identity in seen:
            continue
        seen.add(identity)
        trials.append(
            {
                # Persist the same bounded precision used for identity. This
                # keeps the provenance digest stable across DynamoDB's Decimal
                # conversion instead of rejecting our own validated analysis.
                "learning_rate": identity[0],
                "l2": identity[1],
                "epochs": epochs,
                "rationale": _bounded_text(
                    raw.get("rationale"), MAX_ANALYSIS_RATIONALE_CHARS
                ),
            }
        )
    findings = payload.get("coverage_findings") if isinstance(payload.get("coverage_findings"), list) else []
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    result = {
        "analysis_version": ANALYSIS_VERSION,
        "summary": _bounded_text(payload.get("summary"), MAX_ANALYSIS_SUMMARY_CHARS),
        "coverage_findings": [
            _bounded_text(value, MAX_ANALYSIS_ITEM_CHARS)
            for value in findings[:MAX_ANALYSIS_LIST_ITEMS]
        ],
        "warnings": [
            _bounded_text(value, MAX_ANALYSIS_ITEM_CHARS)
            for value in warnings[:MAX_ANALYSIS_LIST_ITEMS]
        ],
        "recommended_trials": trials,
        "validation_status": "VALIDATED",
    }
    if not result["summary"]:
        raise ValueError("LLM analyst response requires a nonempty summary")
    result["analysis_digest"] = digest(result)
    return result


def _extract_json_text(response: Mapping[str, Any]) -> Mapping[str, Any]:
    blocks = (((response.get("output") or {}).get("message") or {}).get("content") or [])
    text = "\n".join(str(block.get("text") or "") for block in blocks if isinstance(block, Mapping)).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    payload = json.loads(text)
    if not isinstance(payload, Mapping):
        raise ValueError("LLM analyst response must be a JSON object")
    return payload


def _diagnostic_rows(store: SoccerStore) -> tuple[list[dict[str, Any]], bool]:
    """Bound the daily diagnostic scan so LLM research cannot tax collection."""
    entity_types = {
        "SOCCER_MARKET_INVENTORY",
        "SOCCER_EVENT_COVERAGE_PLAN",
        "SOCCER_EVENT_COVERAGE_FETCH",
        "SOCCER_COLLECTION_FAILURE",
        "SOCCER_SHARED_PROVIDER_QUOTA_GUARD",
    }
    rows: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"Limit": 500}
    cursor = None
    pages = 0
    while pages < max(1, DIAGNOSTIC_SCAN_PAGE_LIMIT) and len(rows) < max(1, DIAGNOSTIC_ROW_LIMIT):
        response = store.ops.scan(**kwargs)
        pages += 1
        for item in response.get("Items") or []:
            row = plain(item)
            if row.get("entity_type") in entity_types:
                rows.append(row)
                if len(rows) >= max(1, DIAGNOSTIC_ROW_LIMIT):
                    break
        cursor = response.get("LastEvaluatedKey")
        if not cursor:
            break
        kwargs["ExclusiveStartKey"] = cursor
    return rows, bool(cursor)


def _coverage_diagnostics(store: SoccerStore) -> dict[str, Any]:
    rows, truncated = _diagnostic_rows(store)
    inventories = [row for row in rows if row.get("entity_type") == "SOCCER_MARKET_INVENTORY"]
    plans = [row for row in rows if row.get("entity_type") == "SOCCER_EVENT_COVERAGE_PLAN"]
    fetches = [row for row in rows if row.get("entity_type") == "SOCCER_EVENT_COVERAGE_FETCH"]
    failures = [row for row in rows if row.get("entity_type") == "SOCCER_COLLECTION_FAILURE"]
    quota_blocks = [
        row for row in rows if row.get("entity_type") == "SOCCER_SHARED_PROVIDER_QUOTA_GUARD"
    ]

    books: set[str] = set()
    markets: set[str] = set()
    for row in inventories:
        for bookmaker, detail in (row.get("inventory") or {}).items():
            books.add(str(bookmaker))
            markets.update(str(market) for market in detail.get("markets") or [])

    latest_plan_by_event: dict[str, Mapping[str, Any]] = {}
    for plan in plans:
        event_key = str(plan.get("event_key") or "")
        if not event_key:
            continue
        current = latest_plan_by_event.get(event_key)
        if current is None or str(plan.get("observed_at") or "") > str(current.get("observed_at") or ""):
            latest_plan_by_event[event_key] = plan
    returned_by_cycle: dict[tuple[str, str], set[str]] = {}
    for fetch in fetches:
        cycle = (str(fetch.get("event_key") or ""), str(fetch.get("plan_observed_at") or ""))
        if all(cycle):
            returned_by_cycle.setdefault(cycle, set()).update(
                str(pair) for pair in fetch.get("returned_pairs") or []
            )
    expected: set[str] = set()
    returned: set[str] = set()
    cycles = []
    for event_key, plan in latest_plan_by_event.items():
        plan_at = str(plan.get("observed_at") or "")
        cycle_expected = {str(pair) for pair in plan.get("expected_pairs") or []}
        cycle_returned = returned_by_cycle.get((event_key, plan_at), set()) & cycle_expected
        missing = cycle_expected - cycle_returned
        expected.update(f"{event_key}|{pair}" for pair in cycle_expected)
        returned.update(f"{event_key}|{pair}" for pair in cycle_returned)
        cycles.append(
            {
                "event_key": event_key,
                "plan_observed_at": plan_at,
                "expected": len(cycle_expected),
                "fetched": len(cycle_returned),
                "missing": len(missing),
            }
        )
    missing = sorted(expected - returned)
    provider_unavailable: list[str] = []
    normalization_rejected: list[str] = []
    attempted_incomplete: list[str] = []
    quota_deferred: list[str] = []
    failed_pairs: list[str] = []
    never_attempted: list[str] = []
    unresolved: list[str] = list(missing)
    required_missing: list[str] = list(missing)
    expected_batches: list[str] = []
    succeeded_batches: list[str] = []
    failed_batches: list[str] = []
    deferred_batches: list[str] = []
    unresolved_batches: list[str] = []
    discovery_status_counts: dict[str, int] = {}
    coverage_integrity_failures = 0
    coverage_error_cycles = 0
    dispatch_manifest: dict[str, Any] = {}
    if hasattr(store, "latest_coverage_cycles") and hasattr(
        store, "latest_coverage_dispatch_manifest"
    ):
        summaries, dispatch_manifest = _coverage_summary_universe(
            store,
            observed_at=now_utc(),
        )
        exact = _latest_summary_coverage(
            summaries
        )
        expected = set(exact["expected_pairs"])
        returned = set(exact["returned_pairs"])
        missing = sorted(exact["missing_pairs"])
        provider_unavailable = sorted(exact["provider_unavailable_pairs"])
        normalization_rejected = sorted(exact["normalization_rejected_pairs"])
        attempted_incomplete = sorted(exact["attempted_incomplete_pairs"])
        quota_deferred = sorted(exact["quota_deferred_pairs"])
        failed_pairs = sorted(exact["failed_pairs"])
        never_attempted = sorted(exact["never_attempted_pairs"])
        unresolved = sorted(exact["unresolved_pairs"])
        required_missing = sorted(exact["required_missing_pairs"])
        expected_batches = sorted(exact["expected_batch_digests"])
        succeeded_batches = sorted(exact["succeeded_batch_digests"])
        failed_batches = sorted(exact["failed_batch_digests"])
        deferred_batches = sorted(exact["deferred_batch_digests"])
        unresolved_batches = sorted(exact["unresolved_batch_digests"])
        discovery_status_counts = dict(exact["discovery_status_counts"])
        coverage_integrity_failures = int(exact["integrity_failures"])
        coverage_error_cycles = int(exact["coverage_error_cycles"])
        cycles = list(exact["cycles"])
    failures.sort(key=lambda row: str(row.get("observed_at") or ""), reverse=True)
    cycles.sort(key=lambda row: (row["plan_observed_at"], row["event_key"]), reverse=True)
    incomplete_latest_cycles = sum(
        not bool(cycle.get("complete", not int(cycle.get("missing") or 0)))
        for cycle in cycles
    )
    incomplete_request_cycles = sum(
        not bool(cycle.get("request_complete", not int(cycle.get("missing") or 0)))
        for cycle in cycles
    )
    quota_only_incomplete_request_cycles = sum(
        bool(cycle.get("quota_only_incomplete")) for cycle in cycles
    )
    manifest_authoritative = bool(
        not dispatch_manifest or dispatch_manifest.get("authoritative")
    )
    inventory_binding = (
        dispatch_manifest.get("inventory_authority")
        if isinstance(dispatch_manifest.get("inventory_authority"), Mapping)
        else {}
    )
    compact_dispatch_manifest = (
        {
            "authoritative": dispatch_manifest.get("authoritative"),
            "manifest_digest": dispatch_manifest.get("manifest_digest"),
            "manifest_events": dispatch_manifest.get("manifest_events"),
            "manifest_error": dispatch_manifest.get("manifest_error"),
            "inventory_authority_state": dispatch_manifest.get(
                "inventory_authority_state"
            ),
            "inventory_authority_current": dispatch_manifest.get(
                "inventory_authority_current"
            ),
            "inventory_authority_version": inventory_binding.get(
                "authority_version"
            ),
            "inventory_generation_id": inventory_binding.get("generation_id"),
            "inventory_authority_revision": inventory_binding.get(
                "authority_revision"
            ),
        }
        if dispatch_manifest
        else {}
    )
    return {
        "diagnostics_truncated": truncated,
        "inventory_observations": len(inventories),
        "unique_bookmakers_seen": len(books),
        "unique_markets_seen": len(markets),
        "bookmaker_sample": [
            _bounded_text(value, 60)
            for value in sorted(books)[:BOOKMAKER_SAMPLE_LIMIT]
        ],
        "market_sample": [
            _bounded_text(value, 60)
            for value in sorted(markets)[:MARKET_SAMPLE_LIMIT]
        ],
        "latest_event_cycles": [
            {
                **cycle,
                "event_key": _bounded_text(cycle.get("event_key"), 100),
                "plan_observed_at": _bounded_text(
                    cycle.get("plan_observed_at"), 40
                ),
            }
            for cycle in cycles[:EVENT_CYCLE_SAMPLE_LIMIT]
        ],
        "expected_pairs": len(expected),
        "fetched_pairs": len(returned),
        "missing_pairs": len(missing),
        "required_missing_pairs": len(required_missing),
        "provider_unavailable_pairs": len(provider_unavailable),
        "normalization_rejected_pairs": len(normalization_rejected),
        "attempted_incomplete_pairs": len(attempted_incomplete),
        "quota_deferred_pairs": len(quota_deferred),
        "failed_pairs": len(failed_pairs),
        "never_attempted_pairs": len(never_attempted),
        "unresolved_pairs": len(unresolved),
        "expected_request_batches": len(expected_batches),
        "succeeded_request_batches": len(succeeded_batches),
        "failed_request_batches": len(failed_batches),
        "deferred_request_batches": len(deferred_batches),
        "unresolved_request_batches": len(unresolved_batches),
        "discovery_status_counts": discovery_status_counts,
        "coverage_integrity_failures": coverage_integrity_failures,
        "coverage_error_cycles": coverage_error_cycles,
        "dispatch_manifest": compact_dispatch_manifest,
        "incomplete_latest_event_cycles": incomplete_latest_cycles,
        "incomplete_request_cycles": incomplete_request_cycles,
        "quota_only_incomplete_request_cycles": quota_only_incomplete_request_cycles,
        "non_quota_incomplete_request_cycles": (
            incomplete_request_cycles - quota_only_incomplete_request_cycles
        ),
        "coverage_complete": bool(cycles)
        and manifest_authoritative
        and incomplete_latest_cycles == 0,
        "request_cycles_complete": bool(cycles)
        and manifest_authoritative
        and incomplete_request_cycles == 0,
        "missing_pair_sample": [
            _bounded_text(value, 160)
            for value in missing[:MISSING_PAIR_SAMPLE_LIMIT]
        ],
        "unresolved_pair_sample": [
            _bounded_text(value, 160)
            for value in unresolved[:MISSING_PAIR_SAMPLE_LIMIT]
        ],
        "attempted_incomplete_pair_sample": [
            _bounded_text(value, 160)
            for value in attempted_incomplete[:MISSING_PAIR_SAMPLE_LIMIT]
        ],
        "quota_deferred_pair_sample": [
            _bounded_text(value, 160)
            for value in quota_deferred[:MISSING_PAIR_SAMPLE_LIMIT]
        ],
        "failed_pair_sample": [
            _bounded_text(value, 160)
            for value in failed_pairs[:MISSING_PAIR_SAMPLE_LIMIT]
        ],
        "never_attempted_pair_sample": [
            _bounded_text(value, 160)
            for value in never_attempted[:MISSING_PAIR_SAMPLE_LIMIT]
        ],
        "failed_batch_sample": [
            _bounded_text(value, 160)
            for value in failed_batches[:MISSING_PAIR_SAMPLE_LIMIT]
        ],
        "deferred_batch_sample": [
            _bounded_text(value, 160)
            for value in deferred_batches[:MISSING_PAIR_SAMPLE_LIMIT]
        ],
        "unresolved_batch_sample": [
            _bounded_text(value, 160)
            for value in unresolved_batches[:MISSING_PAIR_SAMPLE_LIMIT]
        ],
        "collection_failures": len(failures),
        "permanent_collection_failures": sum(bool(row.get("permanent")) for row in failures),
        "failure_sample": [
            {
                "event_key": _bounded_text(row.get("event_key"), 100),
                "operation": _bounded_text(row.get("operation"), 50),
                "permanent": bool(row.get("permanent")),
                "observed_at": _bounded_text(row.get("observed_at"), 40),
                "detail": _bounded_text(row.get("detail"), 200),
            }
            for row in failures[:FAILURE_SAMPLE_LIMIT]
        ],
        "shared_quota_guard_blocks": len(quota_blocks),
    }


def _bounded_nonnegative_int(value: Any) -> int | None:
    try:
        return min(max(0, int(value)), 10**15)
    except (TypeError, ValueError, OverflowError):
        return None


def _liveness_summary(value: Any) -> dict[str, Any]:
    rows = value if isinstance(value, Mapping) else {}
    unhealthy: list[dict[str, str]] = []
    healthy = 0
    recovered = 0
    for component, detail in sorted(rows.items(), key=lambda item: str(item[0])):
        detail = detail if isinstance(detail, Mapping) else {}
        is_healthy = bool(detail.get("healthy"))
        reason = _bounded_text(detail.get("reason") or "UNKNOWN", 80)
        if is_healthy:
            healthy += 1
            recovered += int(reason == "RECOVERED_AFTER_ERROR")
        elif len(unhealthy) < LIVENESS_FAILURE_SAMPLE_LIMIT:
            unhealthy.append(
                {
                    "component": _bounded_text(component, 50),
                    "reason": reason,
                }
            )
    return {
        "components": len(rows),
        "healthy": healthy,
        "unhealthy": len(rows) - healthy,
        "recovered_after_error": recovered,
        "unhealthy_sample": unhealthy,
    }


def _compact_context_to_budget(context: dict[str, Any]) -> dict[str, Any]:
    """Drop only bounded samples until both exact request ceilings are met."""
    sample_paths = (
        ("coverage", "failure_sample"),
        ("coverage", "missing_pair_sample"),
        ("coverage", "unresolved_pair_sample"),
        ("coverage", "attempted_incomplete_pair_sample"),
        ("coverage", "quota_deferred_pair_sample"),
        ("coverage", "failed_pair_sample"),
        ("coverage", "never_attempted_pair_sample"),
        ("coverage", "failed_batch_sample"),
        ("coverage", "deferred_batch_sample"),
        ("coverage", "unresolved_batch_sample"),
        ("coverage", "latest_event_cycles"),
        ("coverage", "market_sample"),
        ("coverage", "bookmaker_sample"),
        ("recent_model_reports",),
        ("autonomy", "liveness", "unhealthy_sample"),
    )
    for path in sample_paths:
        parent: Any = context
        for key in path[:-1]:
            if not isinstance(parent, Mapping):
                parent = None
                break
            parent = parent.get(key)
        target = parent.get(path[-1]) if isinstance(parent, Mapping) else None

        def over_budget() -> bool:
            if (
                len(canonical_json(context).encode("utf-8"))
                > MAX_CONTEXT_CANONICAL_BYTES
            ):
                return True
            return _bedrock_request(context)[2] > MAX_BEDROCK_REQUEST_BYTES

        while (
            isinstance(target, list)
            and target
            and over_budget()
        ):
            target.pop()
        # Counts and completion booleans preserve the authoritative meaning of
        # an empty sample.  Omitting the empty list avoids spending the fixed
        # request budget on more than a dozen redundant diagnostic key names.
        if isinstance(parent, dict) and target == []:
            parent.pop(path[-1], None)
    if len(canonical_json(context).encode("utf-8")) > MAX_CONTEXT_CANONICAL_BYTES:
        raise RuntimeError("soccer analyst core context exceeds its hard budget")
    if _bedrock_request(context)[2] > MAX_BEDROCK_REQUEST_BYTES:
        raise RuntimeError("soccer analyst request exceeds its hard byte budget")
    return context


def _context(store: SoccerStore) -> dict[str, Any]:
    observed = now_utc()
    competitions = store.list_competitions()
    autonomy = store.ops.get_item(
        Key={"PK": "AUTONOMY", "SK": "STATE"}, ConsistentRead=True
    ).get("Item") or {}
    model_rows = sorted(
        [row for row in store.model_items() if str(row.get("SK") or "").startswith("VERSION#")],
        key=lambda row: row.get("created_at") or "",
        reverse=True,
    )[:MODEL_REPORT_SAMPLE_LIMIT]
    liveness = _liveness_summary(plain(autonomy.get("component_liveness") or {}))
    autonomy_counts = plain(autonomy.get("counts") or {})
    autonomy_queues = plain(autonomy.get("queues") or {})
    latest_quota = plain(autonomy.get("latest_quota") or {})
    context = {
        "system": "soccer_auto",
        "generated_at": iso_utc(observed),
        "feature_schema_version": _bounded_text(FEATURE_SCHEMA_VERSION, 80),
        "feature_summary": {
            "count": len(FEATURE_NAMES),
            # The analyst can only propose bounded optimizer settings; model
            # output cannot add or remove features.  Bind the exact schema by
            # digest instead of repeating every direct feature name in every
            # Bedrock request.  The former list consumed enough fixed context
            # bytes that a real 43-event dispatch manifest could exceed the
            # hard request budget even after every optional sample was removed.
            "direct_feature_count": sum(
                not str(name).startswith(
                    ("league_bucket_", "market_bucket_", "market_bucket_movement_")
                )
                for name in FEATURE_NAMES
            ),
            "feature_names_digest": digest(
                {
                    "feature_schema_version": FEATURE_SCHEMA_VERSION,
                    "feature_names": list(FEATURE_NAMES),
                }
            ),
            "bucket_counts": {
                prefix: sum(str(name).startswith(prefix) for name in FEATURE_NAMES)
                for prefix in (
                    "league_bucket_",
                    "market_bucket_",
                    "market_bucket_movement_",
                )
            },
        },
        "baseline_trials": list(BASELINE_TRIALS),
        "competition_counts": {
            "known": len(competitions),
            "active": sum(1 for row in competitions if row.get("active")),
            "outright": sum(1 for row in competitions if row.get("has_outrights")),
        },
        "autonomy": {
            "authority": _bounded_text(autonomy.get("authority"), 60),
            "reason": _bounded_text(autonomy.get("reason"), 120),
            "promotion_blocked": bool(autonomy.get("promotion_blocked")),
            "counts": {
                key: value
                for key in (
                    "competitions",
                    "events",
                    "snapshot_slots",
                    "locks",
                    "settlements",
                    "predictions",
                    "models",
                )
                if (value := _bounded_nonnegative_int(autonomy_counts.get(key)))
                is not None
            },
            "queues": {
                key: value
                for key in ("collection", "dead_letter")
                if (value := _bounded_nonnegative_int(autonomy_queues.get(key)))
                is not None
            },
            "latest_quota": {
                key: value
                for key, value in {
                    "operation": _bounded_text(latest_quota.get("operation"), 50),
                    "remaining": _bounded_nonnegative_int(latest_quota.get("remaining")),
                    "used": _bounded_nonnegative_int(latest_quota.get("used")),
                    "last_cost": _bounded_nonnegative_int(latest_quota.get("last_cost")),
                    "observed_at": _bounded_text(latest_quota.get("observed_at"), 40),
                }.items()
                if value not in (None, "")
            },
            "liveness": liveness,
            "component_liveness_complete": bool(
                autonomy.get("component_liveness_complete")
            ),
            "updated_at": _bounded_text(autonomy.get("updated_at"), 40),
        },
        "coverage": _coverage_diagnostics(store),
        "recent_model_reports": [
            {
                "model_digest": _bounded_text(row.get("model_digest"), 80),
                "authority_state": _bounded_text(row.get("authority_state"), 60),
                "created_at": _bounded_text(row.get("created_at"), 40),
                "feature_schema_version": _bounded_text(
                    row.get("feature_schema_version"), 80
                ),
            }
            for row in model_rows
        ],
    }
    return _compact_context_to_budget(context)


def _analysis_is_fresh(row: Mapping[str, Any], observed_at: Any | None = None) -> bool:
    observed = observed_at or now_utc()
    expires_at = row.get("expires_at")
    if expires_at is not None:
        try:
            return observed.timestamp() < int(expires_at)
        except (TypeError, ValueError):
            return False
    created_at = row.get("created_at")
    if not created_at:
        return False
    try:
        return observed < parse_utc(str(created_at)) + timedelta(hours=max(1, ANALYSIS_MAX_AGE_HOURS))
    except (TypeError, ValueError):
        return False


def latest_validated_analysis(
    store: SoccerStore,
    observed_at: Any | None = None,
) -> dict[str, Any] | None:
    row = store.ops.get_item(Key={"PK": "LLM_ANALYSIS", "SK": "LATEST"}, ConsistentRead=True).get("Item")
    if not row:
        return None
    row = plain(row)
    if (
        row.get("validation_status") != "VALIDATED"
        or row.get("analysis_version") != ANALYSIS_VERSION
        or row.get("analysis_origin") != ANALYSIS_ORIGIN
        or row.get("model_id") not in ALLOWED_MODEL_IDS
    ):
        return None
    if not _analysis_is_fresh(row, observed_at):
        return None
    validated = validate_analysis(row)
    content = {
        key: validated[key]
        for key in (
            "analysis_version",
            "summary",
            "coverage_findings",
            "warnings",
            "recommended_trials",
            "validation_status",
        )
    }
    expected_digest = digest(
        {
            **content,
            "analysis_origin": row.get("analysis_origin"),
            "model_id": row.get("model_id"),
            "context_digest": row.get("context_digest"),
            "created_at": row.get("created_at"),
            "expires_at": row.get("expires_at"),
            "stop_reason": row.get("stop_reason"),
            "usage": row.get("usage") or {},
        }
    )
    if expected_digest != row.get("analysis_digest"):
        return None
    return {**row, **content, "analysis_digest": expected_digest}


def latest_llm_trials(store: SoccerStore) -> tuple[list[dict[str, Any]], str | None]:
    row = latest_validated_analysis(store)
    if row is None:
        return [], None
    return [
        {key: trial[key] for key in ("learning_rate", "l2", "epochs")}
        for trial in row["recommended_trials"]
    ], str(row.get("analysis_digest") or "") or None


def _put_newer_llm_pointer(
    store: SoccerStore,
    item: Mapping[str, Any],
    *,
    attempt_started_at: str,
) -> bool:
    """Publish a mutable pointer only when its attempt started later.

    Bedrock invocations may overlap the hourly schedule or a deployment smoke.
    Ordering by invocation start prevents an older slow completion from
    repainting either LATEST or LAST_ATTEMPT after a newer run has published.
    """
    attempt_started_order = int(
        parse_utc(attempt_started_at).timestamp() * 1_000_000
    )
    payload = {
        **dict(item),
        "attempt_started_at": attempt_started_at,
        "attempt_started_order": attempt_started_order,
    }
    try:
        store.ops.put_item(
            Item=ddb_safe(payload),
            ConditionExpression=(
                "attribute_not_exists(attempt_started_order) OR "
                "attempt_started_order < :attempt_started_order"
            ),
            ExpressionAttributeValues=ddb_safe(
                {":attempt_started_order": attempt_started_order}
            ),
        )
        return True
    except ClientError as exc:
        if (exc.response.get("Error") or {}).get("Code") != (
            "ConditionalCheckFailedException"
        ):
            raise
        return False


def _record_llm_attempt(
    store: SoccerStore,
    *,
    observed: Any,
    status: str,
    reason: str | None = None,
    retry_after: str | None = None,
    analysis_digest: str | None = None,
    model_id: str | None = None,
    attempted_model_ids: Sequence[str] | None = None,
    model_errors: Sequence[Mapping[str, Any]] | None = None,
    context_byte_count: int | None = None,
    request_byte_count: int | None = None,
    attempt_id: str,
    attempt_started_at: str,
) -> bool:
    item = {
        "PK": "LLM_ANALYSIS",
        "SK": "LAST_ATTEMPT",
        "entity_type": "SOCCER_LLM_ATTEMPT",
        "status": status,
        "model_id": model_id or MODEL_ID,
        "attempted_model_ids": list(attempted_model_ids or (model_id or MODEL_ID,)),
        "observed_at": iso_utc(observed),
        "attempt_id": attempt_id,
        "attempt_started_at": attempt_started_at,
        "expires_at": int((observed + timedelta(days=ATTEMPT_RETENTION_DAYS)).timestamp()),
    }
    if reason:
        item["reason"] = reason
    if retry_after:
        item["retry_after"] = retry_after
    if analysis_digest:
        item["analysis_digest"] = analysis_digest
    if model_errors:
        item["model_errors"] = [dict(error) for error in model_errors]
    if context_byte_count is not None:
        item["context_byte_count"] = int(context_byte_count)
    if request_byte_count is not None:
        item["request_byte_count"] = int(request_byte_count)
    item["max_output_tokens"] = BEDROCK_MAX_OUTPUT_TOKENS
    return _put_newer_llm_pointer(
        store,
        item,
        attempt_started_at=attempt_started_at,
    )


def _model_ids() -> tuple[str, ...]:
    """Return a stable, de-duplicated real-Bedrock fallback chain."""
    result: list[str] = []
    for model_id in (MODEL_ID, *FALLBACK_MODEL_IDS):
        if model_id and model_id not in result:
            result.append(model_id)
    unknown = [model_id for model_id in result if model_id not in ALLOWED_MODEL_IDS]
    if unknown:
        raise ValueError(f"unsupported soccer Bedrock analyst model IDs: {unknown}")
    return tuple(result)


def _fallback_eligible_model_error(exc: ClientError) -> bool:
    """Whether failure is scoped to this static allowlisted model candidate."""
    error = exc.response.get("Error") or {}
    return error.get("Code") in {
        "AccessDeniedException",
        "ValidationException",
        "ResourceNotFoundException",
        "ThrottlingException",
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelErrorException",
        "ModelTimeoutException",
        "ModelNotReadyException",
    }


def _bedrock_error_diagnostic(model_id: str, exc: ClientError) -> dict[str, Any]:
    """Return a bounded diagnostic without leaking IAM or account details."""
    error = exc.response.get("Error") or {}
    metadata = exc.response.get("ResponseMetadata") or {}
    code = _bounded_text(error.get("Code") or "UnknownClientError", 100)
    raw_message = str(error.get("Message") or "")
    message = re.sub(r"arn:[^\s,;]+", "[REDACTED_ARN]", raw_message)
    message = re.sub(r"(?<!\d)\d{12}(?!\d)", "[REDACTED_ACCOUNT]", message)
    message = " ".join(message.split())[:MAX_BEDROCK_ERROR_MESSAGE_LENGTH]
    lowered = raw_message.lower()
    if code == "ThrottlingException" and "tokens per day" in lowered:
        category = "DAILY_TOKEN_QUOTA"
    elif code == "ThrottlingException":
        category = "ACCOUNT_QUOTA"
    elif code in {
        "ServiceUnavailableException",
        "InternalServerException",
        "ModelErrorException",
        "ModelTimeoutException",
        "ModelNotReadyException",
    }:
        category = "TRANSIENT_SERVICE"
    elif code in {
        "AccessDeniedException",
        "ValidationException",
        "ResourceNotFoundException",
    }:
        category = "CONFIGURATION_UNAVAILABLE"
    else:
        category = "NONRECOVERABLE_CLIENT_ERROR"
    result: dict[str, Any] = {
        "model_id": model_id,
        "error_code": code,
        "category": category,
    }
    if message:
        result["message"] = message
    try:
        http_status = int(metadata.get("HTTPStatusCode"))
    except (TypeError, ValueError):
        http_status = 0
    if 100 <= http_status <= 599:
        result["http_status"] = http_status
    request_id = re.sub(
        r"[^A-Za-z0-9_-]", "", str(metadata.get("RequestId") or "")
    )[:128]
    if request_id:
        result["request_id"] = request_id
    return result


def _bedrock_transport_diagnostic(
    model_id: str, exc: BotoCoreError
) -> dict[str, Any]:
    """Classify a client transport failure without persisting its raw payload."""
    error_code = re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:100]
    return {
        "model_id": model_id,
        "error_code": error_code or "BotoCoreError",
        "category": "TRANSIENT_CLIENT",
        # Botocore exception text can contain endpoint URLs or credential
        # provider details. The bounded class is enough to diagnose transport
        # behavior without persisting any raw exception fields.
        "message": "Bedrock Runtime client transport failure",
    }


def llm_analyst_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    attempt_started = now_utc()
    attempt_started_at = iso_utc(attempt_started)
    attempt_started_order = int(attempt_started.timestamp() * 1_000_000)
    attempt_id = f"{attempt_started_at}#{uuid.uuid4().hex}"
    model_ids = _model_ids()
    if not model_ids:
        return {
            "ok": False,
            "system": "soccer_auto",
            "component": "llm_analyst",
            "reason": "SOCCER_AUTO_LLM_MODEL_ID_NOT_CONFIGURED",
        }
    store = SoccerStore()
    latest = latest_validated_analysis(store)
    force_refresh = bool((event or {}).get("force_refresh"))
    if latest is not None and not force_refresh:
        return {
            "ok": True,
            "system": "soccer_auto",
            "component": "llm_analyst",
            "status": "FRESH_ANALYSIS_REUSED",
            "analysis_digest": latest["analysis_digest"],
            "validated_trials": len(latest["recommended_trials"]),
            "model_id": latest.get("model_id") or MODEL_ID,
            "attempted_model_ids": [],
            "expires_at": latest.get("expires_at"),
            "analysis_origin": latest.get("analysis_origin"),
            "context_digest": latest.get("context_digest"),
            "context_byte_count": latest.get("context_byte_count"),
            "request_byte_count": latest.get("request_byte_count"),
            "max_output_tokens": latest.get("max_output_tokens")
            or BEDROCK_MAX_OUTPUT_TOKENS,
        }
    analysis_context = _context(store)
    context_byte_count = len(canonical_json(analysis_context).encode("utf-8"))
    coverage = analysis_context.get("coverage") or {}
    dispatch_manifest = coverage.get("dispatch_manifest") or {}
    if not bool(dispatch_manifest.get("authoritative")):
        # A syntactically valid Bedrock response is not authoritative when its
        # input event universe is stale, incomplete, or from a mixed inventory
        # generation.  Defer before constructing the prompt or contacting any
        # model, and do not publish an attempt pointer that could be mistaken
        # for Bedrock availability telemetry.
        observed = now_utc()
        retry_after = iso_utc(observed + timedelta(minutes=5))
        return {
            "ok": False,
            "system": "soccer_auto",
            "component": "llm_analyst",
            "status": "DEFERRED_CONTEXT_AUTHORITY",
            "reason": "COVERAGE_DISPATCH_MANIFEST_NOT_AUTHORITATIVE",
            "analysis_available": False,
            "validated_trials": 0,
            "model_id": model_ids[0],
            "attempted_model_ids": [],
            "model_errors": [],
            "retry_after": retry_after,
            "attempt_id": attempt_id,
            "attempt_started_at": attempt_started_at,
            "observed_at": iso_utc(observed),
            "context_digest": digest(analysis_context),
            "context_byte_count": context_byte_count,
            "inventory_authority_state": str(
                dispatch_manifest.get("inventory_authority_state") or "MISSING"
            ),
        }
    system, messages, request_byte_count = _bedrock_request(analysis_context)
    if request_byte_count > MAX_BEDROCK_REQUEST_BYTES:
        raise RuntimeError("soccer analyst request exceeds its hard byte budget")
    bedrock = boto3.client("bedrock-runtime", config=BEDROCK_CLIENT_CONFIG)
    attempted_model_ids: list[str] = []
    model_errors: list[dict[str, Any]] = []
    validated: dict[str, Any] | None = None
    selected_model_id: str | None = None
    selected_stop_reason: str | None = None
    selected_usage: dict[str, Any] = {}
    for model_id in model_ids:
        attempted_model_ids.append(model_id)
        try:
            response = bedrock.converse(
                modelId=model_id,
                system=system,
                messages=messages,
                inferenceConfig={
                    "maxTokens": BEDROCK_MAX_OUTPUT_TOKENS,
                    "temperature": 0.1,
                    "topP": 0.9,
                },
            )
            stop_reason = str(response.get("stopReason") or "")
            if stop_reason != "end_turn":
                raise ValueError(f"Bedrock analyst did not complete cleanly: {stop_reason}")
            validated = validate_analysis(_extract_json_text(response))
            selected_model_id = model_id
            selected_stop_reason = stop_reason
            selected_usage = {
                str(key): int(value)
                for key, value in (response.get("usage") or {}).items()
                if isinstance(value, (int, float))
            }
            break
        except ClientError as exc:
            diagnostic = _bedrock_error_diagnostic(model_id, exc)
            # CloudWatch receives the same bounded diagnostic persisted in the
            # attempt row. Never log the prompt, response body, or raw ARN.
            print(canonical_json({"event": "soccer_bedrock_model_error", **diagnostic}))
            if not _fallback_eligible_model_error(exc):
                raise
            model_errors.append(diagnostic)
        except BotoCoreError as exc:
            # One unavailable endpoint/connection must not consume the whole
            # Lambda window or prevent the next allowlisted model candidate.
            diagnostic = _bedrock_transport_diagnostic(model_id, exc)
            print(
                canonical_json(
                    {"event": "soccer_bedrock_transport_error", **diagnostic}
                )
            )
            model_errors.append(diagnostic)
        except ValueError as exc:
            # A malformed response has no authority. Try another real Bedrock
            # model, but persist only the bounded validation error (never the
            # untrusted response body) if every model remains unavailable.
            invalid_diagnostic = {
                "model_id": model_id,
                "error_code": "INVALID_RESPONSE",
                "category": "INVALID_RESPONSE",
                "message": _bounded_text(exc, MAX_BEDROCK_ERROR_MESSAGE_LENGTH),
            }
            print(
                canonical_json(
                    {"event": "soccer_bedrock_invalid_response", **invalid_diagnostic}
                )
            )
            model_errors.append(invalid_diagnostic)
    if validated is None or selected_model_id is None:
        observed = now_utc()
        quota_only = bool(model_errors) and all(
            error.get("error_code") == "ThrottlingException" for error in model_errors
        )
        daily_quota_only = bool(model_errors) and all(
            error.get("category") == "DAILY_TOKEN_QUOTA" for error in model_errors
        )
        configuration_only = bool(model_errors) and all(
            error.get("category") == "CONFIGURATION_UNAVAILABLE"
            for error in model_errors
        )
        invalid_response_only = bool(model_errors) and all(
            error.get("category") == "INVALID_RESPONSE" for error in model_errors
        )
        retry_after = iso_utc(
            observed
            + (
                timedelta(hours=DAILY_TOKEN_RETRY_HOURS)
                if daily_quota_only
                else timedelta(minutes=TRANSIENT_RETRY_MINUTES)
            )
        )
        attempt_status = (
            "DEFERRED_QUOTA"
            if quota_only
            else "BLOCKED_CONFIGURATION"
            if configuration_only
            else "BLOCKED_INVALID_RESPONSE"
            if invalid_response_only
            else "DEFERRED_TRANSIENT"
        )
        attempt_reason = (
            "BEDROCK_ALL_FALLBACK_MODELS_CONFIGURATION_UNAVAILABLE"
            if configuration_only
            else "BEDROCK_ALL_FALLBACK_MODELS_INVALID_RESPONSE"
            if invalid_response_only
            else "BEDROCK_ALL_FALLBACK_MODELS_UNAVAILABLE"
        )
        _record_llm_attempt(
            store,
            observed=observed,
            status=attempt_status,
            reason=attempt_reason,
            retry_after=retry_after,
            model_id=model_ids[0],
            attempted_model_ids=attempted_model_ids,
            model_errors=model_errors,
            context_byte_count=context_byte_count,
            request_byte_count=request_byte_count,
            attempt_id=attempt_id,
            attempt_started_at=attempt_started_at,
        )
        # Exhausted Bedrock quota is an expected external capacity state, not
        # a Lambda runtime defect. Keep it fail-closed for analysis authority
        # (`ok` remains false and no LATEST analysis is written), but return a
        # structured deferral so AWS/Lambda Errors and the runtime-error alarm
        # remain reserved for code, configuration, invalid-output, and service
        # failures. The hourly schedule will retry the real Bedrock chain.
        if quota_only:
            return {
                "ok": False,
                "system": "soccer_auto",
                "component": "llm_analyst",
                "status": attempt_status,
                "reason": attempt_reason,
                "analysis_available": False,
                "validated_trials": 0,
                "model_id": model_ids[0],
                "attempted_model_ids": attempted_model_ids,
                "model_errors": model_errors,
                "retry_after": retry_after,
                "attempt_id": attempt_id,
                "attempt_started_at": attempt_started_at,
                "observed_at": iso_utc(observed),
            }
        raise RuntimeError(
            "all configured real Bedrock analyst models are temporarily unavailable: "
            + ",".join(
                f"{error['model_id']}={error['error_code']}/{error['category']}"
                for error in model_errors
            )
        )
    observed = now_utc()
    observed_at = iso_utc(observed)
    expires_at = int((observed + timedelta(hours=max(1, ANALYSIS_MAX_AGE_HOURS))).timestamp())
    content = {
        key: validated[key]
        for key in (
            "analysis_version",
            "summary",
            "coverage_findings",
            "warnings",
            "recommended_trials",
            "validation_status",
        )
    }
    context_digest = digest(analysis_context)
    analysis_digest = digest(
        {
            **content,
            "analysis_origin": ANALYSIS_ORIGIN,
            "model_id": selected_model_id,
            "context_digest": context_digest,
            "created_at": observed_at,
            "expires_at": expires_at,
            "stop_reason": selected_stop_reason,
            "usage": selected_usage,
        }
    )
    row = {
        "PK": "LLM_ANALYSIS",
        "SK": f"ANALYSIS#{observed_at}#{analysis_digest}",
        "entity_type": "SOCCER_LLM_ANALYSIS",
        "analysis_origin": ANALYSIS_ORIGIN,
        "model_id": selected_model_id,
        "attempted_model_ids": attempted_model_ids,
        "created_at": observed_at,
        "expires_at": expires_at,
        "stop_reason": selected_stop_reason,
        "usage": selected_usage,
        "context_digest": context_digest,
        "context_byte_count": context_byte_count,
        "request_byte_count": request_byte_count,
        "max_output_tokens": BEDROCK_MAX_OUTPUT_TOKENS,
        **content,
        "analysis_digest": analysis_digest,
        "attempt_id": attempt_id,
        "attempt_started_at": attempt_started_at,
        "attempt_started_order": attempt_started_order,
    }
    store.ops.put_item(Item=ddb_safe(row), ConditionExpression="attribute_not_exists(SK)")
    latest = {**row, "SK": "LATEST", "source_sk": row["SK"]}
    _put_newer_llm_pointer(
        store,
        latest,
        attempt_started_at=attempt_started_at,
    )
    _record_llm_attempt(
        store,
        observed=observed,
        status="ANALYZED",
        analysis_digest=analysis_digest,
        model_id=selected_model_id,
        attempted_model_ids=attempted_model_ids,
        model_errors=model_errors,
        context_byte_count=context_byte_count,
        request_byte_count=request_byte_count,
        attempt_id=attempt_id,
        attempt_started_at=attempt_started_at,
    )
    return {
        "ok": True,
        "system": "soccer_auto",
        "component": "llm_analyst",
        "status": "ANALYZED",
        "analysis_digest": analysis_digest,
        "attempt_id": attempt_id,
        "attempt_started_at": attempt_started_at,
        "observed_at": observed_at,
        "validated_trials": len(validated["recommended_trials"]),
        "model_id": selected_model_id,
        "attempted_model_ids": attempted_model_ids,
        "model_errors": model_errors,
        "expires_at": expires_at,
        "analysis_origin": ANALYSIS_ORIGIN,
        "context_digest": context_digest,
        "stop_reason": selected_stop_reason,
        "usage": selected_usage,
        "context_byte_count": context_byte_count,
        "request_byte_count": request_byte_count,
        "max_output_tokens": BEDROCK_MAX_OUTPUT_TOKENS,
    }
