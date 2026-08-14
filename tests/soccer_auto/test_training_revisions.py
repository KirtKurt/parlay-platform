from __future__ import annotations

import unittest
from datetime import timedelta
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.canonical import digest, iso_utc, parse_utc  # noqa: E402
from soccer_auto.market_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION  # noqa: E402
from soccer_auto.settlement import build_settlement  # noqa: E402
from soccer_auto.trainer import evaluate_prospective_candidate, training_rows  # noqa: E402


EVENT_KEY = "EVENT#soccer_test#event-id"
COMMENCE = "2026-08-14T14:00:00Z"


class ScanStore:
    def __init__(self, locks, settlements, conflicts=None):
        self.locks = locks
        self.settlements = settlements
        self.ops = conflicts or []

    def scan_all(self, table, **kwargs):
        yield from table


class PredictionTable:
    def __init__(self, rows):
        self.rows = rows

    def query(self, **kwargs):
        return {"Items": list(self.rows)}


class ProspectiveStore(ScanStore):
    def __init__(self, settlements, predictions, conflicts=None):
        super().__init__([], settlements, conflicts)
        self.predictions = PredictionTable(predictions)

    def model_items(self, *args, **kwargs):
        return []


def lock_row(revision: int):
    final = settlement_row(revision)
    lock_at = iso_utc(parse_utc(COMMENCE) - timedelta(minutes=45))
    source_at = iso_utc(parse_utc(lock_at) - timedelta(minutes=1))
    plan_at = iso_utc(parse_utc(lock_at) - timedelta(minutes=2))
    features = {
        "feature_names": list(FEATURE_NAMES),
        "values": [0.0] * len(FEATURE_NAMES),
        "market_prior": [0.34, 0.33, 0.33],
    }
    required_pairs = ["book|h2h"]
    source_hashes = [digest({"revision": revision})]
    certificate_digest = digest({"certificate": revision})
    plan_digest = digest({"plan": revision})
    lock = {
        "PK": EVENT_KEY,
        "SK": f"LOCK#T45#REV#{revision}#TARGET#result_1x2",
        "entity_type": "SOCCER_FROZEN_FEATURE_LOCK",
        "event_key": EVENT_KEY,
        "event_id": "event-id",
        "sport_key": "soccer_test",
        "commence_time": COMMENCE,
        "schedule_revision": revision,
        "schedule_identity": final["schedule_identity"],
        "home_team": "Home",
        "away_team": "Away",
        "target": "result_1x2",
        "lock_version": "soccer-auto-t45-lock-v2",
        "lock_at": lock_at,
        "created_at": lock_at,
        "labels": None,
        "immutable": True,
        "training_eligible": True,
        "prediction_eligible": True,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "frozen_features": features,
        "coverage_certificate_version": (
            "soccer-auto-coverage-certificate-v2"
        ),
        "coverage_certificate_digest": certificate_digest,
        "coverage_plan_digest": plan_digest,
        "coverage_plan_observed_at": plan_at,
        "coverage_completed_at": source_at,
        "coverage_required_pairs": required_pairs,
        "coverage_required_pair_count": 1,
        "coverage_required_pair_digest": digest(required_pairs),
        "coverage_probe_pairs": [],
        "coverage_probe_pair_count": 0,
        "coverage_probe_pair_digest": digest([]),
        "coverage_completed_before_lock": True,
        "movement_baseline_certificate_digest": certificate_digest,
        "movement_baseline_plan_digest": plan_digest,
        "movement_baseline_plan_observed_at": plan_at,
        "movement_baseline_distinct": False,
        "source_slot_ids": ["slot"],
        "source_payload_hashes": source_hashes,
        "source_raw_uris": ["s3://raw/live.json"],
        "source_observed_at_max": source_at,
        "source_observed_before_lock": True,
        "movement_baseline_source_slot_ids": ["slot"],
        "movement_baseline_source_payload_hashes": source_hashes,
        "movement_baseline_source_raw_uris": ["s3://raw/live.json"],
        "movement_baseline_source_observed_at_max": source_at,
        "movement_baseline_source_observed_before_lock": True,
    }
    lock["feature_hash"] = digest(
        {
            "event_key": lock["event_key"],
            "schedule_revision": revision,
            "lock_at": lock_at,
            "coverage_certificate_digest": certificate_digest,
            "coverage_plan_digest": plan_digest,
            "coverage_plan_observed_at": plan_at,
            "coverage_completed_at": source_at,
            "coverage_required_pairs": required_pairs,
            "coverage_probe_pairs": [],
            "movement_baseline_certificate_digest": certificate_digest,
            "movement_baseline_plan_digest": plan_digest,
            "movement_baseline_plan_observed_at": plan_at,
            "source_slot_ids": lock["source_slot_ids"],
            "source_hashes": source_hashes,
            "source_raw_uris": lock["source_raw_uris"],
            "source_observed_at_max": source_at,
            "movement_baseline_source_slot_ids": lock[
                "movement_baseline_source_slot_ids"
            ],
            "movement_baseline_source_hashes": source_hashes,
            "movement_baseline_source_raw_uris": lock[
                "movement_baseline_source_raw_uris"
            ],
            "movement_baseline_source_observed_at_max": source_at,
            "features": features,
        }
    )
    return lock


def settlement_row(revision: int):
    return build_settlement(
        {
            "id": "event-id",
            "sport_key": "soccer_test",
            "commence_time": COMMENCE,
            "schedule_revision": revision,
            "home_team": "Home",
            "away_team": "Away",
            "completed": True,
            "scores": [
                {"name": "Home", "score": "2"},
                {"name": "Away", "score": "1"},
            ],
        },
        observed_at="2026-08-14T16:00:00Z",
        regulation_ambiguous=False,
    )


class TrainingRevisionTests(unittest.TestCase):
    def test_stale_schedule_revision_is_excluded_from_training(self):
        rows, excluded = training_rows(ScanStore([lock_row(2)], [settlement_row(3)]))
        self.assertEqual(rows, [])
        self.assertEqual(excluded["schedule_mismatch"], 1)

    def test_exact_schedule_revision_remains_trainable(self):
        rows, excluded = training_rows(ScanStore([lock_row(3)], [settlement_row(3)]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(excluded["schedule_mismatch"], 0)

    def test_settlement_conflict_quarantines_existing_label_from_training(self):
        conflicts = [
            {
                "PK": "SETTLEMENT_CONFLICT",
                "event_key": EVENT_KEY,
                "training_blocked": True,
            }
        ]
        rows, excluded = training_rows(
            ScanStore([lock_row(3)], [settlement_row(3)], conflicts)
        )
        self.assertEqual(rows, [])
        self.assertEqual(excluded["settlement_conflict"], 1)

    def test_unsigned_live_eligibility_flip_is_rejected(self):
        final = build_settlement(
            {
                "id": "event-id",
                "sport_key": "soccer_test",
                "commence_time": COMMENCE,
                "schedule_revision": 3,
                "home_team": "Home",
                "away_team": "Away",
                "completed": True,
                "scores": [
                    {"name": "Home", "score": "2"},
                    {"name": "Away", "score": "1"},
                ],
            },
            observed_at="2026-08-14T16:00:00Z",
            regulation_ambiguous=True,
        )
        final["training_eligible_1x2"] = True
        final["training_eligible_score_derived"] = True
        with patch.dict(
            "os.environ",
            {"SOCCER_AUTO_ALLOW_UNVERIFIED_KNOCKOUT_LABELS": "false"},
        ):
            rows, excluded = training_rows(ScanStore([lock_row(3)], [final]))
        self.assertEqual(rows, [])
        self.assertEqual(excluded["settlement_ineligible"], 1)

    def test_settlement_conflict_is_excluded_from_prospective_promotion_gate(self):
        settlement = settlement_row(3)
        prediction = {
            "event_key": EVENT_KEY,
            "commence_time": COMMENCE,
            "schedule_revision": 3,
            "target": "result_1x2",
            "probabilities": {"home": 0.60, "draw": 0.20, "away": 0.20},
            "market_prior": {"home": 0.40, "draw": 0.30, "away": 0.30},
        }
        conflicts = [
            {
                "PK": "SETTLEMENT_CONFLICT",
                "event_key": EVENT_KEY,
                "training_blocked": True,
            }
        ]
        metrics = evaluate_prospective_candidate(
            ProspectiveStore([settlement], [prediction], conflicts),
            {"model_digest": "candidate"},
        )
        self.assertEqual(metrics["count"], 0)

    def test_settlement_identity_and_digest_include_schedule_revision(self):
        base = {
            "id": "event-id",
            "sport_key": "soccer_test",
            "commence_time": COMMENCE,
            "home_team": "Home",
            "away_team": "Away",
            "completed": True,
            "scores": [
                {"name": "Home", "score": "2"},
                {"name": "Away", "score": "1"},
            ],
        }
        first = build_settlement(
            {**base, "schedule_revision": 3}, observed_at="2026-08-14T16:00:00Z"
        )
        revised = build_settlement(
            {**base, "schedule_revision": 4}, observed_at="2026-08-14T16:00:00Z"
        )
        self.assertEqual(first["schedule_revision"], 3)
        self.assertNotEqual(first["settlement_digest"], revised["settlement_digest"])

    def test_completed_score_before_kickoff_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "before scheduled kickoff"):
            build_settlement(
                {
                    "id": "event-id",
                    "sport_key": "soccer_test",
                    "commence_time": COMMENCE,
                    "home_team": "Home",
                    "away_team": "Away",
                    "completed": True,
                    "schedule_revision": 3,
                    "scores": [
                        {"name": "Home", "score": "2"},
                        {"name": "Away", "score": "1"},
                    ],
                },
                observed_at="2026-08-14T13:59:59Z",
            )


if __name__ == "__main__":
    unittest.main()
