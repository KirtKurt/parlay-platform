from __future__ import annotations

import unittest

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from botocore.exceptions import ClientError  # noqa: E402
from soccer_auto.api import _latest_cycle_coverage, _latest_summary_coverage  # noqa: E402
from soccer_auto.canonical import digest  # noqa: E402
from soccer_auto.collector import _enqueue_coverage_fanout  # noqa: E402
from soccer_auto.storage import (  # noqa: E402
    EVENT_INVENTORY_AUTHORITY_VERSION,
    COVERAGE_PLAN_VERSION,
    SoccerStore,
    coverage_expected_batch_digests,
    coverage_plan_digest,
)


def conditional_failure():
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "condition"}},
        "PutItem",
    )


def exact_summary(**values):
    row = {
        "event_key": "event",
        "plan_observed_at": "2026-08-14T04:00:00Z",
        "discovery_observed_at": "2026-08-14T03:59:59Z",
        "discovery_status": "HTTP_200",
        "schedule_identity": "identity-event",
        "schedule_revision": 1,
        "required_pairs": [],
        "probe_pairs": [],
        **values,
    }
    required = sorted(set(row.get("required_pairs") or ()))
    probe = sorted(set(row.get("probe_pairs") or ()))
    expected = sorted(set(required) | set(probe))
    request_markets = sorted(
        set(row.get("request_markets") or ())
        or {pair.rsplit("|", 1)[1] for pair in expected}
    )
    row["request_markets"] = request_markets
    row.setdefault("plan_version", COVERAGE_PLAN_VERSION)
    row.setdefault("expected_digest", digest(expected))
    row.setdefault(
        "plan_digest",
        coverage_plan_digest(
            event_key=row["event_key"],
            observed_at=row["plan_observed_at"],
            schedule_revision=row["schedule_revision"],
            schedule_identity_value=row["schedule_identity"],
            request_markets=request_markets,
            required_pairs=required,
            probe_pairs=probe,
        ),
    )
    expected_batches = coverage_expected_batch_digests(
        plan_digest=row["plan_digest"],
        request_markets=request_markets,
        expected_pairs=expected,
    )
    row.setdefault("fanout_expected_batch_digests", expected_batches)
    row.setdefault("fanout_enqueued_batch_digests", expected_batches)
    terminal_pairs = (
        set(row.get("returned_pairs") or ())
        | set(row.get("provider_unavailable_pairs") or ())
        | set(row.get("normalization_rejected_pairs") or ())
    ) & set(expected)
    row.setdefault(
        "fanout_succeeded_batch_digests",
        expected_batches if expected and terminal_pairs == set(expected) else [],
    )
    row.setdefault("fanout_failed_batch_digests", [])
    row.setdefault("fanout_deferred_batch_digests", [])
    return row


def activate_plan_fanout(store, plan, *, event_key="event"):
    batches = coverage_expected_batch_digests(
        plan_digest=plan["plan_digest"],
        request_markets=plan.get("request_markets") or (),
        expected_pairs=plan.get("expected_pairs") or (),
    )
    store.put_coverage_fanout_expected(
        event_key,
        plan_observed_at=plan["plan_observed_at"],
        plan_digest=plan["plan_digest"],
        batch_digests=batches,
        observed_at=plan["plan_observed_at"],
    )
    for batch in batches:
        store.mark_coverage_fanout_enqueued(
            event_key,
            plan_observed_at=plan["plan_observed_at"],
            plan_digest=plan["plan_digest"],
            batch_digest=batch,
            observed_at=plan["plan_observed_at"],
        )
    store.complete_coverage_fanout(
        event_key,
        plan_observed_at=plan["plan_observed_at"],
        plan_digest=plan["plan_digest"],
        observed_at=plan["plan_observed_at"],
    )
    return batches


class CoverageOps:
    def __init__(self):
        self.rows = {}

    def put_item(self, *, Item, ConditionExpression=None, ExpressionAttributeValues=None, **kwargs):
        key = (str(Item["PK"]), str(Item["SK"]))
        current = self.rows.get(key)
        values = ExpressionAttributeValues or {}
        if ConditionExpression and "plan_observed_at <" in ConditionExpression:
            if current and str(current.get("plan_observed_at") or "") >= str(values[":observed_at"]):
                raise conditional_failure()
        if (
            ConditionExpression
            and "observed_at < :observed_at" in ConditionExpression
            and "plan_observed_at" not in ConditionExpression
        ):
            if current and str(current.get("observed_at") or "") >= str(values[":observed_at"]):
                raise conditional_failure()
        if ConditionExpression and "plan_observed_at=:plan_at" in ConditionExpression:
            if (
                not current
                or current.get("plan_observed_at") != values[":plan_at"]
                or current.get("plan_digest") != values[":plan_digest"]
                or int(current.get("summary_revision") or 0) != int(values[":revision"])
            ):
                raise conditional_failure()
        elif ConditionExpression and "summary_revision=:revision" in ConditionExpression:
            if current and int(current.get("summary_revision") or 0) != int(values[":revision"]):
                raise conditional_failure()
        self.rows[key] = dict(Item)

    def get_item(self, *, Key, **kwargs):
        row = self.rows.get((str(Key["PK"]), str(Key["SK"])))
        return {"Item": dict(row)} if row else {}

    def query(self, **kwargs):
        return {
            "Items": [
                dict(row)
                for (pk, _), row in sorted(self.rows.items())
                if pk == "COVERAGE_LATEST"
            ]
        }


class CoverageCycleTests(unittest.TestCase):
    def test_real_store_canonicalizes_and_completes_multi_batch_fanout(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        queued = []
        store.enqueue = lambda payload: queued.append(dict(payload))
        event = {
            "event_key": "event",
            "event_id": "provider-event",
            "sport_key": "soccer_test",
            "commence_time": "2026-08-14T14:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "schedule_revision": 1,
        }
        generation = "2026-08-14T04:00:00Z"
        store.put_coverage_discovery_attempt(
            event,
            discovery_observed_at=generation,
            status="QUEUED",
            observed_at=generation,
        )
        markets = [f"market_{index:02d}" for index in range(48)]
        plan = store.put_coverage_plan(
            event["event_key"],
            {"book": {"markets": markets}},
            "2026-08-14T04:00:01Z",
            event=event,
            discovery_observed_at=generation,
            request_markets=markets,
        )
        self.assertEqual(plan["discovery_status"], "PLAN_READY")

        result = _enqueue_coverage_fanout(
            store,
            event,
            plan,
            observed_at="2026-08-14T04:00:02Z",
        )
        expected = coverage_expected_batch_digests(
            plan_digest=plan["plan_digest"],
            request_markets=plan["request_markets"],
            expected_pairs=plan["expected_pairs"],
        )
        summary = store.latest_coverage_cycles()[0]

        self.assertEqual(len(expected), 5)
        self.assertNotEqual(expected, sorted(expected))
        self.assertEqual(result["fetch_jobs_total"], 5)
        self.assertEqual(result["fetch_jobs_enqueued"], 5)
        self.assertEqual(len(queued), 5)
        self.assertEqual(
            {str(job["batch_digest"]) for job in queued}, set(expected)
        )
        self.assertEqual(
            summary["fanout_expected_batch_digests"], sorted(expected)
        )
        self.assertEqual(
            summary["fanout_enqueued_batch_digests"], sorted(expected)
        )
        self.assertEqual(summary["discovery_status"], "HTTP_200")

    def test_invalid_multi_batch_fanout_reports_every_plan_batch_unresolved(self) -> None:
        summaries = []
        for event_index in range(2):
            markets = [f"market_{index:02d}" for index in range(48)]
            summaries.append(
                exact_summary(
                    event_key=f"event-{event_index}",
                    schedule_identity=f"identity-{event_index}",
                    discovery_status="PLAN_READY",
                    request_markets=markets,
                    required_pairs=[f"book|{market}" for market in markets],
                    fanout_expected_batch_digests=[],
                    fanout_enqueued_batch_digests=[],
                    fanout_succeeded_batch_digests=[],
                )
            )

        result = _latest_summary_coverage(summaries)

        self.assertEqual(result["integrity_failures"], 2)
        self.assertEqual(len(result["expected_batch_digests"]), 10)
        self.assertEqual(len(result["succeeded_batch_digests"]), 0)
        self.assertEqual(len(result["failed_batch_digests"]), 0)
        self.assertEqual(len(result["unresolved_batch_digests"]), 10)
        self.assertEqual(
            result["expected_batch_digests"],
            result["succeeded_batch_digests"]
            | result["failed_batch_digests"]
            | result["unresolved_batch_digests"],
        )
        self.assertTrue(
            all(cycle["expected_batches"] == 5 for cycle in result["cycles"])
        )
        self.assertTrue(
            all(cycle["unresolved_batches"] == 5 for cycle in result["cycles"])
        )

    def test_old_fetch_cannot_satisfy_new_plan(self) -> None:
        plans = [
            {
                "event_key": "event",
                "observed_at": "2026-08-14T04:00:00Z",
                "expected_pairs": ["book|h2h"],
            },
            {
                "event_key": "event",
                "observed_at": "2026-08-14T04:15:00Z",
                "expected_pairs": ["book|h2h", "book|player_shots"],
            },
        ]
        fetches = [
            {
                "event_key": "event",
                "plan_observed_at": "2026-08-14T04:00:00Z",
                "returned_pairs": ["book|h2h", "book|player_shots"],
            }
        ]
        result = _latest_cycle_coverage(plans, fetches)
        self.assertEqual(len(result["expected_pairs"]), 2)
        self.assertEqual(result["returned_pairs"], set())
        self.assertEqual(len(result["missing_pairs"]), 2)
        self.assertFalse(result["cycles"][0]["complete"])

    def test_latest_matching_cycle_reconciles_exact_pairs(self) -> None:
        plans = [
            {
                "event_key": "event",
                "observed_at": "2026-08-14T04:15:00Z",
                "expected_pairs": ["book|h2h", "book|totals"],
            }
        ]
        fetches = [
            {
                "event_key": "event",
                "plan_observed_at": "2026-08-14T04:15:00Z",
                "returned_pairs": ["book|h2h", "book|totals", "book|new_market"],
            }
        ]
        result = _latest_cycle_coverage(plans, fetches)
        self.assertFalse(result["missing_pairs"])
        self.assertEqual(len(result["returned_pairs"]), 2)
        self.assertTrue(result["cycles"][0]["complete"])

    def test_exact_summary_separates_current_availability_from_rolling_probes(self) -> None:
        result = _latest_summary_coverage(
            [
                exact_summary(**{
                    "event_key": "event",
                    "plan_observed_at": "2026-08-14T04:00:00Z",
                    "required_pairs": ["book|h2h"],
                    "probe_pairs": ["book|btts", "book|totals"],
                    "returned_pairs": ["book|h2h"],
                    "normalization_rejected_pairs": ["book|btts"],
                    "provider_unavailable_pairs": ["book|totals"],
                })
            ]
        )
        cycle = result["cycles"][0]
        self.assertTrue(cycle["request_complete"])
        self.assertTrue(cycle["required_availability_complete"])
        self.assertTrue(cycle["complete"])
        self.assertFalse(cycle["all_planned_pairs_returned"])
        self.assertEqual(cycle["missing"], 2)
        self.assertEqual(cycle["unresolved"], 0)

    def test_returned_pair_wins_over_every_earlier_attempt_outcome(self) -> None:
        result = _latest_summary_coverage(
            [
                exact_summary(**{
                    "event_key": "event",
                    "required_pairs": ["book|h2h"],
                    "returned_pairs": ["book|h2h"],
                    "provider_unavailable_pairs": ["book|h2h"],
                    "normalization_rejected_pairs": ["book|h2h"],
                    "quota_deferred_pairs": ["book|h2h"],
                    "failed_pairs": ["book|h2h"],
                })
            ]
        )
        cycle = result["cycles"][0]
        self.assertEqual(cycle["fetched"], 1)
        self.assertEqual(cycle["missing"], 0)
        self.assertEqual(cycle["provider_unavailable"], 0)
        self.assertEqual(cycle["normalization_rejected"], 0)
        self.assertEqual(cycle["quota_deferred"], 0)
        self.assertEqual(cycle["failed"], 0)

    def test_quota_deferred_pair_remains_unresolved_and_incomplete(self) -> None:
        result = _latest_summary_coverage(
            [
                exact_summary(**{
                    "event_key": "event",
                    "required_pairs": ["book|h2h"],
                    "quota_deferred_pairs": ["book|h2h"],
                })
            ]
        )
        cycle = result["cycles"][0]
        self.assertFalse(cycle["request_complete"])
        self.assertFalse(cycle["complete"])
        self.assertEqual(cycle["quota_deferred"], 1)
        self.assertEqual(cycle["unresolved"], 1)

    def test_zero_pair_retryable_batch_is_not_mislabeled_quota_only(self) -> None:
        summary = exact_summary(
            event_key="event",
            required_pairs=[],
            probe_pairs=[],
            request_markets=["btts"],
        )
        batch = summary["fanout_expected_batch_digests"][0]
        summary.update(
            {
                "fanout_enqueued_batch_digests": [batch],
                "fanout_succeeded_batch_digests": [],
                "fanout_failed_batch_digests": [batch],
                "fanout_deferred_batch_digests": [],
                "outcome_counts": {"RETRYABLE_ERROR": 1},
            }
        )
        cycle = _latest_summary_coverage([summary])["cycles"][0]
        self.assertFalse(cycle["request_complete"])
        self.assertFalse(cycle["quota_only_incomplete"])
        self.assertEqual(cycle["failed_batches"], 1)

    def test_internal_or_malformed_deferred_reasons_fail_closed(self) -> None:
        summary = exact_summary(
            event_key="event",
            required_pairs=[],
            probe_pairs=[],
            request_markets=["btts"],
        )
        batch = summary["fanout_expected_batch_digests"][0]
        summary.update(
            {
                "fanout_enqueued_batch_digests": [batch],
                "fanout_deferred_batch_digests": [batch],
                "fanout_deferred_batch_reasons": {
                    batch: ["ATOMIC_ADMISSION_CONTENTION"]
                },
            }
        )
        internal = _latest_summary_coverage([summary])["cycles"][0]
        self.assertFalse(internal["integrity_valid"])
        self.assertFalse(internal["quota_only_incomplete"])

        summary["fanout_deferred_batch_reasons"] = "not-a-map"
        malformed = _latest_summary_coverage([summary])["cycles"][0]
        self.assertFalse(malformed["integrity_valid"])
        self.assertFalse(malformed["quota_only_incomplete"])

    def test_pending_discovery_is_visible_and_cannot_be_complete(self) -> None:
        result = _latest_summary_coverage(
            [
                {
                    "event_key": "pending",
                    "discovery_observed_at": "2026-08-14T04:15:00Z",
                    "discovery_status": "QUOTA_DEFERRED",
                    "required_pairs": [],
                    "probe_pairs": [],
                    "expected_digest": digest([]),
                }
            ]
        )
        cycle = result["cycles"][0]
        self.assertFalse(cycle["discovery_complete"])
        self.assertFalse(cycle["request_complete"])
        self.assertFalse(cycle["complete"])
        self.assertEqual(result["discovery_status_counts"], {"QUOTA_DEFERRED": 1})

    def test_digest_or_partition_tamper_fails_closed(self) -> None:
        summary = exact_summary(
            event_key="event",
            required_pairs=["book|h2h"],
            probe_pairs=["book|h2h"],
            returned_pairs=["book|h2h"],
            expected_digest="tampered",
        )
        result = _latest_summary_coverage([summary])
        cycle = result["cycles"][0]
        self.assertFalse(cycle["integrity_valid"])
        self.assertFalse(cycle["complete"])
        self.assertEqual(result["integrity_failures"], 1)

    def test_required_pair_cannot_be_relabelled_as_a_probe(self) -> None:
        original = exact_summary(
            event_key="event",
            required_pairs=["book|h2h"],
            returned_pairs=[],
        )
        repainted = {
            **original,
            "required_pairs": [],
            "probe_pairs": ["book|h2h"],
        }
        cycle = _latest_summary_coverage([repainted])["cycles"][0]
        self.assertFalse(cycle["integrity_valid"])
        self.assertFalse(cycle["complete"])

    def test_unexpected_response_pair_cannot_satisfy_an_unattempted_scope(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        plan = store.put_coverage_plan(
            "event",
            {"book": {"markets": ["h2h", "totals"]}},
            "2026-08-14T04:00:00Z",
            request_markets=["h2h", "totals"],
        )
        activate_plan_fanout(store, plan)
        fetch = store.put_coverage_fetch(
            "event",
            {
                "bookmakers": [
                    {
                        "key": "book",
                        "markets": [{"key": "h2h"}, {"key": "totals"}],
                    }
                ]
            },
            observed_at="2026-08-14T04:00:01Z",
            requested_bookmakers=(),
            requested_markets=("h2h",),
            plan_observed_at=plan["plan_observed_at"],
            plan_digest=plan["plan_digest"],
            planned_pairs=("book|h2h",),
            raw_returned_pairs=("book|h2h", "book|totals"),
        )
        summary = store.latest_coverage_cycles()[0]
        self.assertEqual(summary["returned_pairs"], ["book|h2h"])
        self.assertEqual(fetch["unexpected_returned_pairs"], ["book|totals"])
        cycle = _latest_summary_coverage([summary])["cycles"][0]
        self.assertFalse(cycle["complete"])
        self.assertEqual(cycle["never_attempted"], 1)

    def test_complementary_region_children_terminalize_absence_only_when_complete(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        plan = store.put_coverage_plan(
            "event",
            {"book": {"markets": ["h2h"]}},
            "2026-08-14T04:00:00Z",
            request_markets=["h2h"],
        )
        root_batch = activate_plan_fanout(store, plan)[0]
        for index in range(2):
            store.put_coverage_fetch(
                "event",
                {"bookmakers": []},
                observed_at=f"2026-08-14T04:00:0{index + 1}Z",
                requested_bookmakers=(),
                requested_markets=("h2h",),
                plan_observed_at=plan["plan_observed_at"],
                plan_digest=plan["plan_digest"],
                planned_pairs=("book|h2h",),
                raw_returned_pairs=(),
                outcome="HTTP_200",
                absence_scope_complete=False,
                split_group_digest=root_batch,
                batch_digest=f"child-{index}",
                attempted_regions=(("us",), ("uk",))[index],
                split_expected_regions=("us", "uk"),
            )
            cycle = _latest_summary_coverage(store.latest_coverage_cycles())["cycles"][0]
            if index == 0:
                self.assertEqual(cycle["attempted_incomplete"], 1)
                self.assertFalse(cycle["request_complete"])
        cycle = _latest_summary_coverage(store.latest_coverage_cycles())["cycles"][0]
        self.assertEqual(cycle["attempted_incomplete"], 0)
        self.assertEqual(cycle["never_attempted"], 0)
        self.assertEqual(cycle["provider_unavailable"], 1)
        self.assertTrue(cycle["request_complete"])

    def test_partial_split_success_plus_external_quota_is_exact_quota_only(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        plan = store.put_coverage_plan(
            "event",
            {"book": {"markets": ["h2h"]}},
            "2026-08-14T04:00:00Z",
            request_markets=["h2h"],
        )
        root = activate_plan_fanout(store, plan)[0]
        common = {
            "requested_bookmakers": (),
            "requested_markets": ("h2h",),
            "plan_observed_at": plan["plan_observed_at"],
            "plan_digest": plan["plan_digest"],
            "planned_pairs": ("book|h2h",),
            "raw_returned_pairs": (),
            "absence_scope_complete": False,
            "split_group_digest": root,
        }
        store.put_coverage_fetch(
            "event",
            {"bookmakers": []},
            observed_at="2026-08-14T04:00:01Z",
            outcome="SPLIT_PENDING",
            batch_digest=root,
            split_child_leaf_ids=("region-a", "region-b"),
            **common,
        )
        store.put_coverage_fetch(
            "event",
            {"bookmakers": []},
            observed_at="2026-08-14T04:00:02Z",
            outcome="HTTP_200",
            batch_digest="region-a-batch",
            split_leaf_id="region-a",
            split_expected_leaf_ids=("region-a", "region-b"),
            **common,
        )
        store.put_coverage_fetch(
            "event",
            {"bookmakers": []},
            observed_at="2026-08-14T04:00:03Z",
            outcome="QUOTA_DEFERRED",
            budget_reason="RACE_BUFFER_REACHED",
            batch_digest="region-b-batch",
            split_leaf_id="region-b",
            split_expected_leaf_ids=("region-a", "region-b"),
            **common,
        )
        cycle = _latest_summary_coverage(store.latest_coverage_cycles())["cycles"][0]
        self.assertEqual(cycle["attempted_incomplete"], 0)
        self.assertEqual(cycle["quota_deferred"], 1)
        self.assertEqual(cycle["deferred_batches"], 1)
        self.assertTrue(cycle["quota_only_incomplete"])

        store.put_coverage_fetch(
            "event",
            {"bookmakers": []},
            observed_at="2026-08-14T04:00:04Z",
            outcome="HTTP_200",
            batch_digest="region-b-batch",
            split_leaf_id="region-b",
            split_expected_leaf_ids=("region-a", "region-b"),
            **common,
        )
        resolved = _latest_summary_coverage(store.latest_coverage_cycles())["cycles"][0]
        self.assertEqual(resolved["quota_deferred"], 0)
        self.assertEqual(resolved["deferred_batches"], 0)
        self.assertEqual(resolved["provider_unavailable"], 1)
        self.assertTrue(resolved["request_complete"])

    def test_split_retryable_leaf_cannot_later_be_repainted_as_quota(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        plan = store.put_coverage_plan(
            "event",
            {},
            "2026-08-14T04:00:00Z",
            request_markets=["btts"],
        )
        root = activate_plan_fanout(store, plan)[0]
        common = {
            "requested_bookmakers": (),
            "requested_markets": ("btts",),
            "plan_observed_at": plan["plan_observed_at"],
            "plan_digest": plan["plan_digest"],
            "planned_pairs": (),
            "raw_returned_pairs": (),
            "absence_scope_complete": False,
            "split_group_digest": root,
            "batch_digest": "child-batch",
            "split_leaf_id": "child",
            "split_expected_leaf_ids": ("child",),
        }
        store.put_coverage_fetch(
            "event",
            {"bookmakers": []},
            observed_at="2026-08-14T04:00:01Z",
            outcome="SPLIT_PENDING",
            batch_digest=root,
            split_leaf_id=None,
            split_expected_leaf_ids=(),
            split_child_leaf_ids=("child",),
            **{key: value for key, value in common.items() if key not in {
                "batch_digest", "split_leaf_id", "split_expected_leaf_ids"
            }},
        )
        store.put_coverage_fetch(
            "event",
            {"bookmakers": []},
            observed_at="2026-08-14T04:00:02Z",
            outcome="RETRYABLE_ERROR",
            **common,
        )
        store.put_coverage_fetch(
            "event",
            {"bookmakers": []},
            observed_at="2026-08-14T04:00:03Z",
            outcome="QUOTA_DEFERRED",
            budget_reason="RACE_BUFFER_REACHED",
            **common,
        )
        summary = store.latest_coverage_summary("event")
        self.assertIn(root, summary["fanout_failed_batch_digests"])
        self.assertNotIn(root, summary["fanout_deferred_batch_digests"])
        cycle = _latest_summary_coverage([summary])["cycles"][0]
        self.assertFalse(cycle["quota_only_incomplete"])

    def test_new_dispatch_generation_blocks_an_older_discovery_response(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        event = {
            "event_key": "event",
            "event_id": "id",
            "sport_key": "soccer_test",
            "commence_time": "2026-08-14T14:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "schedule_revision": 1,
        }
        first = "2026-08-14T04:00:00Z"
        second = "2026-08-14T04:15:00Z"
        store.put_coverage_discovery_attempt(
            event,
            discovery_observed_at=first,
            status="QUEUED",
            observed_at=first,
        )
        store.put_coverage_discovery_attempt(
            event,
            discovery_observed_at=second,
            status="QUEUED",
            observed_at=second,
        )
        stale = store.put_coverage_plan(
            "event",
            {"book": {"markets": ["h2h"]}},
            "2026-08-14T04:15:01Z",
            event=event,
            discovery_observed_at=first,
        )
        latest = store.latest_coverage_cycles()[0]
        self.assertFalse(stale["latest_summary_updated"])
        self.assertEqual(latest["discovery_observed_at"], second)
        self.assertEqual(latest["discovery_status"], "QUEUED")
        self.assertFalse(latest.get("plan_observed_at"))

    def test_completed_discovery_generation_cannot_regress_or_reset(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        event = {
            "event_key": "event",
            "event_id": "id",
            "sport_key": "soccer_test",
            "commence_time": "2026-08-14T14:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "schedule_revision": 1,
        }
        generation = "2026-08-14T04:00:00Z"
        store.put_coverage_discovery_attempt(
            event,
            discovery_observed_at=generation,
            status="QUEUED",
            observed_at=generation,
        )
        plan = store.put_coverage_plan(
            "event",
            {"book": {"markets": ["h2h"]}},
            "2026-08-14T04:00:01Z",
            event=event,
            discovery_observed_at=generation,
            request_markets=["h2h"],
        )
        batch = coverage_expected_batch_digests(
            plan_digest=plan["plan_digest"],
            request_markets=plan["request_markets"],
            expected_pairs=plan["expected_pairs"],
        )[0]
        store.put_coverage_fanout_expected(
            "event",
            plan_observed_at=plan["plan_observed_at"],
            plan_digest=plan["plan_digest"],
            batch_digests=[batch],
            observed_at="2026-08-14T04:00:01Z",
        )
        store.mark_coverage_fanout_enqueued(
            "event",
            plan_observed_at=plan["plan_observed_at"],
            plan_digest=plan["plan_digest"],
            batch_digest=batch,
            observed_at="2026-08-14T04:00:01Z",
        )
        store.complete_coverage_fanout(
            "event",
            plan_observed_at=plan["plan_observed_at"],
            plan_digest=plan["plan_digest"],
            observed_at="2026-08-14T04:00:01Z",
        )
        duplicate = store.put_coverage_discovery_attempt(
            event,
            discovery_observed_at=generation,
            status="STARTED",
            observed_at="2026-08-14T04:00:02Z",
        )
        self.assertTrue(plan["latest_summary_updated"])
        self.assertFalse(duplicate["latest_summary_updated"])
        self.assertEqual(duplicate["discovery_status"], "HTTP_200")
        self.assertEqual(duplicate["plan_digest"], plan["plan_digest"])

    def test_same_slot_schedule_revision_replaces_the_old_identity(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        generation = "2026-08-14T04:00:00Z"
        first = {
            "event_key": "event",
            "event_id": "id",
            "sport_key": "soccer_test",
            "commence_time": "2026-08-14T14:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "schedule_revision": 1,
        }
        revised = {
            **first,
            "commence_time": "2026-08-14T14:30:00Z",
            "schedule_revision": 2,
        }
        store.put_coverage_discovery_attempt(
            first,
            discovery_observed_at=generation,
            status="QUEUED",
            observed_at=generation,
        )
        store.put_coverage_plan(
            "event",
            {"book": {"markets": ["h2h"]}},
            "2026-08-14T04:00:01Z",
            event=first,
            discovery_observed_at=generation,
        )
        advanced = store.put_coverage_discovery_attempt(
            revised,
            discovery_observed_at=generation,
            status="QUEUED",
            observed_at="2026-08-14T04:00:02Z",
        )
        self.assertTrue(advanced["latest_summary_updated"])
        self.assertEqual(advanced["schedule_revision"], 2)
        self.assertEqual(advanced["discovery_status"], "QUEUED")
        self.assertFalse(advanced.get("plan_observed_at"))

    def test_dispatch_manifest_is_exact_and_cannot_be_repainted_by_an_older_run(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        entries = [
            {
                "event_key": "event",
                "commence_time": "2026-08-14T14:00:00Z",
                "schedule_revision": 1,
                "schedule_identity": "identity",
                "required_discovery_observed_at": "2026-08-14T04:15:00Z",
            }
        ]
        inventory_authority = {
            "valid": True,
            "authority_version": EVENT_INVENTORY_AUTHORITY_VERSION,
            "generation_id": "inventory-test",
            "completed_at": "2026-08-14T04:15:00Z",
            "authority_revision": 2,
            "reason": "",
        }
        latest = store.put_coverage_dispatch_manifest(
            entries,
            observed_at="2026-08-14T04:15:01Z",
            inventory_authority=inventory_authority,
        )
        stale = store.put_coverage_dispatch_manifest(
            [],
            observed_at="2026-08-14T04:14:01Z",
            inventory_authority=inventory_authority,
        )
        self.assertTrue(latest["latest_manifest_updated"])
        self.assertFalse(stale["latest_manifest_updated"])
        self.assertEqual(stale["event_count"], 1)
        self.assertEqual(
            store.latest_coverage_dispatch_manifest()["manifest_digest"],
            latest["manifest_digest"],
        )

    def test_storage_materializes_raw_absence_and_normalization_rejection_exactly(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        plan = store.put_coverage_plan(
            "event",
            {"book": {"markets": ["btts", "h2h", "totals"]}},
            "2026-08-14T04:00:00Z",
            required_inventory={"book": {"markets": ["btts", "h2h"]}},
            request_markets=["btts", "h2h", "totals"],
        )
        root_batch = activate_plan_fanout(store, plan)[0]
        store.put_coverage_fetch(
            "event",
            {"bookmakers": [{"key": "book", "markets": [{"key": "h2h"}]}]},
            observed_at="2026-08-14T04:00:01Z",
            requested_bookmakers=(),
            requested_markets=("btts", "h2h", "totals"),
            plan_observed_at=plan["plan_observed_at"],
            plan_digest=plan["plan_digest"],
            planned_pairs=plan["expected_pairs"],
            raw_returned_pairs=("book|btts", "book|h2h"),
            outcome="HTTP_200",
            absence_scope_complete=True,
            batch_digest=root_batch,
        )
        summary = store.latest_coverage_cycles()[0]
        self.assertEqual(summary["returned_pairs"], ["book|h2h"])
        self.assertEqual(summary["normalization_rejected_pairs"], ["book|btts"])
        self.assertEqual(summary["provider_unavailable_pairs"], ["book|totals"])
        store.put_coverage_fetch(
            "event",
            {
                "bookmakers": [
                    {
                        "key": "book",
                        "markets": [
                            {"key": "btts"},
                            {"key": "h2h"},
                            {"key": "totals"},
                        ],
                    }
                ]
            },
            observed_at="2026-08-14T04:00:02Z",
            requested_bookmakers=(),
            requested_markets=("btts", "h2h", "totals"),
            plan_observed_at=plan["plan_observed_at"],
            plan_digest=plan["plan_digest"],
            planned_pairs=plan["expected_pairs"],
            raw_returned_pairs=plan["expected_pairs"],
            outcome="HTTP_200",
            absence_scope_complete=True,
        )
        summary = store.latest_coverage_cycles()[0]
        self.assertEqual(summary["returned_pairs"], plan["expected_pairs"])
        self.assertEqual(summary["normalization_rejected_pairs"], [])
        self.assertEqual(summary["provider_unavailable_pairs"], [])

    def test_older_plan_cannot_repaint_the_latest_keyed_summary(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        latest = store.put_coverage_plan(
            "event",
            {"book": {"markets": ["h2h"]}},
            "2026-08-14T04:15:00Z",
        )
        stale = store.put_coverage_plan(
            "event",
            {"book": {"markets": ["totals"]}},
            "2026-08-14T04:00:00Z",
        )
        self.assertEqual(stale["plan_digest"], latest["plan_digest"])
        self.assertEqual(stale["plan_observed_at"], "2026-08-14T04:15:00Z")
        self.assertEqual(store.latest_coverage_cycles()[0]["required_pairs"], ["book|h2h"])

    def test_unrelated_ops_rows_cannot_truncate_the_keyed_latest_cycle_read(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = CoverageOps()
        store.put_coverage_plan(
            "event",
            {"book": {"markets": ["h2h"]}},
            "2026-08-14T04:00:00Z",
        )
        for index in range(2500):
            store.ops.rows[(f"UNRELATED#{index}", "ROW")] = {
                "PK": f"UNRELATED#{index}",
                "SK": "ROW",
            }
        rows = store.latest_coverage_cycles(event_keys={"event"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_key"], "event")


if __name__ == "__main__":
    unittest.main()
