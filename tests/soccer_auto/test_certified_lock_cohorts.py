from __future__ import annotations

import unittest

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.canonical import digest, schedule_identity, scope_hash  # noqa: E402
from soccer_auto.config import ALL_BOOKMAKER_REGIONS  # noqa: E402
from soccer_auto.inference import (  # noqa: E402
    build_frozen_lock,
    live_lock_coverage_provenance_valid,
)
from soccer_auto.storage import (  # noqa: E402
    COVERAGE_CERTIFICATE_VERSION,
    COVERAGE_PLAN_VERSION,
    coverage_certificate_digest,
    coverage_cycle_complete,
    coverage_expected_batch_digests,
    coverage_plan_digest,
)


EVENT_KEY = "EVENT#soccer_test#certified-event"


def event_row():
    row = {
        "event_key": EVENT_KEY,
        "event_id": "certified-event",
        "sport_key": "soccer_test",
        "commence_time": "2026-08-14T14:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "schedule_revision": 3,
    }
    row["schedule_identity"] = schedule_identity(row)
    return row


def odds_payload(*, books=("book-a", "book-b", "book-c")):
    return {
        "id": "certified-event",
        "sport_key": "soccer_test",
        "commence_time": "2026-08-14T14:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "bookmakers": [
            {
                "key": book,
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-08-14T13:10:00Z",
                        "outcomes": [
                            {"name": "Home", "price": 2.1},
                            {"name": "Draw", "price": 3.2},
                            {"name": "Away", "price": 3.6},
                        ],
                    }
                ],
            }
            for book in books
        ],
    }


def complete_certificate():
    event = event_row()
    required = [f"{book}|h2h" for book in ("book-a", "book-b", "book-c")]
    plan_at = "2026-08-14T13:08:00Z"
    plan_digest = coverage_plan_digest(
        event_key=EVENT_KEY,
        observed_at=plan_at,
        schedule_revision=event["schedule_revision"],
        schedule_identity_value=event["schedule_identity"],
        request_markets=["h2h"],
        required_pairs=required,
        probe_pairs=[],
    )
    batches = coverage_expected_batch_digests(
        plan_digest=plan_digest,
        request_markets=["h2h"],
        expected_pairs=required,
    )
    row = {
        "entity_type": "SOCCER_COVERAGE_CERTIFICATE",
        "certificate_version": COVERAGE_CERTIFICATE_VERSION,
        "event_key": EVENT_KEY,
        "commence_time": event["commence_time"],
        "schedule_revision": event["schedule_revision"],
        "schedule_identity": event["schedule_identity"],
        "plan_version": COVERAGE_PLAN_VERSION,
        "plan_observed_at": plan_at,
        "plan_digest": plan_digest,
        "discovery_observed_at": "2026-08-14T13:07:59Z",
        "discovery_status": "HTTP_200",
        "coverage_error": None,
        "request_markets": ["h2h"],
        "required_pairs": required,
        "probe_pairs": [],
        "expected_digest": digest(required),
        "returned_pairs": required,
        "provider_unavailable_pairs": [],
        "normalization_rejected_pairs": [],
        "attempted_incomplete_pairs": [],
        "quota_deferred_pairs": [],
        "failed_pairs": [],
        "fanout_expected_batch_digests": batches,
        "fanout_enqueued_batch_digests": batches,
        "fanout_succeeded_batch_digests": batches,
        "fanout_failed_batch_digests": [],
        "fanout_deferred_batch_digests": [],
        "fanout_deferred_batch_reasons": {},
        "region_split_conflicts": 0,
        "split_batch_conflicts": 0,
        "summary_revision": 8,
        "updated_at": "2026-08-14T13:10:05Z",
        "completed_at": "2026-08-14T13:10:05Z",
        "immutable": True,
    }
    row["certificate_digest"] = coverage_certificate_digest(row)
    assert coverage_cycle_complete(row)
    return row


def certified_pointer(certificate, payload):
    scope = scope_hash(
        bookmakers=[],
        regions=ALL_BOOKMAKER_REGIONS,
        markets=["h2h"],
    )
    return {
        "PK": EVENT_KEY,
        "SK": (
            "SLOT#2026-08-14T13:10:00Z#REV#3#"
            f"PLAN#{certificate['plan_digest']}#SCOPE#{scope}"
        ),
        "entity_type": "SOCCER_CANONICAL_SNAPSHOT_SLOT",
        "schedule_revision": 3,
        "schedule_identity": event_row()["schedule_identity"],
        "slot_start": "2026-08-14T13:10:00Z",
        "slot_seconds": 60,
        "grace_seconds": 20,
        "scope_hash": scope,
        "observed_at": "2026-08-14T13:10:05Z",
        "payload_sha256": digest(payload),
        "raw_uri": "s3://raw/certified.json",
        "pair_keys_returned": list(certificate["required_pairs"]),
        "coverage_plan_observed_at": certificate["plan_observed_at"],
        "coverage_plan_digest": certificate["plan_digest"],
        "coverage_batch_digest": "batch",
    }


class CohortStore:
    def __init__(self, certificates, slots, payload):
        self.certificates = list(certificates)
        self.slots = list(slots)
        self.payload = payload
        self.slot_queries = []

    def coverage_certificates_before(self, *args, **kwargs):
        return list(self.certificates)

    def canonical_slots_before(
        self,
        event_key,
        cutoff,
        *,
        schedule_revision=None,
        schedule_identity=None,
        coverage_plan_digest=None,
        coverage_plan_observed_at=None,
    ):
        self.slot_queries.append(
            (coverage_plan_digest, coverage_plan_observed_at)
        )
        return [
            row
            for row in self.slots
            if int(row.get("schedule_revision") or 0) == schedule_revision
            and str(row.get("schedule_identity") or "") == schedule_identity
            and str(row.get("coverage_plan_digest") or "")
            == coverage_plan_digest
            and str(row.get("coverage_plan_observed_at") or "")
            == coverage_plan_observed_at
        ]

    def read_json(self, uri):
        return self.payload


class CertifiedLockCohortTests(unittest.TestCase):
    def test_no_certificate_retries_without_persisting_a_lock(self):
        result = build_frozen_lock(
            CohortStore([], [], odds_payload()),
            event_row(),
            observed_at="2026-08-14T13:15:00Z",
        )

        self.assertFalse(result["write_ready"])
        self.assertEqual(
            result["reason"],
            "COMPLETE_PRELOCK_COVERAGE_CERTIFICATE_UNAVAILABLE",
        )

    def test_exact_complete_certificate_builds_plan_bound_v2_lock(self):
        certificate = complete_certificate()
        payload = odds_payload()
        pointer = certified_pointer(certificate, payload)
        store = CohortStore([certificate], [pointer], payload)

        lock = build_frozen_lock(
            store,
            event_row(),
            observed_at="2026-08-14T13:15:00Z",
        )

        self.assertTrue(lock["write_ready"])
        self.assertTrue(lock["prediction_eligible"])
        self.assertTrue(lock["training_eligible"])
        self.assertTrue(live_lock_coverage_provenance_valid(lock))
        self.assertEqual(
            lock["coverage_certificate_digest"],
            certificate["certificate_digest"],
        )
        self.assertEqual(lock["coverage_plan_digest"], certificate["plan_digest"])
        self.assertFalse(lock["movement_baseline_distinct"])
        self.assertEqual(
            lock["movement_baseline_limitation"],
            "NO_DISTINCT_EARLIER_CERTIFIED_PLAN",
        )
        self.assertEqual(
            store.slot_queries,
            [(certificate["plan_digest"], certificate["plan_observed_at"])],
        )

    def test_same_plan_certificates_fall_back_to_exact_latest_baseline(self):
        older = complete_certificate()
        newer = {
            **older,
            "summary_revision": int(older["summary_revision"]) + 1,
            "updated_at": "2026-08-14T13:11:05Z",
            "completed_at": "2026-08-14T13:11:05Z",
        }
        newer["certificate_digest"] = coverage_certificate_digest(newer)
        self.assertTrue(coverage_cycle_complete(newer))
        payload = odds_payload()
        pointer = certified_pointer(newer, payload)
        store = CohortStore([newer, older], [pointer], payload)

        lock = build_frozen_lock(
            store,
            event_row(),
            observed_at="2026-08-14T13:15:00Z",
        )

        self.assertTrue(lock["write_ready"])
        self.assertTrue(live_lock_coverage_provenance_valid(lock))
        self.assertFalse(lock["movement_baseline_distinct"])
        self.assertEqual(
            lock["coverage_certificate_digest"],
            newer["certificate_digest"],
        )
        self.assertEqual(
            lock["movement_baseline_certificate_digest"],
            newer["certificate_digest"],
        )
        self.assertEqual(
            lock["movement_baseline_limitation"],
            "NO_DISTINCT_EARLIER_CERTIFIED_PLAN",
        )
        self.assertEqual(
            store.slot_queries,
            [(newer["plan_digest"], newer["plan_observed_at"])],
        )

    def test_same_plan_certificate_cohorts_are_cached_by_certificate(self):
        base = complete_certificate()
        older = {
            **base,
            "summary_revision": int(base["summary_revision"]) - 1,
            "updated_at": "2026-08-14T13:09:00Z",
            "completed_at": "2026-08-14T13:09:00Z",
        }
        older["certificate_digest"] = coverage_certificate_digest(older)
        newer = {
            **base,
            "summary_revision": int(base["summary_revision"]) + 1,
            "updated_at": "2026-08-14T13:11:05Z",
            "completed_at": "2026-08-14T13:11:05Z",
        }
        newer["certificate_digest"] = coverage_certificate_digest(newer)
        payload = odds_payload()
        pointer = certified_pointer(newer, payload)
        # Deliberately exercise both same-plan identities: the older
        # certificate predates this pointer, while the newer one proves it.
        store = CohortStore([older, newer], [pointer], payload)

        lock = build_frozen_lock(
            store,
            event_row(),
            observed_at="2026-08-14T13:15:00Z",
        )

        self.assertTrue(lock["write_ready"])
        self.assertTrue(live_lock_coverage_provenance_valid(lock))
        self.assertEqual(
            lock["coverage_certificate_digest"],
            newer["certificate_digest"],
        )
        self.assertEqual(
            store.slot_queries,
            [
                (older["plan_digest"], older["plan_observed_at"]),
                (newer["plan_digest"], newer["plan_observed_at"]),
            ],
        )

    def test_missing_required_pair_retries_instead_of_freezing_partial_data(self):
        certificate = complete_certificate()
        payload = odds_payload(books=("book-a", "book-b"))
        pointer = certified_pointer(certificate, payload)
        pointer["pair_keys_returned"] = ["book-a|h2h", "book-b|h2h"]

        result = build_frozen_lock(
            CohortStore([certificate], [pointer], payload),
            event_row(),
            observed_at="2026-08-14T13:15:00Z",
        )

        self.assertFalse(result["write_ready"])
        self.assertEqual(
            result["reason"],
            "NO_FINALIZED_CERTIFIED_PRELOCK_CANONICAL_COHORT",
        )

    def test_tampered_archived_payload_cannot_enter_certified_lock(self):
        certificate = complete_certificate()
        payload = odds_payload()
        pointer = certified_pointer(certificate, payload)
        pointer["payload_sha256"] = "0" * 64

        with self.assertRaisesRegex(
            ValueError,
            "canonical snapshot payload digest mismatch",
        ):
            build_frozen_lock(
                CohortStore([certificate], [pointer], payload),
                event_row(),
                observed_at="2026-08-14T13:15:00Z",
            )

    def test_legacy_or_tampered_live_lock_provenance_is_rejected(self):
        certificate = complete_certificate()
        payload = odds_payload()
        lock = build_frozen_lock(
            CohortStore(
                [certificate],
                [certified_pointer(certificate, payload)],
                payload,
            ),
            event_row(),
            observed_at="2026-08-14T13:15:00Z",
        )

        legacy = {**lock, "lock_version": "soccer-auto-t45-lock-v1"}
        tampered = {**lock, "coverage_plan_digest": "substituted-plan"}

        self.assertFalse(live_lock_coverage_provenance_valid(legacy))
        self.assertFalse(live_lock_coverage_provenance_valid(tampered))

    def test_legacy_pointer_without_plan_provenance_is_excluded(self):
        certificate = complete_certificate()
        payload = odds_payload()
        pointer = certified_pointer(certificate, payload)
        pointer.pop("coverage_plan_digest")
        pointer.pop("coverage_plan_observed_at")

        result = build_frozen_lock(
            CohortStore([certificate], [pointer], payload),
            event_row(),
            observed_at="2026-08-14T13:15:00Z",
        )

        self.assertFalse(result["write_ready"])
        self.assertEqual(
            result["reason"],
            "NO_FINALIZED_CERTIFIED_PRELOCK_CANONICAL_COHORT",
        )


if __name__ == "__main__":
    unittest.main()
