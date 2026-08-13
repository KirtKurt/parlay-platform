"""Soccer-only LLM analyst for bounded autonomous experiment proposals.

The LLM never emits match probabilities, writes predictions, promotes models,
or changes infrastructure.  It analyzes isolated soccer coverage/model reports
and proposes a small hyperparameter search.  Deterministic validation clamps
every proposal before the chronological ML trainer may evaluate it.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any, Mapping, Sequence

import boto3

from .canonical import canonical_json, digest, iso_utc, parse_utc
from .market_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from .storage import SoccerStore, ddb_safe, now_utc, plain


ANALYSIS_VERSION = "soccer-auto-llm-analyst-v1"
MODEL_ID = os.getenv("SOCCER_AUTO_LLM_MODEL_ID", "").strip()
MAX_TRIALS = 2
ANALYSIS_MAX_AGE_HOURS = int(os.getenv("SOCCER_AUTO_LLM_ANALYSIS_MAX_AGE_HOURS", "36"))
DIAGNOSTIC_SCAN_PAGE_LIMIT = int(os.getenv("SOCCER_AUTO_LLM_DIAGNOSTIC_SCAN_PAGE_LIMIT", "20"))
DIAGNOSTIC_ROW_LIMIT = int(os.getenv("SOCCER_AUTO_LLM_DIAGNOSTIC_ROW_LIMIT", "2000"))
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
        "summary": "short string",
        "coverage_findings": ["string"],
        "warnings": ["string"],
        "recommended_trials": [
            {
                "learning_rate": "number from 0.005 through 0.1",
                "l2": "number from 0.00001 through 0.1",
                "epochs": "integer from 20 through 120",
                "rationale": "short string grounded only in the context",
            }
        ],
    }
    return (
        "Review this soccer-only context and propose at most two distinct "
        "residual-softmax training trials. Do not propose new data sources or "
        "features that are absent from the supplied feature schema. Required JSON schema:\n"
        f"{canonical_json(schema)}\nCONTEXT:\n{canonical_json(context)}"
    )


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
                "learning_rate": learning_rate,
                "l2": l2,
                "epochs": epochs,
                "rationale": _bounded_text(raw.get("rationale")),
            }
        )
    findings = payload.get("coverage_findings") if isinstance(payload.get("coverage_findings"), list) else []
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    result = {
        "analysis_version": ANALYSIS_VERSION,
        "summary": _bounded_text(payload.get("summary"), 1000),
        "coverage_findings": [_bounded_text(value) for value in findings[:20]],
        "warnings": [_bounded_text(value) for value in warnings[:20]],
        "recommended_trials": trials,
        "validation_status": "VALIDATED",
    }
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
    failures.sort(key=lambda row: str(row.get("observed_at") or ""), reverse=True)
    cycles.sort(key=lambda row: (row["plan_observed_at"], row["event_key"]), reverse=True)
    return {
        "diagnostics_truncated": truncated,
        "inventory_observations": len(inventories),
        "unique_bookmakers_seen": len(books),
        "unique_markets_seen": len(markets),
        "bookmaker_sample": sorted(books)[:100],
        "market_sample": sorted(markets)[:200],
        "latest_event_cycles": cycles[:100],
        "expected_pairs": len(expected),
        "fetched_pairs": len(returned),
        "missing_pairs": len(missing),
        "missing_pair_sample": missing[:100],
        "collection_failures": len(failures),
        "permanent_collection_failures": sum(bool(row.get("permanent")) for row in failures),
        "failure_sample": [
            {
                "event_key": row.get("event_key"),
                "operation": row.get("operation"),
                "permanent": bool(row.get("permanent")),
                "observed_at": row.get("observed_at"),
                "detail": _bounded_text(row.get("detail"), 300),
            }
            for row in failures[:50]
        ],
        "shared_quota_guard_blocks": len(quota_blocks),
    }


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
    )[:10]
    return {
        "system": "soccer_auto",
        "generated_at": iso_utc(observed),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "baseline_trials": list(BASELINE_TRIALS),
        "competition_counts": {
            "known": len(competitions),
            "active": sum(1 for row in competitions if row.get("active")),
            "outright": sum(1 for row in competitions if row.get("has_outrights")),
        },
        "autonomy": {
            key: plain(autonomy.get(key))
            for key in (
                "authority",
                "reason",
                "promotion_blocked",
                "counts",
                "queues",
                "latest_quota",
                "component_liveness",
                "component_liveness_complete",
                "updated_at",
            )
        },
        "coverage": _coverage_diagnostics(store),
        "recent_model_reports": [
            {
                "model_digest": row.get("model_digest"),
                "authority_state": row.get("authority_state"),
                "retrospective_report": row.get("retrospective_report"),
                "prospective_metrics": row.get("prospective_metrics"),
                "gate_failures": row.get("prospective_gate_failures") or row.get("retrospective_gate_failures"),
            }
            for row in model_rows
        ],
    }


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


def latest_llm_trials(store: SoccerStore) -> tuple[list[dict[str, Any]], str | None]:
    row = store.ops.get_item(Key={"PK": "LLM_ANALYSIS", "SK": "LATEST"}, ConsistentRead=True).get("Item")
    if not row:
        return [], None
    row = plain(row)
    if row.get("validation_status") != "VALIDATED" or row.get("analysis_version") != ANALYSIS_VERSION:
        return [], None
    if not _analysis_is_fresh(row):
        return [], None
    validated = validate_analysis(row)
    if validated["analysis_digest"] != row.get("analysis_digest"):
        return [], None
    return [
        {key: trial[key] for key in ("learning_rate", "l2", "epochs")}
        for trial in validated["recommended_trials"]
    ], str(row.get("analysis_digest") or "") or None


def llm_analyst_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    if not MODEL_ID:
        return {
            "ok": False,
            "system": "soccer_auto",
            "component": "llm_analyst",
            "reason": "SOCCER_AUTO_LLM_MODEL_ID_NOT_CONFIGURED",
        }
    store = SoccerStore()
    analysis_context = _context(store)
    response = boto3.client("bedrock-runtime").converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": _prompt(analysis_context)}]}],
        inferenceConfig={"maxTokens": 1800, "temperature": 0.1, "topP": 0.9},
    )
    validated = validate_analysis(_extract_json_text(response))
    observed = now_utc()
    observed_at = iso_utc(observed)
    expires_at = int((observed + timedelta(hours=max(1, ANALYSIS_MAX_AGE_HOURS))).timestamp())
    row = {
        "PK": "LLM_ANALYSIS",
        "SK": f"ANALYSIS#{observed_at}#{validated['analysis_digest']}",
        "entity_type": "SOCCER_LLM_ANALYSIS",
        "model_id": MODEL_ID,
        "created_at": observed_at,
        "expires_at": expires_at,
        "context_digest": digest(analysis_context),
        **validated,
    }
    store.ops.put_item(Item=ddb_safe(row), ConditionExpression="attribute_not_exists(SK)")
    latest = {**row, "SK": "LATEST", "source_sk": row["SK"]}
    store.ops.put_item(Item=ddb_safe(latest))
    return {
        "ok": True,
        "system": "soccer_auto",
        "component": "llm_analyst",
        "analysis_digest": validated["analysis_digest"],
        "validated_trials": len(validated["recommended_trials"]),
        "model_id": MODEL_ID,
        "expires_at": expires_at,
    }
