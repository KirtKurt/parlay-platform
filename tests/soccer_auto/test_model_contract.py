from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from soccer_auto.canonical import SnapshotAttempt, choose_canonical_attempt
from soccer_auto.model import TrainingRow, chronological_split, fit_model


class ModelContractTests(unittest.TestCase):
    def _rows(self, count=30):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            TrainingRow(
                event_key=f"event-{index}",
                commence_time=(start + timedelta(hours=index * 3)).isoformat().replace("+00:00", "Z"),
                feature_hash=f"hash-{index}",
                features=(float(index % 4), float(index % 7)),
                market_prior=(0.45, 0.28, 0.27),
                label=index % 3,
                competition="soccer_test",
            )
            for index in range(count)
        ]

    def test_split_is_chronological_and_event_disjoint(self) -> None:
        split = chronological_split(self._rows(), embargo_seconds=0)
        train = {row.event_key for row in split.train}
        validation = {row.event_key for row in split.validation}
        audit = {row.event_key for row in split.audit}
        self.assertFalse(train & validation)
        self.assertFalse(train & audit)
        self.assertFalse(validation & audit)
        self.assertLess(max(row.timestamp for row in split.train), min(row.timestamp for row in split.audit))

    def test_training_is_deterministic(self) -> None:
        rows = self._rows()
        first = fit_model(rows, ("x", "y"), epochs=20).to_dict()["model_digest"]
        second = fit_model(rows, ("x", "y"), epochs=20).to_dict()["model_digest"]
        self.assertEqual(first, second)

    def test_post_start_attempt_cannot_be_canonical(self) -> None:
        attempt = SnapshotAttempt(
            attempt_id="late",
            observed_at="2026-01-01T12:01:00Z",
            commence_time="2026-01-01T12:00:00Z",
            raw_uri="s3://soccer/late",
            payload_sha256="abc",
            bookmaker_count=99,
            market_count=99,
            valid=False,
        )
        self.assertIsNone(
            choose_canonical_attempt(
                [attempt], slot_start="2026-01-01T12:01:00Z", slot_seconds=60
            )
        )


if __name__ == "__main__":
    unittest.main()
