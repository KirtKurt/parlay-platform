"""Read-only operational API for the isolated soccer_auto service."""
from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any, Mapping

from boto3.dynamodb.conditions import Key

from .canonical import digest, iso_utc, parse_utc, schedule_identity
from .config import (
    PUBLICATION_COMMIT_HEADROOM_SECONDS,
    PUBLICATION_CUTOFF_MINUTES,
)
from .odds_api import provider_safety_config
from .storage import (
    COVERAGE_CERTIFICATE_VERSION,
    COVERAGE_DISPATCH_MANIFEST_VERSION,
    COVERAGE_EXTERNAL_QUOTA_REASONS,
    COVERAGE_PLAN_VERSION,
    EVENT_INVENTORY_AUTHORITY_MAX_AGE_SECONDS,
    EVENT_INVENTORY_AUTHORITY_VERSION,
    SoccerStore,
    coverage_expected_batch_digests,
    coverage_plan_digest,
    now_utc,
    plain,
)


PUBLIC_BINDING_VERSION = "soccer-auto-public-prediction-binding-v2"
COVERAGE_DISPATCH_MANIFEST_MAX_AGE_SECONDS = 180


def _response(status: int, body: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(plain(dict(body)), sort_keys=True, default=str),
    }


def status(store: SoccerStore) -> dict[str, Any]:
    state = store.ops.get_item(Key={"PK": "AUTONOMY", "SK": "STATE"}, ConsistentRead=True).get("Item")
    provider_429_telemetry = store.provider_429_status()
    if not state:
        return {
            "ok": True,
            "system": "soccer_auto",
            "authority": "BOOTSTRAPPING",
            "automatic_prediction_allowed": False,
            "promotion_blocked": True,
            "reason": "AUTONOMOUS_CONTROLLER_HAS_NOT_COMPLETED_FIRST_CYCLE",
            "shared_provider_safety": provider_safety_config(),
            "distributed_rate_limit_state": store.rate_limit_status(),
            "provider_429_telemetry": provider_429_telemetry,
            "historical_backfill": _historical_status(store),
        }
    return {
        "ok": True,
        **plain(state),
        "shared_provider_safety": provider_safety_config(),
        "distributed_rate_limit_state": store.rate_limit_status(),
        "provider_429_telemetry": provider_429_telemetry,
        "historical_backfill": _historical_status(store),
    }


def predictions(store: SoccerStore, limit: int = 100) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    query: dict[str, Any] = {
        "IndexName": "ByPredictionTime",
        "KeyConditionExpression": Key("GSI1PK").eq("SOCCER_PREDICTIONS"),
        "ScanIndexForward": False,
        "Limit": 500,
    }
    raw_cap = min(2000, max(500, limit * 4))
    for _ in range(4):
        response = store.predictions.query(**query)
        rows.extend(plain(row) for row in response.get("Items") or [])
        cursor = response.get("LastEvaluatedKey")
        if not cursor or len(rows) >= raw_cap:
            break
        query["ExclusiveStartKey"] = cursor
    rows = rows[:raw_cap]
    current_events: dict[str, Mapping[str, Any] | None] = {}
    public_bindings: dict[tuple[str, int, str, str], Mapping[str, Any] | None] = {}
    public_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    suppressed = 0
    for row in rows:
        if (
            row.get("prediction_status") not in {"PUBLISHED", "NO_PICK"}
            or row.get("model_authority") != "CHAMPION"
            or row.get("immutable") is not True
        ):
            suppressed += 1
            continue
        event_key = str(row.get("event_key") or "")
        if event_key not in current_events:
            current_events[event_key] = store.get_event(event_key)
        current = current_events[event_key] or {}
        try:
            revision = int(row.get("schedule_revision") or 0)
            horizon = str(row.get("horizon") or "")
            target = str(row.get("target") or "")
            row_identity = str(row.get("schedule_identity") or "")
            current_identity = str(current.get("schedule_identity") or "")
            same_schedule = bool(
                event_key
                and current
                and revision > 0
                and revision == int(current.get("schedule_revision") or 0)
                and row_identity
                and current_identity
                and row_identity == current_identity
                and row_identity == schedule_identity(row)
                and current_identity == schedule_identity(current)
                and str(row.get("commence_time") or "")
                == str(current.get("commence_time") or "")
                and horizon == "T45"
                and target == "result_1x2"
            )
        except (KeyError, TypeError, ValueError):
            same_schedule = False
        if not same_schedule:
            suppressed += 1
            continue
        binding_key = (event_key, revision, horizon, target)
        if binding_key not in public_bindings:
            binding = store.ops.get_item(
                Key={
                    "PK": f"PUBLIC_PREDICTION_BINDING#{event_key}",
                    "SK": f"REV#{revision}#HORIZON#{horizon}#TARGET#{target}",
                },
                ConsistentRead=True,
            ).get("Item")
            public_bindings[binding_key] = plain(binding) if binding else None
        binding = public_bindings[binding_key] or {}
        try:
            publication_cutoff = parse_utc(
                str(row.get("commence_time") or "")
            ) - timedelta(minutes=PUBLICATION_CUTOFF_MINUTES)
            commit_deadline = publication_cutoff - timedelta(
                seconds=PUBLICATION_COMMIT_HEADROOM_SECONDS
            )
            created_at = parse_utc(str(row.get("created_at") or ""))
            bound_at = parse_utc(str(binding.get("bound_at") or ""))
            autonomy_updated_at = parse_utc(
                str(row.get("autonomy_updated_at") or "")
            )
            timing_matches = bool(
                str(row.get("publication_cutoff") or "")
                == iso_utc(publication_cutoff)
                and str(binding.get("publication_cutoff") or "")
                == iso_utc(publication_cutoff)
                and str(row.get("commit_deadline") or "")
                == iso_utc(commit_deadline)
                and str(binding.get("commit_deadline") or "")
                == iso_utc(commit_deadline)
                and float(row.get("commit_headroom_seconds"))
                == PUBLICATION_COMMIT_HEADROOM_SECONDS
                and float(binding.get("commit_headroom_seconds"))
                == PUBLICATION_COMMIT_HEADROOM_SECONDS
                and created_at == bound_at
                and created_at <= commit_deadline
                and int(row.get("autonomy_updated_at_epoch_ms") or 0)
                == int(autonomy_updated_at.timestamp() * 1000)
            )
            binding_matches = bool(
                binding
                and timing_matches
                and binding.get("entity_type") == "SOCCER_PUBLIC_PREDICTION_BINDING"
                and binding.get("binding_version") == PUBLIC_BINDING_VERSION
                and binding.get("immutable") is True
                and str(binding.get("event_key") or "") == event_key
                and str(binding.get("event_id") or "") == str(row.get("event_id") or "")
                and str(binding.get("sport_key") or "") == str(row.get("sport_key") or "")
                and str(binding.get("commence_time") or "")
                == str(row.get("commence_time") or "")
                and int(binding.get("schedule_revision") or 0) == revision
                and str(binding.get("schedule_identity") or "") == row_identity
                and str(binding.get("horizon") or "") == horizon
                and str(binding.get("target") or "") == target
                and str(binding.get("lock_sk") or "")
                == f"LOCK#{horizon}#REV#{revision}#TARGET#{target}"
                and bool(str(row.get("feature_hash") or ""))
                and str(binding.get("feature_hash") or "")
                == str(row.get("feature_hash") or "")
                and str(binding.get("lock_version") or "")
                == "soccer-auto-t45-lock-v2"
                and str(row.get("lock_version") or "")
                == str(binding.get("lock_version") or "")
                and str(binding.get("coverage_certificate_version") or "")
                == COVERAGE_CERTIFICATE_VERSION
                and str(row.get("coverage_certificate_version") or "")
                == str(binding.get("coverage_certificate_version") or "")
                and bool(str(row.get("coverage_certificate_digest") or ""))
                and str(binding.get("coverage_certificate_digest") or "")
                == str(row.get("coverage_certificate_digest") or "")
                and bool(str(row.get("coverage_plan_digest") or ""))
                and str(binding.get("coverage_plan_digest") or "")
                == str(row.get("coverage_plan_digest") or "")
                and bool(str(row.get("autonomy_updated_at") or ""))
                and str(binding.get("autonomy_updated_at") or "")
                == str(row.get("autonomy_updated_at") or "")
                and int(row.get("autonomy_updated_at_epoch_ms") or 0) > 0
                and int(binding.get("autonomy_updated_at_epoch_ms") or 0)
                == int(row.get("autonomy_updated_at_epoch_ms") or 0)
                and int(row.get("event_metadata_revision") or 0) > 0
                and int(binding.get("event_metadata_revision") or 0)
                == int(row.get("event_metadata_revision") or 0)
                and bool(str(row.get("model_digest") or ""))
                and str(binding.get("model_digest") or "")
                == str(row.get("model_digest") or "")
            )
        except (TypeError, ValueError):
            binding_matches = False
        if not binding_matches:
            suppressed += 1
            continue
        identity = (event_key, horizon, target)
        existing = public_rows.get(identity)
        if existing is None or str(row.get("created_at") or "") < str(
            existing.get("created_at") or ""
        ):
            public_rows[identity] = row
        else:
            suppressed += 1
    visible = sorted(
        public_rows.values(),
        key=lambda row: (str(row.get("commence_time") or ""), str(row.get("event_key") or "")),
        reverse=True,
    )[:limit]
    return {
        "ok": True,
        "system": "soccer_auto",
        "count": len(visible),
        "predictions": visible,
        "audit_rows_suppressed": suppressed,
        "public_contract": "one immutable current-schedule T45 public decision per event",
    }


def _query_partition(table: Any, pk: str, *, limit: int = 1000) -> tuple[list[dict[str, Any]], bool]:
    response = table.query(
        KeyConditionExpression=Key("PK").eq(pk),
        ConsistentRead=True,
        Limit=max(1, int(limit)),
    )
    return [plain(row) for row in response.get("Items") or []], bool(response.get("LastEvaluatedKey"))


def _historical_status(store: SoccerStore) -> dict[str, Any]:
    from .historical_materializer import materialization_status

    cursors, truncated = _query_partition(store.ops, "HISTORICAL_CURSOR", limit=1000)
    detail = [row for row in cursors if not str(row.get("SK") or "").endswith("#SUMMARY")]
    completed = [row for row in detail if row.get("status") == "COMPLETE"]
    progressing = [
        row
        for row in detail
        if row.get("status") in {"RUNNING", "PENDING", "QUOTA_DEFERRED"}
    ]
    latest_progress = max(
        (str(row.get("last_progress_at") or row.get("updated_at") or "") for row in detail),
        default="",
    )
    materialization = materialization_status(store)
    return {
        "enabled": os.getenv("SOCCER_AUTO_HISTORICAL_BACKFILL_ENABLED", "true").lower()
        == "true",
        "mode": "RAW_ARCHIVE_PLUS_AUTHORITATIVE_T45",
        "state": "COMPLETE" if detail and len(completed) == len(detail) else "RUNNING" if progressing else "PENDING",
        "cursor_rows": len(detail),
        "completed_cursor_rows": len(completed),
        "calls_completed": sum(int(row.get("calls_completed") or 0) for row in detail),
        "latest_progress_at": latest_progress or None,
        "cursors_truncated": truncated,
        "historical_training_rows": int(
            materialization.get("historical_training_rows") or 0
        ),
        "training_note": (
            "Recent rows are admitted only after immutable Odds API final-score "
            "evidence is joined to a point-in-time T45 snapshot; older odds-only "
            "archives remain training-ineligible."
        ),
        "supervised_materialization": materialization,
    }


def _bounded_ops_diagnostics(
    store: SoccerStore,
    *,
    page_limit: int = 4,
    row_limit: int = 2000,
) -> tuple[list[dict[str, Any]], bool]:
    """Bound API work so the coverage endpoint cannot full-scan itself into a 504."""
    entity_types = {
        "SOCCER_MARKET_INVENTORY",
        "SOCCER_EVENT_COVERAGE_PLAN",
        "SOCCER_EVENT_COVERAGE_FETCH",
        "SOCCER_COLLECTION_FAILURE",
    }
    rows: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"Limit": 500}
    cursor = None
    for _ in range(max(1, page_limit)):
        response = store.ops.scan(**kwargs)
        for item in response.get("Items") or []:
            row = plain(item)
            if row.get("entity_type") in entity_types:
                rows.append(row)
                if len(rows) >= max(1, row_limit):
                    return rows, True
        cursor = response.get("LastEvaluatedKey")
        if not cursor:
            return rows, False
        kwargs["ExclusiveStartKey"] = cursor
    return rows, bool(cursor)


def _latest_cycle_coverage(
    coverage_plans: list[Mapping[str, Any]],
    coverage_fetches: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconcile only each event's latest discovery/fetch cycle."""
    latest_by_event: dict[str, Mapping[str, Any]] = {}
    for plan in coverage_plans:
        event_key = str(plan.get("event_key") or "")
        observed_at = str(plan.get("observed_at") or "")
        if event_key and (
            event_key not in latest_by_event
            or observed_at > str(latest_by_event[event_key].get("observed_at") or "")
        ):
            latest_by_event[event_key] = plan
    returned_by_cycle: dict[tuple[str, str], set[str]] = {}
    for fetch in coverage_fetches:
        cycle = (str(fetch.get("event_key") or ""), str(fetch.get("plan_observed_at") or ""))
        if all(cycle):
            returned_by_cycle.setdefault(cycle, set()).update(
                str(pair) for pair in fetch.get("returned_pairs") or []
            )
    expected_pairs: set[str] = set()
    returned_pairs: set[str] = set()
    cycles = []
    for event_key, plan in latest_by_event.items():
        plan_at = str(plan.get("observed_at") or "")
        expected = {str(pair) for pair in plan.get("expected_pairs") or []}
        returned = returned_by_cycle.get((event_key, plan_at), set())
        missing = expected - returned
        expected_pairs.update(f"{event_key}|{pair}" for pair in expected)
        returned_pairs.update(f"{event_key}|{pair}" for pair in returned & expected)
        cycles.append(
            {
                "event_key": event_key,
                "plan_observed_at": plan_at,
                "expected": len(expected),
                "fetched": len(returned & expected),
                "missing": len(missing),
                "complete": bool(expected) and not missing,
            }
        )
    cycles.sort(key=lambda row: (row["plan_observed_at"], row["event_key"]), reverse=True)
    return {
        "expected_pairs": expected_pairs,
        "returned_pairs": returned_pairs,
        "missing_pairs": expected_pairs - returned_pairs,
        "cycles": cycles,
    }


def _latest_summary_coverage(
    summaries: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconcile exact materialized cycles with deterministic outcome precedence."""
    aggregate: dict[str, set[str]] = {
        "expected_pairs": set(),
        "required_pairs": set(),
        "probe_pairs": set(),
        "returned_pairs": set(),
        "provider_unavailable_pairs": set(),
        "normalization_rejected_pairs": set(),
        "attempted_incomplete_pairs": set(),
        "quota_deferred_pairs": set(),
        "failed_pairs": set(),
        "unresolved_pairs": set(),
        "missing_pairs": set(),
        "required_missing_pairs": set(),
        "probe_missing_pairs": set(),
        "never_attempted_pairs": set(),
        "expected_batch_digests": set(),
        "succeeded_batch_digests": set(),
        "failed_batch_digests": set(),
        "deferred_batch_digests": set(),
        "unresolved_batch_digests": set(),
    }
    cycles: list[dict[str, Any]] = []
    discovery_status_counts: dict[str, int] = {}
    integrity_failures = 0
    coverage_error_cycles = 0
    for summary in summaries:
        event_key = str(summary.get("event_key") or summary.get("SK") or "")
        if not event_key:
            continue
        stored_required = {str(pair) for pair in summary.get("required_pairs") or []}
        stored_probe = {str(pair) for pair in summary.get("probe_pairs") or []}
        stored_expected = {str(pair) for pair in summary.get("expected_pairs") or []}
        expected = stored_expected or stored_required | stored_probe
        required = stored_required & expected
        probe = (stored_probe & expected) - required
        discovery_status = str(summary.get("discovery_status") or "UNKNOWN")
        discovery_budget_reason = str(summary.get("budget_reason") or "")
        discovery_status_counts[discovery_status] = (
            discovery_status_counts.get(discovery_status, 0) + 1
        )
        coverage_error_cycles += int(bool(summary.get("coverage_error")))
        expected_digest_valid = str(summary.get("expected_digest") or "") == digest(
            sorted(expected)
        )
        partition_valid = (
            not (stored_required & stored_probe)
            and (not stored_expected or stored_expected == stored_required | stored_probe)
        )
        plan_observed_at = str(summary.get("plan_observed_at") or "")
        stored_plan_digest = str(summary.get("plan_digest") or "")
        plan_present = bool(plan_observed_at and stored_plan_digest)
        request_markets = sorted(
            {str(value) for value in summary.get("request_markets") or [] if value}
        )
        plan_binding_valid = bool(
            (not plan_observed_at and not stored_plan_digest)
            or (
                plan_present
                and str(summary.get("plan_version") or "")
                == COVERAGE_PLAN_VERSION
                and stored_plan_digest
                == coverage_plan_digest(
                    event_key=event_key,
                    observed_at=plan_observed_at,
                    schedule_revision=int(
                        summary.get("schedule_revision") or 0
                    ),
                    schedule_identity_value=str(
                        summary.get("schedule_identity") or ""
                    ),
                    request_markets=request_markets,
                    required_pairs=sorted(required),
                    probe_pairs=sorted(probe),
                )
            )
        )
        integrity_valid = (
            expected_digest_valid and partition_valid and plan_binding_valid
        )
        raw_expected_batch_values = list(
            summary.get("fanout_expected_batch_digests") or []
        )
        raw_enqueued_batch_values = list(
            summary.get("fanout_enqueued_batch_digests") or []
        )
        raw_succeeded_batch_values = list(
            summary.get("fanout_succeeded_batch_digests") or []
        )
        raw_failed_batch_values = list(
            summary.get("fanout_failed_batch_digests") or []
        )
        raw_deferred_batch_values = list(
            summary.get("fanout_deferred_batch_digests") or []
        )
        expected_batches = {
            str(value) for value in raw_expected_batch_values if value
        }
        exact_expected_batches = set(
            coverage_expected_batch_digests(
                plan_digest=stored_plan_digest,
                request_markets=request_markets,
                expected_pairs=sorted(expected),
            )
        ) if plan_present else set()
        enqueued_batches = {
            str(value) for value in raw_enqueued_batch_values if value
        }
        succeeded_batches = {
            str(value) for value in raw_succeeded_batch_values if value
        }
        failed_batches = {
            str(value) for value in raw_failed_batch_values if value
        }
        deferred_batches = {
            str(value) for value in raw_deferred_batch_values if value
        }
        raw_deferred_batch_reasons = summary.get(
            "fanout_deferred_batch_reasons"
        ) or {}
        deferred_reason_container_valid = isinstance(
            raw_deferred_batch_reasons, Mapping
        )
        deferred_batch_reasons = (
            {
                str(key): [str(reason) for reason in value if reason]
                for key, value in raw_deferred_batch_reasons.items()
                if key and isinstance(value, (list, tuple, set))
            }
            if deferred_reason_container_valid
            else {}
        )
        deferred_reason_integrity_valid = bool(
            deferred_reason_container_valid
            and set(deferred_batch_reasons) == deferred_batches
            and all(
                reasons
                and len(reasons) == len(set(reasons))
                and set(reasons) <= COVERAGE_EXTERNAL_QUOTA_REASONS
                for reasons in deferred_batch_reasons.values()
            )
        )
        discovery_quota_reason_valid = bool(
            discovery_status != "QUOTA_DEFERRED"
            or discovery_budget_reason in COVERAGE_EXTERNAL_QUOTA_REASONS
        )
        batch_partition_valid = bool(
            expected_batches == exact_expected_batches
            and len(raw_expected_batch_values) == len(expected_batches)
            and len(raw_enqueued_batch_values) == len(enqueued_batches)
            and len(raw_succeeded_batch_values) == len(succeeded_batches)
            and len(raw_failed_batch_values) == len(failed_batches)
            and len(raw_deferred_batch_values) == len(deferred_batches)
            and
            enqueued_batches <= expected_batches
            and succeeded_batches <= enqueued_batches
            and failed_batches <= enqueued_batches
            and deferred_batches <= enqueued_batches
            and not (succeeded_batches & failed_batches)
            and not (succeeded_batches & deferred_batches)
            and not (failed_batches & deferred_batches)
            and deferred_reason_integrity_valid
            and discovery_quota_reason_valid
            and int(summary.get("split_batch_conflicts") or 0) == 0
            and int(summary.get("region_split_conflicts") or 0) == 0
        )
        integrity_valid = integrity_valid and batch_partition_valid
        if not integrity_valid:
            integrity_failures += 1
        discovery_complete = bool(
            discovery_status == "HTTP_200"
            and plan_present
            and expected_batches
            and enqueued_batches == expected_batches
        )
        unresolved_batches = expected_batches - succeeded_batches - failed_batches
        reported_expected_batches = set(expected_batches)
        if not batch_partition_valid:
            succeeded_batches = set()
            failed_batches = set()
            deferred_batches = set()
            # The immutable plan still proves the request universe even when
            # persisted fanout evidence is absent or malformed.  Report that
            # full universe as unresolved so global batch algebra cannot
            # undercount multiple invalid PLAN_READY cycles.
            reported_expected_batches = set(
                exact_expected_batches or expected_batches
            )
            unresolved_batches = set(reported_expected_batches)

        returned = {str(pair) for pair in summary.get("returned_pairs") or []} & expected
        rejected = (
            {str(pair) for pair in summary.get("normalization_rejected_pairs") or []}
            & expected
        ) - returned
        unavailable = (
            {str(pair) for pair in summary.get("provider_unavailable_pairs") or []}
            & expected
        ) - returned - rejected
        terminal = returned | rejected | unavailable
        unresolved = expected - terminal
        attempted_incomplete = (
            {str(pair) for pair in summary.get("attempted_incomplete_pairs") or []}
            & unresolved
        )
        if not integrity_valid:
            returned = set()
            rejected = set()
            unavailable = set()
            terminal = set()
            unresolved = set(expected)
            attempted_incomplete = set()
        deferred = (
            {str(pair) for pair in summary.get("quota_deferred_pairs") or []}
            & unresolved
        ) - attempted_incomplete
        failed = (
            {str(pair) for pair in summary.get("failed_pairs") or []}
            & unresolved
        ) - attempted_incomplete - deferred
        attempted = (
            returned | rejected | unavailable | attempted_incomplete | deferred | failed
        )
        never_attempted = unresolved - attempted
        missing = expected - returned
        required_missing = required - returned
        probe_missing = probe - returned

        values = {
            "expected_pairs": expected,
            "required_pairs": required,
            "probe_pairs": probe,
            "returned_pairs": returned,
            "provider_unavailable_pairs": unavailable,
            "normalization_rejected_pairs": rejected,
            "attempted_incomplete_pairs": attempted_incomplete,
            "quota_deferred_pairs": deferred,
            "failed_pairs": failed,
            "unresolved_pairs": unresolved,
            "missing_pairs": missing,
            "required_missing_pairs": required_missing,
            "probe_missing_pairs": probe_missing,
            "never_attempted_pairs": never_attempted,
            "expected_batch_digests": reported_expected_batches,
            "succeeded_batch_digests": succeeded_batches,
            "failed_batch_digests": failed_batches,
            "deferred_batch_digests": deferred_batches,
            "unresolved_batch_digests": unresolved_batches,
        }
        for name, pairs in values.items():
            aggregate[name].update(f"{event_key}|{pair}" for pair in pairs)
        request_complete = bool(
            discovery_complete
            and integrity_valid
            and succeeded_batches == expected_batches
            and not failed_batches
            and not unresolved_batches
            and bool(expected)
            and not unresolved
        )
        quota_only_incomplete = bool(
            not request_complete
            and integrity_valid
            and not summary.get("coverage_error")
            and discovery_status in {"HTTP_200", "QUOTA_DEFERRED"}
            and not attempted_incomplete
            and not failed
            and not never_attempted
            and unresolved == deferred
            and not failed_batches
            and unresolved_batches == deferred_batches
            and (
                discovery_status == "QUOTA_DEFERRED"
                or bool(deferred)
                or bool(deferred_batches)
            )
        )
        cycles.append(
            {
                "event_key": event_key,
                "plan_observed_at": str(summary.get("plan_observed_at") or ""),
                "plan_digest": str(summary.get("plan_digest") or ""),
                "discovery_observed_at": str(
                    summary.get("discovery_observed_at") or ""
                ),
                "discovery_status": discovery_status,
                "budget_reason": discovery_budget_reason,
                "deferred_batch_reasons": deferred_batch_reasons,
                "discovery_complete": discovery_complete,
                "integrity_valid": integrity_valid,
                "coverage_error": str(summary.get("coverage_error") or ""),
                "coverage_item_size_bytes": int(
                    summary.get("coverage_item_size_bytes") or 0
                ),
                "split_batch_conflicts": int(
                    summary.get("split_batch_conflicts") or 0
                ),
                "expected": len(expected),
                "required_current": len(required),
                "rolling_probes": len(probe),
                "fetched": len(returned),
                "provider_unavailable": len(unavailable),
                "normalization_rejected": len(rejected),
                "attempted_incomplete": len(attempted_incomplete),
                "quota_deferred": len(deferred),
                "failed": len(failed),
                "never_attempted": len(never_attempted),
                "unresolved": len(unresolved),
                "missing": len(missing),
                "required_missing": len(required_missing),
                "probe_missing": len(probe_missing),
                "expected_batches": len(reported_expected_batches),
                "enqueued_batches": len(enqueued_batches),
                "succeeded_batches": len(succeeded_batches),
                "failed_batches": len(failed_batches),
                "deferred_batches": len(deferred_batches),
                "unresolved_batches": len(unresolved_batches),
                "request_complete": request_complete,
                "quota_only_incomplete": quota_only_incomplete,
                "required_availability_complete": (
                    discovery_complete
                    and integrity_valid
                    and succeeded_batches == expected_batches
                    and not failed_batches
                    and not unresolved_batches
                    and bool(required)
                    and not required_missing
                ),
                "all_planned_pairs_returned": (
                    discovery_complete
                    and integrity_valid
                    and succeeded_batches == expected_batches
                    and not failed_batches
                    and not unresolved_batches
                    and bool(expected)
                    and not missing
                ),
                "complete": (
                    discovery_complete
                    and integrity_valid
                    and succeeded_batches == expected_batches
                    and not failed_batches
                    and not unresolved_batches
                    and bool(required)
                    and not required_missing
                    and not unresolved
                ),
                "outcome_counts": dict(summary.get("outcome_counts") or {}),
                "updated_at": str(summary.get("updated_at") or ""),
            }
        )
    cycles.sort(key=lambda row: (row["plan_observed_at"], row["event_key"]), reverse=True)
    return {
        **aggregate,
        "cycles": cycles,
        "discovery_status_counts": discovery_status_counts,
        "integrity_failures": integrity_failures,
        "coverage_error_cycles": coverage_error_cycles,
    }


def _coverage_summary_universe(
    store: SoccerStore,
    *,
    observed_at: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join exact latest summaries to the dispatcher's fresh event manifest."""
    manifest = store.latest_coverage_dispatch_manifest()
    version = COVERAGE_DISPATCH_MANIFEST_VERSION
    manifest_at = str(manifest.get("observed_at") or "")
    raw_entries = manifest.get("events") or []
    raw_inventory_binding = manifest.get("inventory_authority") or {}
    inventory_binding = (
        {
            "authority_version": str(
                raw_inventory_binding.get("authority_version") or ""
            ),
            "generation_id": str(raw_inventory_binding.get("generation_id") or ""),
            "completed_at": str(raw_inventory_binding.get("completed_at") or ""),
            "authority_revision": int(
                raw_inventory_binding.get("authority_revision") or 0
            ),
        }
        if isinstance(raw_inventory_binding, Mapping)
        else {}
    )
    current_inventory = store.event_inventory_authority()
    inventory_current = False
    try:
        inventory_age = (
            observed_at - parse_utc(inventory_binding["completed_at"])
        ).total_seconds()
        inventory_current = bool(
            inventory_binding["authority_version"]
            == EVENT_INVENTORY_AUTHORITY_VERSION
            and inventory_binding["generation_id"]
            and inventory_binding["authority_revision"] > 0
            and str(current_inventory.get("authority_version") or "")
            == inventory_binding["authority_version"]
            and str(current_inventory.get("authority_state") or "") == "COMPLETED"
            and str(current_inventory.get("generation_id") or "")
            == inventory_binding["generation_id"]
            and str(current_inventory.get("completed_at") or "")
            == inventory_binding["completed_at"]
            and int(current_inventory.get("authority_revision") or 0)
            == inventory_binding["authority_revision"]
            and -5
            <= inventory_age
            <= EVENT_INVENTORY_AUTHORITY_MAX_AGE_SECONDS
        )
    except (KeyError, TypeError, ValueError):
        inventory_current = False
    integrity_valid = False
    fresh = False
    entries: list[dict[str, Any]] = []
    try:
        entries = sorted(
            (
                {
                    "event_key": str(row["event_key"]),
                    "commence_time": str(row["commence_time"]),
                    "schedule_revision": int(row.get("schedule_revision") or 0),
                    "schedule_identity": str(row["schedule_identity"]),
                    "required_discovery_observed_at": str(
                        row.get("required_discovery_observed_at") or ""
                    ),
                }
                for row in raw_entries
            ),
            key=lambda row: (row["commence_time"], row["event_key"]),
        )
        keys = [row["event_key"] for row in entries]
        expected_digest = digest(
            {
                "version": version,
                "observed_at": manifest_at,
                "inventory_authority": inventory_binding,
                "manifest_error": str(manifest.get("manifest_error") or ""),
                "events": entries,
            }
        )
        integrity_valid = bool(
            manifest.get("entity_type") == "SOCCER_COVERAGE_DISPATCH_MANIFEST"
            and manifest.get("manifest_version") == version
            and not manifest.get("manifest_error")
            and str(manifest.get("manifest_digest") or "") == expected_digest
            and int(manifest.get("event_count") or 0) == len(entries)
            and len(set(keys)) == len(keys)
            and all(
                row["event_key"]
                and row["schedule_identity"]
                and row["schedule_revision"] > 0
                for row in entries
            )
        )
        age_seconds = (observed_at - parse_utc(manifest_at)).total_seconds()
        fresh = -5 <= age_seconds <= COVERAGE_DISPATCH_MANIFEST_MAX_AGE_SECONDS
    except (KeyError, TypeError, ValueError):
        integrity_valid = False
        fresh = False

    rows = (
        store.latest_coverage_cycles(event_keys={row["event_key"] for row in entries})
        if integrity_valid
        else []
    )
    by_event = {
        str(row.get("event_key") or row.get("SK") or ""): dict(row)
        for row in rows
    }
    authoritative_rows: list[dict[str, Any]] = []
    missing = 0
    schedule_mismatch = 0
    schedule_revision_mismatch = 0
    stale_generation = 0
    for entry in entries:
        event_key = entry["event_key"]
        row = by_event.get(event_key)
        reason = ""
        if row is None:
            missing += 1
            reason = "SUMMARY_MISSING"
        elif int(row.get("schedule_revision") or 0) != int(
            entry["schedule_revision"]
        ):
            schedule_revision_mismatch += 1
            reason = "SCHEDULE_REVISION_MISMATCH"
        elif str(row.get("schedule_identity") or "") != entry["schedule_identity"]:
            schedule_mismatch += 1
            reason = "SCHEDULE_IDENTITY_MISMATCH"
        elif (
            entry["required_discovery_observed_at"]
            and str(row.get("discovery_observed_at") or "")
            < entry["required_discovery_observed_at"]
        ):
            stale_generation += 1
            reason = "DISCOVERY_GENERATION_STALE"
        if not reason:
            authoritative_rows.append(row)
            continue
        authoritative_rows.append(
            {
                "PK": "COVERAGE_LATEST",
                "SK": event_key,
                "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
                **entry,
                "discovery_observed_at": str(
                    (row or {}).get("discovery_observed_at") or ""
                ),
                "discovery_status": reason,
                "required_pairs": [],
                "probe_pairs": [],
                "expected_digest": digest([]),
                "plan_digest": "",
                "returned_pairs": [],
                "provider_unavailable_pairs": [],
                "normalization_rejected_pairs": [],
                "attempted_incomplete_pairs": [],
                "quota_deferred_pairs": [],
                "failed_pairs": [],
            }
        )
    return authoritative_rows, {
        "manifest_observed_at": manifest_at,
        "manifest_digest": str(manifest.get("manifest_digest") or ""),
        "manifest_events": len(entries),
        "manifest_declared_events": int(manifest.get("event_count") or 0),
        "manifest_error": str(manifest.get("manifest_error") or ""),
        "manifest_item_size_bytes": int(
            manifest.get("manifest_item_size_bytes") or 0
        ),
        "inventory_authority": inventory_binding,
        "inventory_authority_state": str(
            current_inventory.get("authority_state") or "MISSING"
        ),
        "inventory_authority_current": inventory_current,
        "manifest_fresh": fresh,
        "manifest_integrity_valid": integrity_valid,
        "missing_event_summaries": missing,
        "schedule_identity_mismatches": schedule_mismatch,
        "schedule_revision_mismatches": schedule_revision_mismatch,
        "stale_discovery_generations": stale_generation,
        "authoritative": integrity_valid and fresh and inventory_current,
    }


def coverage(store: SoccerStore) -> dict[str, Any]:
    competitions = store.list_competitions()
    diagnostic_rows, diagnostics_truncated = _bounded_ops_diagnostics(store)
    inventories = [row for row in diagnostic_rows if row.get("entity_type") == "SOCCER_MARKET_INVENTORY"]
    books = set()
    markets = set()
    for row in inventories:
        for book, detail in (row.get("inventory") or {}).items():
            books.add(book)
            markets.update(detail.get("markets") or [])
    cursors, cursors_truncated = _query_partition(store.ops, "HISTORICAL_CURSOR")
    daily_windows, windows_truncated = _query_partition(store.ops, "COLLECTION_WINDOW")
    daily_windows.sort(key=lambda row: row.get("match_day") or "", reverse=True)
    collection_failures = [row for row in diagnostic_rows if row.get("entity_type") == "SOCCER_COLLECTION_FAILURE"]
    quota_blocks, quota_blocks_truncated = _query_partition(store.ops, "QUOTA_GUARD")
    rate_limit_blocks, rate_blocks_truncated = _query_partition(store.ops, "RATE_LIMIT_GUARD")
    coverage_observed_at = now_utc()
    latest_cycle_summaries, coverage_universe = _coverage_summary_universe(
        store,
        observed_at=coverage_observed_at,
    )
    cycle_coverage = _latest_summary_coverage(latest_cycle_summaries)
    expected_pairs = cycle_coverage["expected_pairs"]
    required_pairs = cycle_coverage["required_pairs"]
    probe_pairs = cycle_coverage["probe_pairs"]
    returned_pairs = cycle_coverage["returned_pairs"]
    missing_pairs = sorted(cycle_coverage["missing_pairs"])
    required_missing_pairs = sorted(cycle_coverage["required_missing_pairs"])
    probe_missing_pairs = sorted(cycle_coverage["probe_missing_pairs"])
    unresolved_pairs = sorted(cycle_coverage["unresolved_pairs"])
    unavailable_pairs = sorted(cycle_coverage["provider_unavailable_pairs"])
    rejected_pairs = sorted(cycle_coverage["normalization_rejected_pairs"])
    attempted_incomplete_pairs = sorted(
        cycle_coverage["attempted_incomplete_pairs"]
    )
    deferred_pairs = sorted(cycle_coverage["quota_deferred_pairs"])
    failed_pairs = sorted(cycle_coverage["failed_pairs"])
    never_attempted_pairs = sorted(cycle_coverage["never_attempted_pairs"])
    expected_batch_digests = sorted(cycle_coverage["expected_batch_digests"])
    succeeded_batch_digests = sorted(cycle_coverage["succeeded_batch_digests"])
    failed_batch_digests = sorted(cycle_coverage["failed_batch_digests"])
    deferred_batch_digests = sorted(cycle_coverage["deferred_batch_digests"])
    unresolved_batch_digests = sorted(cycle_coverage["unresolved_batch_digests"])
    for pair in expected_pairs:
        _, bookmaker, market = pair.rsplit("|", 2)
        books.add(bookmaker)
        markets.add(market)
    return {
        "ok": True,
        "system": "soccer_auto",
        "competitions": {
            "known": len(competitions),
            "active": sum(bool(row.get("active")) for row in competitions),
            "rows": competitions,
        },
        "live_inventory": {
            "event_inventory_observations": len(inventories),
            "unique_bookmakers_seen": len(books),
            "bookmakers_seen": sorted(books),
            "unique_markets_seen": len(markets),
            "markets_seen": sorted(markets),
            "expected_event_bookmaker_market_pairs": len(expected_pairs),
            "required_current_event_bookmaker_market_pairs": len(required_pairs),
            "rolling_probe_event_bookmaker_market_pairs": len(probe_pairs),
            "fetched_event_bookmaker_market_pairs": len(returned_pairs),
            "provider_unavailable_event_bookmaker_market_pairs": len(unavailable_pairs),
            "normalization_rejected_event_bookmaker_market_pairs": len(rejected_pairs),
            "attempted_incomplete_event_bookmaker_market_pairs": len(
                attempted_incomplete_pairs
            ),
            "quota_deferred_event_bookmaker_market_pairs": len(deferred_pairs),
            "failed_event_bookmaker_market_pairs": len(failed_pairs),
            "never_attempted_event_bookmaker_market_pairs": len(never_attempted_pairs),
            "unresolved_event_bookmaker_market_pairs": len(unresolved_pairs),
            "expected_request_batches": len(expected_batch_digests),
            "succeeded_request_batches": len(succeeded_batch_digests),
            "failed_request_batches": len(failed_batch_digests),
            "deferred_request_batches": len(deferred_batch_digests),
            "unresolved_request_batches": len(unresolved_batch_digests),
            "missing_event_bookmaker_market_pairs": len(missing_pairs),
            "missing_pair_sample": missing_pairs[:500],
            "required_missing_pair_sample": required_missing_pairs[:500],
            "rolling_probe_missing_pair_sample": probe_missing_pairs[:500],
            "provider_unavailable_pair_sample": unavailable_pairs[:500],
            "normalization_rejected_pair_sample": rejected_pairs[:500],
            "attempted_incomplete_pair_sample": attempted_incomplete_pairs[:500],
            "quota_deferred_pair_sample": deferred_pairs[:500],
            "failed_pair_sample": failed_pairs[:500],
            "never_attempted_pair_sample": never_attempted_pairs[:500],
            "unresolved_pair_sample": unresolved_pairs[:500],
            "failed_batch_sample": failed_batch_digests[:500],
            "deferred_batch_sample": deferred_batch_digests[:500],
            "unresolved_batch_sample": unresolved_batch_digests[:500],
            "latest_event_cycles": cycle_coverage["cycles"][:500],
            "dispatch_manifest": coverage_universe,
            "discovery_status_counts": cycle_coverage["discovery_status_counts"],
            "coverage_integrity_failures": cycle_coverage["integrity_failures"],
            "coverage_error_cycles": cycle_coverage["coverage_error_cycles"],
            "incomplete_latest_event_cycles": sum(
                not row["complete"] for row in cycle_coverage["cycles"]
            ),
            "incomplete_request_cycles": sum(
                not row["request_complete"] for row in cycle_coverage["cycles"]
            ),
            "quota_only_incomplete_request_cycles": sum(
                bool(row.get("quota_only_incomplete"))
                for row in cycle_coverage["cycles"]
            ),
            "non_quota_incomplete_request_cycles": sum(
                not row["request_complete"]
                and not row.get("quota_only_incomplete")
                for row in cycle_coverage["cycles"]
            ),
            "collection_failures": len(collection_failures),
            "permanent_collection_failures": sum(bool(row.get("permanent")) for row in collection_failures),
            "quota_guard_blocks": len(quota_blocks),
            "distributed_rate_limit_blocks": len(rate_limit_blocks),
            "coverage_complete": (
                coverage_universe["authoritative"]
                and
                bool(cycle_coverage["cycles"])
                and all(row["complete"] for row in cycle_coverage["cycles"])
            ),
            "all_planned_pairs_returned": (
                coverage_universe["authoritative"]
                and
                bool(cycle_coverage["cycles"])
                and all(
                    row["all_planned_pairs_returned"]
                    for row in cycle_coverage["cycles"]
                )
            ),
            "request_cycles_complete": (
                coverage_universe["authoritative"]
                and
                bool(cycle_coverage["cycles"])
                and all(row["request_complete"] for row in cycle_coverage["cycles"])
            ),
            "latest_cycle_source": "KEYED_STRONGLY_CONSISTENT_SUMMARY",
            "latest_cycle_read_truncated": False,
            "diagnostics_truncated": diagnostics_truncated,
        },
        "historical_cursors": cursors,
        "historical_backfill": _historical_status(store),
        "response_truncated": any(
            (
                diagnostics_truncated,
                cursors_truncated,
                windows_truncated,
                quota_blocks_truncated,
                rate_blocks_truncated,
            )
        ),
        "shared_provider_safety": provider_safety_config(),
        "distributed_rate_limit_state": store.rate_limit_status(),
        "provider_429_telemetry": store.provider_429_status(),
        "daily_collection_windows": daily_windows[:45],
        "collection_contract": {
            "match_day_timezone": "America/New_York",
            "opens": "10 hours before the first kickoff of each match-day",
            "no_early_market_or_odds_calls": True,
            "cadence": "15 minutes after the window opens and 5 minutes inside T-6h; pre-match only",
        },
        "historical_label_limit": "The Odds API historical odds do not include final results; unlabeled historical snapshots remain training-ineligible.",
    }


def models(store: SoccerStore) -> dict[str, Any]:
    rows = store.model_items()
    return {
        "ok": True,
        "system": "soccer_auto",
        "count": len(rows),
        "models": rows,
    }


def api_handler(event: Mapping[str, Any], context: Any) -> dict[str, Any]:
    try:
        store = SoccerStore()
        path = str(event.get("rawPath") or event.get("path") or "")
        params = event.get("queryStringParameters") or {}
        if path == "/v1/soccer-auto/status":
            return _response(200, status(store))
        if path == "/v1/soccer-auto/predictions":
            return _response(200, predictions(store, min(500, max(1, int(params.get("limit") or 100)))))
        if path == "/v1/soccer-auto/coverage":
            return _response(200, coverage(store))
        if path == "/v1/soccer-auto/models":
            return _response(200, models(store))
        return _response(404, {"ok": False, "system": "soccer_auto", "error": "route_not_found"})
    except Exception as exc:
        return _response(500, {"ok": False, "system": "soccer_auto", "error": str(exc)})
