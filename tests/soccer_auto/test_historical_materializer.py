from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from botocore.exceptions import ClientError  # noqa: E402
from soccer_auto.canonical import digest, iso_utc, parse_utc  # noqa: E402
from soccer_auto.historical import _manifest  # noqa: E402
from soccer_auto.historical_materializer import (  # noqa: E402
    historical_lock_provenance_valid,
    materialization_status,
    run_materialization,
)
from soccer_auto.odds_api import ApiResponse  # noqa: E402
from soccer_auto.settlement import build_settlement  # noqa: E402
from soccer_auto.settlement import settlement_training_evidence_valid  # noqa: E402
from soccer_auto.trainer import training_rows  # noqa: E402


COMMENCE = "2026-08-14T15:00:00Z"
OBSERVED = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)


def conditional_failure() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
        "PutItem",
    )


class Ops:
    def __init__(self, rows=None):
        self.rows = {
            (str(row["PK"]), str(row["SK"])): dict(row) for row in (rows or [])
        }

    def get_item(self, *, Key, **kwargs):
        row = self.rows.get((str(Key["PK"]), str(Key["SK"])))
        return {"Item": dict(row)} if row else {}

    def put_item(self, **kwargs):
        item = dict(kwargs["Item"])
        key = (str(item["PK"]), str(item["SK"]))
        if kwargs.get("ConditionExpression") == "attribute_not_exists(SK)" and key in self.rows:
            raise conditional_failure()
        self.rows[key] = item
        return {}

    def query(self, **kwargs):
        return {"Items": [dict(row) for row in self.rows.values()]}


class Store:
    def __init__(self, settlements=None, conflicts=None, budget=True):
        self.settlements = list(settlements or [])
        self.locks = []
        self.ops = Ops(conflicts)
        self.budget = budget
        self.archives = []
        self.quota = []

    @staticmethod
    def scan_all(table, **kwargs):
        yield from table

    def get_lock(self, event_key, target="result_1x2", *, schedule_revision=None):
        expected = f"LOCK#T45#REV#{int(schedule_revision)}#TARGET#{target}"
        return next(
            (
                dict(row)
                for row in self.locks
                if row["PK"] == event_key and row["SK"] == expected
            ),
            None,
        )

    def put_lock(self, item):
        if any(
            row["PK"] == item["PK"] and row["SK"] == item["SK"]
            for row in self.locks
        ):
            return False
        self.locks.append(dict(item))
        return True

    def provider_budget_available(self, *args, **kwargs):
        return self.budget

    def record_quota(self, *args, **kwargs):
        self.quota.append((args, kwargs))

    def archive_json(self, category, payload, **kwargs):
        uri = f"s3://raw/{category}/{len(self.archives)}.json"
        self.archives.append((category, payload, kwargs, uri))
        return uri, digest(payload)


def settlement():
    return build_settlement(
        {
            "id": "event-1",
            "sport_key": "soccer_epl",
            "schedule_revision": 3,
            "commence_time": COMMENCE,
            "completed": True,
            "home_team": "Home",
            "away_team": "Away",
            "scores": [
                {"name": "Home", "score": "2"},
                {"name": "Away", "score": "1"},
            ],
        },
        observed_at=iso_utc(OBSERVED),
        regulation_ambiguous=False,
    )


def ambiguous_settlement():
    return build_settlement(
        {
            "id": "cup-event",
            "sport_key": "soccer_fa_cup",
            "schedule_revision": 1,
            "commence_time": COMMENCE,
            "completed": True,
            "home_team": "Home",
            "away_team": "Away",
            "scores": [
                {"name": "Home", "score": "2"},
                {"name": "Away", "score": "1"},
            ],
        },
        observed_at=iso_utc(OBSERVED),
        regulation_ambiguous=True,
    )


def odds_event(*, home="Home", away="Away", book_count=3):
    return {
        "id": "event-1",
        "sport_key": "soccer_epl",
        "commence_time": COMMENCE,
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": f"book-{index}",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": home, "price": 2.10 + index / 100},
                            {"name": "Draw", "price": 3.20 + index / 100},
                            {"name": away, "price": 3.60 + index / 100},
                        ],
                    }
                ],
            }
            for index in range(book_count)
        ],
    }


class Client:
    def __init__(self, *, event=None, provider_at=None):
        self.event = event if event is not None else odds_event()
        self.provider_at = provider_at
        self.calls = []

    def historical_odds(self, sport_key, snapshot_at, markets, **kwargs):
        self.calls.append((sport_key, snapshot_at, tuple(markets), kwargs))
        provider_at = self.provider_at or snapshot_at
        return ApiResponse(
            data={
                "timestamp": provider_at,
                "next_timestamp": iso_utc(parse_utc(snapshot_at) + timedelta(minutes=5)),
                "data": [self.event] if self.event else [],
            },
            status=200,
            request_url="https://example.test/historical/soccer",
        )


class HistoricalMaterializerTests(unittest.TestCase):
    def run_once(self, store, client):
        with patch(
            "soccer_auto.historical_materializer._client", return_value=client
        ), patch(
            "soccer_auto.historical_materializer.now_utc", return_value=OBSERVED
        ):
            return run_materialization(store, max_events=5)

    def test_no_authoritative_settlement_never_calls_provider(self):
        store = Store()
        client = Client()
        result = self.run_once(store, client)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(store.locks, [])

    def test_unsigned_eligibility_flip_cannot_admit_ambiguous_result(self):
        final = ambiguous_settlement()
        final["training_eligible_1x2"] = True
        final["training_eligible_score_derived"] = True
        with patch.dict(
            "os.environ",
            {"SOCCER_AUTO_ALLOW_UNVERIFIED_KNOCKOUT_LABELS": "false"},
        ):
            self.assertFalse(settlement_training_evidence_valid(final))

    def test_digest_valid_ambiguous_result_is_not_materialized_or_counted(self):
        with patch.dict(
            "os.environ",
            {"SOCCER_AUTO_ALLOW_UNVERIFIED_KNOCKOUT_LABELS": "false"},
        ):
            final = ambiguous_settlement()
            self.assertTrue(settlement_training_evidence_valid(final))
            self.assertFalse(final["training_eligible_1x2"])
            store = Store([final])
            client = Client()
            result = self.run_once(store, client)
            status = materialization_status(store)

        self.assertEqual(result["authoritative_settlements"], 0)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(store.locks, [])
        self.assertEqual(status["authoritative_settlements"], 0)
        self.assertEqual(status["validated_settlements"], 1)
        self.assertEqual(status["ineligible_validated_settlements"], 1)
        self.assertEqual(status["historical_training_rows"], 0)

    def test_previously_materialized_ambiguous_lock_remains_quarantined(self):
        with patch.dict(
            "os.environ",
            {"SOCCER_AUTO_ALLOW_UNVERIFIED_KNOCKOUT_LABELS": "false"},
        ):
            final = ambiguous_settlement()
            historical_event = odds_event()
            historical_event.update(
                {"id": "cup-event", "sport_key": "soccer_fa_cup"}
            )
            store = Store([final])
            client = Client(event=historical_event)
            # Reproduce the prior bug's persisted lock and terminal state.
            with patch(
                "soccer_auto.historical_materializer._authoritative_settlements",
                return_value=[final],
            ):
                result = self.run_once(store, client)
            status = materialization_status(store)
            rows, excluded = training_rows(store)

        self.assertEqual(result["materialized"], 1)
        self.assertEqual(len(store.locks), 1)
        self.assertEqual(
            next(
                row
                for row in store.ops.rows.values()
                if row.get("entity_type")
                == "SOCCER_HISTORICAL_T45_MATERIALIZATION_STATE"
            )["status"],
            "MATERIALIZED_ELIGIBLE",
        )
        self.assertFalse(historical_lock_provenance_valid(store.locks[0], final))
        self.assertEqual(status["materialized_rows"], 0)
        self.assertEqual(status["historical_training_rows"], 0)
        self.assertEqual(status["pending_authoritative_settlements"], 0)
        self.assertEqual(rows, [])
        self.assertEqual(excluded["settlement_ineligible"], 1)

    def test_exact_t45_materialization_is_trainable_but_never_predictable(self):
        final = settlement()
        store = Store([final])
        client = Client()
        result = self.run_once(store, client)

        expected_lock_at = iso_utc(parse_utc(COMMENCE) - timedelta(minutes=45))
        self.assertEqual(result["materialized"], 1)
        self.assertEqual(client.calls[0][1], expected_lock_at)
        self.assertEqual(len(store.locks), 1)
        lock = store.locks[0]
        self.assertTrue(lock["historical_materialization"])
        self.assertTrue(lock["training_eligible"])
        self.assertFalse(lock["prediction_eligible"])
        self.assertIsNone(lock["labels"])
        self.assertTrue(lock["source_provider_before_lock"])
        self.assertFalse(lock["source_observed_before_lock"])
        self.assertEqual(lock["source_settlement_digest"], final["settlement_digest"])
        self.assertLessEqual(
            parse_utc(lock["source_provider_at_max"]), parse_utc(lock["lock_at"])
        )
        rows, excluded = training_rows(store)
        self.assertEqual(len(rows), 1)
        self.assertEqual(excluded["historical_provenance"], 0)

    def test_legacy_settlement_digest_gets_derived_schedule_identity(self):
        final = settlement()
        final.pop("schedule_identity")
        final["settlement_digest"] = digest(
            {
                key: final[key]
                for key in (
                    "event_key",
                    "schedule_revision",
                    "commence_time",
                    "sport_key",
                    "home_team",
                    "away_team",
                    "home_score",
                    "away_score",
                    "result_1x2",
                    "settlement_semantics",
                )
            }
        )
        store = Store([final])

        result = self.run_once(store, Client())
        rows, excluded = training_rows(store)

        self.assertEqual(result["materialized"], 1)
        self.assertTrue(store.locks[0]["schedule_identity"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(excluded["historical_provenance"], 0)

    def test_retry_is_idempotent_and_does_not_recall_provider(self):
        store = Store([settlement()])
        client = Client()
        first = self.run_once(store, client)
        second = self.run_once(store, client)
        self.assertEqual(first["materialized"], 1)
        self.assertEqual(second["existing_locks"], 1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(store.locks), 1)

    def test_provider_repaint_is_quarantined_before_lock_write(self):
        final = settlement()
        store = Store([final])
        lock_at = iso_utc(parse_utc(COMMENCE) - timedelta(minutes=45))
        _manifest(
            store,
            mode="SUPERVISED_T45",
            sport_key="soccer_epl",
            requested_at=lock_at,
            provider_at=lock_at,
            raw_uri="s3://raw/first.json",
            payload_hash="first-payload",
            event_id="event-1",
            markets=["h2h", "spreads", "totals"],
        )

        result = self.run_once(store, Client())

        self.assertEqual(store.locks, [])
        self.assertEqual(result["failures"][0]["reason"], "QUARANTINED_REPAINT")
        self.assertTrue(
            any(row.get("entity_type") == "SOCCER_HISTORICAL_RAW_CONFLICT" for row in store.ops.rows.values())
        )

    def test_post_t45_provider_snapshot_is_quarantined(self):
        lock_at = parse_utc(COMMENCE) - timedelta(minutes=45)
        store = Store([settlement()])
        client = Client(provider_at=iso_utc(lock_at + timedelta(minutes=1)))
        result = self.run_once(store, client)
        self.assertEqual(store.locks, [])
        self.assertEqual(result["failures"][0]["reason"], "QUARANTINED_SNAPSHOT_TIME")

    def test_post_t45_nested_market_update_is_quarantined(self):
        event = odds_event()
        event["bookmakers"][0]["markets"][0]["last_update"] = iso_utc(
            parse_utc(COMMENCE) - timedelta(minutes=44)
        )
        store = Store([settlement()])
        result = self.run_once(store, Client(event=event))
        self.assertEqual(store.locks, [])
        self.assertEqual(
            result["failures"][0]["reason"], "QUARANTINED_SNAPSHOT_TIME"
        )

    def test_schedule_identity_mismatch_is_quarantined(self):
        store = Store([settlement()])
        client = Client(event=odds_event(home="Different Home"))
        result = self.run_once(store, client)
        self.assertEqual(store.locks, [])
        self.assertEqual(
            result["failures"][0]["reason"], "QUARANTINED_IDENTITY_MISMATCH"
        )

    def test_incomplete_bookmaker_coverage_never_writes_lock(self):
        store = Store([settlement()])
        client = Client(event=odds_event(book_count=2))
        result = self.run_once(store, client)
        self.assertEqual(store.locks, [])
        self.assertEqual(
            result["failures"][0]["reason"], "INELIGIBLE_MARKET_COVERAGE"
        )

    def test_settlement_conflict_blocks_materialization_before_provider(self):
        final = settlement()
        conflict = {
            "PK": "SETTLEMENT_CONFLICT",
            "SK": "event-1#conflict",
            "event_key": final["event_key"],
            "training_blocked": True,
        }
        store = Store([final], conflicts=[conflict])
        client = Client()
        result = self.run_once(store, client)
        self.assertEqual(result["conflict_skips"], 1)
        self.assertEqual(client.calls, [])
        self.assertEqual(store.locks, [])

    def test_tampered_historical_provenance_is_excluded_by_trainer(self):
        store = Store([settlement()])
        self.run_once(store, Client())
        store.locks[0]["source_provider_at_max"] = COMMENCE
        rows, excluded = training_rows(store)
        self.assertEqual(rows, [])
        self.assertEqual(excluded["historical_provenance"], 1)

    def test_removed_historical_marker_cannot_bypass_provenance(self):
        store = Store([settlement()])
        self.run_once(store, Client())
        store.locks[0].pop("historical_materialization")
        rows, excluded = training_rows(store)
        self.assertEqual(rows, [])
        self.assertEqual(excluded["historical_provenance"], 1)

    def test_tampered_feature_digest_is_excluded_by_trainer(self):
        store = Store([settlement()])
        self.run_once(store, Client())
        store.locks[0]["frozen_features"]["values"][0] += 0.01
        rows, excluded = training_rows(store)
        self.assertEqual(rows, [])
        self.assertEqual(excluded["historical_provenance"], 1)

    def test_tampered_lock_identity_field_is_excluded_by_trainer(self):
        store = Store([settlement()])
        self.run_once(store, Client())
        store.locks[0]["sport_key"] = "soccer_spain_la_liga"
        rows, excluded = training_rows(store)
        self.assertEqual(rows, [])
        self.assertEqual(excluded["historical_provenance"], 1)

    def test_status_counts_computed_training_rows(self):
        store = Store([settlement()])
        self.run_once(store, Client())
        status = materialization_status(store)
        self.assertEqual(status["authoritative_settlements"], 1)
        self.assertEqual(status["materialized_rows"], 1)
        self.assertEqual(status["historical_training_rows"], 1)
        self.assertEqual(status["pending_authoritative_settlements"], 0)

    def test_late_settlement_conflict_removes_row_from_status_and_training(self):
        final = settlement()
        store = Store([final])
        self.run_once(store, Client())
        conflict = {
            "PK": "SETTLEMENT_CONFLICT",
            "SK": "event-1#late-conflict",
            "event_key": final["event_key"],
            "training_blocked": True,
        }
        store.ops.put_item(Item=conflict)

        status = materialization_status(store)
        rows, excluded = training_rows(store)

        self.assertEqual(status["historical_training_rows"], 0)
        self.assertEqual(rows, [])
        self.assertEqual(excluded["settlement_conflict"], 1)


if __name__ == "__main__":
    unittest.main()
