from __future__ import annotations

import inspect
import unittest
from datetime import timedelta
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.api import coverage, predictions  # noqa: E402
from soccer_auto.canonical import digest, iso_utc, parse_utc, schedule_identity  # noqa: E402
from soccer_auto.storage import (  # noqa: E402
    COVERAGE_DISPATCH_MANIFEST_VERSION,
    COVERAGE_PLAN_VERSION,
    EVENT_INVENTORY_AUTHORITY_VERSION,
    coverage_expected_batch_digests,
    coverage_plan_digest,
    now_utc,
)


class PredictionTable:
    def __init__(self, rows):
        self.rows = rows

    def query(self, **kwargs):
        return {"Items": list(self.rows)}


class OpsTable:
    def __init__(self, bindings):
        self.bindings = {
            (str(row["PK"]), str(row["SK"])): dict(row)
            for row in bindings
        }

    def get_item(self, **kwargs):
        key = kwargs["Key"]
        row = self.bindings.get((str(key["PK"]), str(key["SK"])))
        return {"Item": dict(row)} if row else {}


class Store:
    def __init__(self, rows, current, bindings):
        self.predictions = PredictionTable(rows)
        self.current = current
        self.ops = OpsTable(bindings)

    def get_event(self, event_key):
        return self.current.get(event_key)


class CoverageOps:
    def scan(self, **kwargs):
        return {"Items": []}

    def query(self, **kwargs):
        return {"Items": []}


class CoverageStore:
    def __init__(self, cycles=None):
        self.ops = CoverageOps()
        self.active_after = None
        self.cycles = cycles

    def list_competitions(self):
        return [{"sport_key": "soccer_test", "active": True}]

    def latest_coverage_cycles(self, *, active_after=None, **kwargs):
        self.active_after = active_after
        rows = self.cycles if self.cycles is not None else [
            {
                "event_key": "EVENT#soccer_test#one",
                "plan_observed_at": "2026-08-14T04:00:00Z",
                "plan_digest": "plan",
                "discovery_observed_at": "2026-08-14T03:59:59Z",
                "discovery_status": "HTTP_200",
                "required_pairs": ["book|h2h"],
                "probe_pairs": ["book|totals"],
                "expected_digest": digest(["book|h2h", "book|totals"]),
                "returned_pairs": ["book|h2h"],
                "provider_unavailable_pairs": ["book|totals"],
            }
        ]
        normalized = [
            {
                "commence_time": "2026-08-14T14:00:00Z",
                "schedule_revision": 1,
                "schedule_identity": f"identity-{row['event_key']}",
                **row,
            }
            for row in rows
        ]
        for row in normalized:
            if row.get("plan_observed_at"):
                expected = sorted(
                    set(row.get("required_pairs") or ())
                    | set(row.get("probe_pairs") or ())
                )
                request_markets = sorted(
                    set(row.get("request_markets") or ())
                    or {pair.rsplit("|", 1)[1] for pair in expected}
                )
                row["request_markets"] = request_markets
                row["plan_version"] = COVERAGE_PLAN_VERSION
                row["plan_digest"] = coverage_plan_digest(
                    event_key=row["event_key"],
                    observed_at=row["plan_observed_at"],
                    schedule_revision=row["schedule_revision"],
                    schedule_identity_value=row["schedule_identity"],
                    request_markets=request_markets,
                    required_pairs=sorted(row.get("required_pairs") or ()),
                    probe_pairs=sorted(row.get("probe_pairs") or ()),
                )
                batches = coverage_expected_batch_digests(
                    plan_digest=row["plan_digest"],
                    request_markets=request_markets,
                    expected_pairs=expected,
                )
                row.setdefault("fanout_expected_batch_digests", batches)
                row.setdefault("fanout_enqueued_batch_digests", batches)
                terminal = (
                    set(row.get("returned_pairs") or ())
                    | set(row.get("provider_unavailable_pairs") or ())
                    | set(row.get("normalization_rejected_pairs") or ())
                ) & set(expected)
                row.setdefault(
                    "fanout_succeeded_batch_digests",
                    batches if expected and terminal == set(expected) else [],
                )
                row.setdefault("fanout_failed_batch_digests", [])
                row.setdefault("fanout_deferred_batch_digests", [])
        return normalized

    def latest_coverage_dispatch_manifest(self):
        rows = self.latest_coverage_cycles()
        observed_at = iso_utc(now_utc())
        entries = sorted(
            [
                {
                    "event_key": row["event_key"],
                    "commence_time": row["commence_time"],
                    "schedule_revision": row["schedule_revision"],
                    "schedule_identity": row["schedule_identity"],
                    "required_discovery_observed_at": row["discovery_observed_at"],
                }
                for row in rows
            ],
            key=lambda row: (row["commence_time"], row["event_key"]),
        )
        version = COVERAGE_DISPATCH_MANIFEST_VERSION
        inventory_binding = {
            "authority_version": EVENT_INVENTORY_AUTHORITY_VERSION,
            "generation_id": "inventory-test",
            "completed_at": observed_at,
            "authority_revision": 2,
        }
        self.inventory_binding = inventory_binding
        return {
            "entity_type": "SOCCER_COVERAGE_DISPATCH_MANIFEST",
            "manifest_version": version,
            "manifest_digest": digest(
                {
                    "version": version,
                    "observed_at": observed_at,
                    "inventory_authority": inventory_binding,
                    "manifest_error": "",
                    "events": entries,
                }
            ),
            "observed_at": observed_at,
            "events": entries,
            "event_count": len(entries),
            "inventory_authority": inventory_binding,
            "manifest_error": "",
        }

    def event_inventory_authority(self):
        binding = self.inventory_binding
        return {
            **binding,
            "authority_state": "COMPLETED",
        }

    def rate_limit_status(self):
        return None

    def provider_429_status(self):
        return {"rolling_count": 0, "latest_rows": []}


class MissingSummaryCoverageStore(CoverageStore):
    def latest_coverage_dispatch_manifest(self):
        manifest = super().latest_coverage_dispatch_manifest()
        entries = list(manifest["events"])
        entries.append(
            {
                "event_key": "EVENT#soccer_test#missing",
                "commence_time": "2026-08-14T15:00:00Z",
                "schedule_revision": 1,
                "schedule_identity": "identity-missing",
                "required_discovery_observed_at": manifest["observed_at"],
            }
        )
        entries.sort(key=lambda row: (row["commence_time"], row["event_key"]))
        manifest["events"] = entries
        manifest["event_count"] = len(entries)
        manifest["manifest_digest"] = digest(
            {
                "version": manifest["manifest_version"],
                "observed_at": manifest["observed_at"],
                "inventory_authority": manifest["inventory_authority"],
                "manifest_error": manifest.get("manifest_error") or "",
                "events": entries,
            }
        )
        return manifest


class StaleManifestCoverageStore(CoverageStore):
    def latest_coverage_dispatch_manifest(self):
        manifest = super().latest_coverage_dispatch_manifest()
        observed_at = iso_utc(parse_utc(manifest["observed_at"]) - timedelta(minutes=10))
        manifest["observed_at"] = observed_at
        manifest["manifest_digest"] = digest(
            {
                "version": manifest["manifest_version"],
                "observed_at": observed_at,
                "inventory_authority": manifest["inventory_authority"],
                "manifest_error": manifest.get("manifest_error") or "",
                "events": manifest["events"],
            }
        )
        return manifest


class RunningInventoryCoverageStore(CoverageStore):
    def event_inventory_authority(self):
        return {
            "authority_version": EVENT_INVENTORY_AUTHORITY_VERSION,
            "authority_state": "RUNNING",
            "generation_id": "new-inventory-generation",
            "completed_at": "",
            "authority_revision": 3,
        }

def row(*, model: str, revision: int = 4, status: str = "PUBLISHED", created: str):
    value = {
        "event_key": "EVENT#soccer_test#one",
        "event_id": "one",
        "sport_key": "soccer_test",
        "schedule_revision": revision,
        "commence_time": "2026-08-14T14:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "horizon": "T45",
        "target": "result_1x2",
        "feature_hash": "feature-four",
        "model_digest": model,
        "model_authority": "CHAMPION",
        "prediction_status": status,
        "created_at": created,
        "immutable": True,
    }
    value["schedule_identity"] = schedule_identity(value)
    return value


def current_event(*, revision: int = 4):
    value = {
        "event_key": "EVENT#soccer_test#one",
        "event_id": "one",
        "sport_key": "soccer_test",
        "schedule_revision": revision,
        "commence_time": "2026-08-14T14:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
    }
    value["schedule_identity"] = schedule_identity(value)
    return value


def binding(*, model: str, revision: int = 4):
    event = current_event(revision=revision)
    return {
        "PK": "PUBLIC_PREDICTION_BINDING#EVENT#soccer_test#one",
        "SK": f"REV#{revision}#HORIZON#T45#TARGET#result_1x2",
        "entity_type": "SOCCER_PUBLIC_PREDICTION_BINDING",
        "binding_version": "soccer-auto-public-prediction-binding-v1",
        "event_key": event["event_key"],
        "event_id": event["event_id"],
        "sport_key": event["sport_key"],
        "commence_time": event["commence_time"],
        "schedule_revision": revision,
        "schedule_identity": event["schedule_identity"],
        "horizon": "T45",
        "target": "result_1x2",
        "lock_sk": f"LOCK#T45#REV#{revision}#TARGET#result_1x2",
        "feature_hash": "feature-four",
        "model_digest": model,
        "immutable": True,
    }


class ApiRepaintTests(unittest.TestCase):
    def test_public_endpoint_suppresses_shadow_stale_and_duplicate_authorities(self):
        rows = [
            row(model="shadow", status="SHADOW", created="2026-08-14T13:15:00Z"),
            row(model="old-revision", revision=3, created="2026-08-14T13:14:00Z"),
            row(model="first-bound", created="2026-08-14T13:15:01Z"),
            row(model="later-repaint", created="2026-08-14T13:16:01Z"),
        ]
        current = {"EVENT#soccer_test#one": current_event()}
        result = predictions(Store(rows, current, [binding(model="first-bound")]))
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["predictions"][0]["model_digest"], "first-bound")
        self.assertEqual(result["audit_rows_suppressed"], 3)

    def test_public_endpoint_requires_champion_current_identity_and_exact_binding(self):
        valid = row(model="bound", created="2026-08-14T13:15:01Z")
        challenger = {**valid, "model_digest": "challenger", "model_authority": "PROSPECTIVE_SHADOW"}
        copied_identity = {
            **valid,
            "model_digest": "copied-identity",
            "home_team": "Repainted Home",
        }
        wrong_model = {**valid, "model_digest": "not-bound"}
        missing_binding = row(model="missing-binding", revision=5, created="2026-08-14T13:17:00Z")
        current = {"EVENT#soccer_test#one": current_event()}

        result = predictions(
            Store(
                [challenger, copied_identity, wrong_model, missing_binding, valid],
                current,
                [binding(model="bound")],
            )
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["predictions"][0]["model_digest"], "bound")
        self.assertEqual(result["audit_rows_suppressed"], 4)

    def test_public_endpoint_fails_closed_without_immutable_binding(self):
        decision = row(model="bound", created="2026-08-14T13:15:01Z")
        current = {"EVENT#soccer_test#one": current_event()}
        mutable = {**binding(model="bound"), "immutable": False}

        missing = predictions(Store([decision], current, []))
        mutable_result = predictions(Store([decision], current, [mutable]))

        self.assertEqual(missing["count"], 0)
        self.assertEqual(mutable_result["count"], 0)

    def test_coverage_hot_path_has_no_unbounded_scan_all(self):
        source = inspect.getsource(coverage)
        self.assertNotIn("scan_all", source)
        self.assertIn("_bounded_ops_diagnostics", source)
        self.assertIn("_coverage_summary_universe", source)

    def test_coverage_uses_exact_cycle_outcomes_without_calling_probe_absence_fetched(self):
        store = CoverageStore()
        with patch("soccer_auto.api._historical_status", return_value={"state": "RUNNING"}):
            result = coverage(store)
        inventory = result["live_inventory"]
        self.assertTrue(inventory["dispatch_manifest"]["authoritative"])
        self.assertEqual(inventory["required_current_event_bookmaker_market_pairs"], 1)
        self.assertEqual(inventory["rolling_probe_event_bookmaker_market_pairs"], 1)
        self.assertEqual(inventory["fetched_event_bookmaker_market_pairs"], 1)
        self.assertEqual(inventory["provider_unavailable_event_bookmaker_market_pairs"], 1)
        self.assertTrue(inventory["coverage_complete"])
        self.assertTrue(inventory["request_cycles_complete"])
        self.assertFalse(inventory["all_planned_pairs_returned"])
        self.assertEqual(inventory["latest_cycle_source"], "KEYED_STRONGLY_CONSISTENT_SUMMARY")

    def test_complete_event_cannot_hide_a_pending_or_empty_event(self):
        complete = {
            "event_key": "EVENT#soccer_test#complete",
            "plan_observed_at": "2026-08-14T04:00:00Z",
            "plan_digest": "complete-plan",
            "discovery_observed_at": "2026-08-14T03:59:59Z",
            "discovery_status": "HTTP_200",
            "required_pairs": ["book|h2h"],
            "probe_pairs": [],
            "expected_digest": digest(["book|h2h"]),
            "returned_pairs": ["book|h2h"],
        }
        pending = {
            "event_key": "EVENT#soccer_test#pending",
            "discovery_observed_at": "2026-08-14T04:15:00Z",
            "discovery_status": "QUOTA_DEFERRED",
            "required_pairs": [],
            "probe_pairs": [],
            "expected_digest": digest([]),
        }
        empty = {
            **pending,
            "event_key": "EVENT#soccer_test#empty",
            "plan_observed_at": "2026-08-14T04:15:01Z",
            "plan_digest": "empty-plan",
            "discovery_status": "HTTP_200",
        }
        with patch("soccer_auto.api._historical_status", return_value={"state": "RUNNING"}):
            pending_result = coverage(CoverageStore([complete, pending]))
            empty_result = coverage(CoverageStore([complete, empty]))
        for result in (pending_result, empty_result):
            inventory = result["live_inventory"]
            self.assertFalse(inventory["coverage_complete"])
            self.assertFalse(inventory["request_cycles_complete"])
            self.assertEqual(inventory["incomplete_latest_event_cycles"], 1)

    def test_missing_summary_or_stale_dispatch_manifest_fails_closed(self):
        with patch("soccer_auto.api._historical_status", return_value={"state": "RUNNING"}):
            missing = coverage(MissingSummaryCoverageStore())
            stale = coverage(StaleManifestCoverageStore())
        missing_inventory = missing["live_inventory"]
        self.assertFalse(missing_inventory["coverage_complete"])
        self.assertEqual(
            missing_inventory["dispatch_manifest"]["missing_event_summaries"], 1
        )
        stale_inventory = stale["live_inventory"]
        self.assertFalse(stale_inventory["coverage_complete"])
        self.assertFalse(stale_inventory["dispatch_manifest"]["manifest_fresh"])

    def test_new_inventory_generation_immediately_invalidates_prior_manifest(self):
        complete = {
            "event_key": "EVENT#soccer_test#one",
            "discovery_observed_at": "2026-08-14T04:00:00Z",
            "discovery_status": "HTTP_200",
            "plan_observed_at": "2026-08-14T04:00:01Z",
            "required_pairs": ["book|h2h"],
            "returned_pairs": ["book|h2h"],
        }
        with patch("soccer_auto.api._historical_status", return_value={"state": "RUNNING"}):
            result = coverage(RunningInventoryCoverageStore([complete]))
        manifest = result["live_inventory"]["dispatch_manifest"]
        self.assertFalse(manifest["inventory_authority_current"])
        self.assertEqual(manifest["inventory_authority_state"], "RUNNING")
        self.assertFalse(manifest["authoritative"])
        self.assertFalse(result["live_inventory"]["coverage_complete"])


if __name__ == "__main__":
    unittest.main()
