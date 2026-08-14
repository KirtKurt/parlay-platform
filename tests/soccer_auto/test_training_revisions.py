from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

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
    return {
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
        "training_eligible": True,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_hash": "feature-hash",
        "frozen_features": {
            "feature_names": list(FEATURE_NAMES),
            "values": [0.0] * len(FEATURE_NAMES),
            "market_prior": [0.34, 0.33, 0.33],
        },
    }


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
