from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from botocore.exceptions import ClientError  # noqa: E402
from soccer_auto.canonical import digest, iso_utc, parse_utc  # noqa: E402
from soccer_auto.historical import (  # noqa: E402
    MAX_CALLS_PER_INVOCATION,
    HistoricalCursorConflict,
    HistoricalTimestampError,
    HistoricalWrapperSchemaError,
    _cursor,
    _cursor_start,
    _manifest,
    _provider_timestamps,
    _save_cursor,
    _validated_wrapper,
    historical_handler,
    run_additional,
    run_featured,
)
from soccer_auto.odds_api import ApiResponse  # noqa: E402


def conditional_failure() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "race"}},
        "PutItem",
    )


class Ops:
    def __init__(self, rows=None):
        self.rows = {
            (str(row["PK"]), str(row["SK"])): dict(row)
            for row in (rows or [])
        }
        self.writes = []

    def get_item(self, *, Key, **kwargs):
        row = self.rows.get((str(Key["PK"]), str(Key["SK"])))
        return {"Item": dict(row)} if row else {}

    def put_item(self, **kwargs):
        item = dict(kwargs["Item"])
        key = (str(item["PK"]), str(item["SK"]))
        current = self.rows.get(key)
        condition = kwargs.get("ConditionExpression")
        values = kwargs.get("ExpressionAttributeValues") or {}
        if condition == "attribute_not_exists(SK)" and current is not None:
            raise conditional_failure()
        if condition == "attribute_not_exists(revision)" and current and "revision" in current:
            raise conditional_failure()
        if condition == "revision=:expected" and (
            not current or int(current.get("revision") or 0) != int(values[":expected"])
        ):
            raise conditional_failure()
        self.rows[key] = item
        self.writes.append(item)

    def query(self, **kwargs):
        return {
            "Items": [
                dict(row)
                for (pk, _), row in self.rows.items()
                if pk == "HISTORICAL_CURSOR"
            ]
        }


class Store:
    def __init__(self, rows=None, competitions=None, budget=None):
        self.ops = Ops(rows)
        self.settlements = []
        self.locks = []
        self.competitions = list(
            competitions
            or [{"sport_key": "soccer_future_league", "has_outrights": False}]
        )
        self.budget = list(budget or [])
        self.budget_checks = []
        self.quota_observations = []
        self.archives = []

    @staticmethod
    def scan_all(table, **kwargs):
        yield from table

    def list_competitions(self):
        return list(self.competitions)

    def provider_budget_available(self, *args, **kwargs):
        self.budget_checks.append((args, kwargs))
        return self.budget.pop(0) if self.budget else True

    def record_quota(self, *args, **kwargs):
        self.quota_observations.append((args, kwargs))

    def archive_json(self, category, payload, **kwargs):
        uri = f"s3://raw/{category}/{len(self.archives)}.json"
        self.archives.append(
            {"category": category, "payload": payload, "kwargs": kwargs, "uri": uri}
        )
        return uri, digest(payload)


class Client:
    def __init__(self):
        self.featured_calls = []
        self.events_calls = []
        self.event_odds_calls = []

    def historical_odds(self, sport_key, requested_at, *args, **kwargs):
        self.featured_calls.append((sport_key, requested_at, args, kwargs))
        next_at = iso_utc(parse_utc(requested_at) + timedelta(minutes=10))
        return ApiResponse(
            data={
                "timestamp": requested_at,
                "next_timestamp": next_at,
                "data": [],
            },
            status=200,
            request_url="https://example.test/history/featured",
        )

    def historical_events(self, sport_key, requested_at):
        self.events_calls.append((sport_key, requested_at))
        return ApiResponse(
            data={
                "timestamp": requested_at,
                "next_timestamp": iso_utc(
                    parse_utc(requested_at) + timedelta(minutes=5)
                ),
                "data": [],
            },
            status=200,
            request_url="https://example.test/history/events",
        )

    def historical_event_odds(
        self, sport_key, event_id, provider_at, markets, **kwargs
    ):
        self.event_odds_calls.append(
            {
                "sport_key": sport_key,
                "event_id": event_id,
                "provider_at": provider_at,
                "markets": list(markets),
                "kwargs": kwargs,
            }
        )
        return ApiResponse(
            data={
                "timestamp": provider_at,
                "data": {
                    "id": event_id,
                    "sport_key": sport_key,
                    "commence_time": "2026-01-02T00:00:00Z",
                    "home_team": "Home",
                    "away_team": "Away",
                    "bookmakers": [],
                },
            },
            status=200,
            request_url="https://example.test/history/event-odds",
        )


def cursor_row(
    mode: str,
    sport_key: str,
    snapshot_at: str,
    **extra,
):
    return {
        "PK": "HISTORICAL_CURSOR",
        "SK": f"{mode}#{sport_key}",
        "entity_type": "SOCCER_HISTORICAL_BACKFILL_CURSOR",
        "mode": mode,
        "sport_key": sport_key,
        "snapshot_at": snapshot_at,
        "status": "RUNNING",
        "calls_completed": 0,
        "revision": 1,
        **extra,
    }


class HistoricalCursorTests(unittest.TestCase):
    def test_additional_resume_uses_persisted_market_plan_not_new_seeds(self) -> None:
        market_plan = [["market_a"], ["market_b"], ["market_c"]]
        cursor = cursor_row(
            "ADDITIONAL",
            "soccer_future_league",
            "2026-01-01T00:00:00Z",
            pending_provider_at="2026-01-01T00:00:00Z",
            pending_requested_at="2026-01-01T00:00:00Z",
            pending_next_timestamp="2026-01-01T00:05:00Z",
            pending_events=[{"id": "event-1"}],
            pending_event_index=0,
            pending_market_index=0,
            pending_market_plan=market_plan,
            pending_market_plan_digest=digest(market_plan),
        )
        store = Store([cursor], budget=[True, False])
        client = Client()
        with patch("soccer_auto.historical._client", return_value=client), patch(
            "soccer_auto.historical._market_keys_for_sport",
            return_value=["new_market_that_must_not_replace_the_plan"],
        ):
            result = run_additional(store)
        self.assertTrue(result["deferred"])
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["cursor"]["pending_event_index"], 0)
        self.assertEqual(result["cursor"]["pending_market_index"], 1)
        self.assertEqual(result["cursor"]["calls_completed"], 1)
        self.assertEqual(client.event_odds_calls[0]["markets"], ["market_a"])
        self.assertEqual(result["cursor"]["pending_market_plan"], market_plan)

    def test_completed_featured_cursor_never_wraps_to_start_or_calls_provider_again(self) -> None:
        now = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        sport_key = "soccer_done"
        store = Store(
            [cursor_row("FEATURED", sport_key, iso_utc(now))],
            competitions=[{"sport_key": sport_key, "has_outrights": False}],
        )
        client = Client()
        with patch("soccer_auto.historical._client", return_value=client), patch(
            "soccer_auto.historical.now_utc", return_value=now
        ):
            first = run_featured(store, max_calls=1)
            second = run_featured(store, max_calls=1)
        persisted = store.ops.rows[("HISTORICAL_CURSOR", f"FEATURED#{sport_key}")]
        self.assertEqual(first["completed_this_cycle"], 1)
        self.assertEqual(second["calls"], 0)
        self.assertEqual(persisted["status"], "COMPLETE")
        self.assertEqual(persisted["snapshot_at"], iso_utc(now))
        self.assertEqual(client.featured_calls, [])

    def test_completed_additional_cursor_never_wraps_or_creates_provider_client(self) -> None:
        now = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        sport_key = "soccer_done"
        store = Store(
            [cursor_row("ADDITIONAL", sport_key, iso_utc(now))],
            competitions=[{"sport_key": sport_key, "has_outrights": False}],
        )
        with patch(
            "soccer_auto.historical._client",
            side_effect=AssertionError("a completed cursor cannot call the provider"),
        ), patch("soccer_auto.historical.now_utc", return_value=now):
            first = run_additional(store, max_calls=1)
            second = run_additional(store, max_calls=1)
        persisted = store.ops.rows[("HISTORICAL_CURSOR", f"ADDITIONAL#{sport_key}")]
        self.assertEqual(first["reason"], "COMPLETE")
        self.assertEqual(second["reason"], "COMPLETE")
        self.assertEqual(persisted["status"], "COMPLETE")
        self.assertEqual(persisted["snapshot_at"], iso_utc(now))

    def test_compare_and_swap_prevents_stale_cursor_from_moving_backward(self) -> None:
        sport_key = "soccer_one"
        store = Store(
            [cursor_row("FEATURED", sport_key, "2026-01-01T00:00:00Z")]
        )
        first = _cursor(
            store, "FEATURED", sport_key, "2020-06-06T00:00:00Z"
        )
        stale = _cursor(
            store, "FEATURED", sport_key, "2020-06-06T00:00:00Z"
        )
        first["snapshot_at"] = "2026-01-01T00:10:00Z"
        _save_cursor(store, first)
        stale["snapshot_at"] = "2026-01-01T00:05:00Z"
        with self.assertRaises(HistoricalCursorConflict):
            _save_cursor(store, stale)
        self.assertEqual(
            store.ops.rows[("HISTORICAL_CURSOR", f"FEATURED#{sport_key}")][
                "snapshot_at"
            ],
            "2026-01-01T00:10:00Z",
        )

    def test_new_catalog_key_gets_own_start_cursor_without_reusing_existing_sport(self) -> None:
        existing_key = "soccer_z_existing"
        new_key = "soccer_a_new"
        existing_at = "2025-01-01T00:00:00Z"
        store = Store(
            [
                cursor_row(
                    "FEATURED",
                    existing_key,
                    existing_at,
                    last_error="stale provider error",
                    last_error_at="2025-01-01T00:00:00Z",
                )
            ],
            competitions=[{"sport_key": existing_key, "has_outrights": False}],
        )
        client = Client()
        with patch("soccer_auto.historical._client", return_value=client):
            run_featured(store, max_calls=1)
            existing_after_first = dict(
                store.ops.rows[("HISTORICAL_CURSOR", f"FEATURED#{existing_key}")]
            )
            store.competitions.insert(
                0, {"sport_key": new_key, "has_outrights": False}
            )
            run_featured(store, max_calls=1)
        self.assertEqual(client.featured_calls[0][0], existing_key)
        self.assertEqual(client.featured_calls[0][1], existing_at)
        self.assertEqual(client.featured_calls[1][0], new_key)
        self.assertEqual(client.featured_calls[1][1], "2020-06-06T10:05:00Z")
        self.assertEqual(
            store.ops.rows[("HISTORICAL_CURSOR", f"FEATURED#{existing_key}")][
                "snapshot_at"
            ],
            existing_after_first["snapshot_at"],
        )
        self.assertNotIn("last_error", existing_after_first)
        self.assertNotIn("last_error_at", existing_after_first)

    def test_epl_zero_call_prestart_schema_quarantine_migrates_to_official_start(self) -> None:
        sport_key = "soccer_epl"
        store = Store(
            [
                cursor_row(
                    "FEATURED",
                    sport_key,
                    "2020-06-06T00:00:00Z",
                    status="QUARANTINED_PROVIDER_SCHEMA",
                    calls_completed=0,
                    last_error="historical_featured HTTP-200 wrapper is missing timestamp",
                    last_error_at="2026-08-14T03:36:49Z",
                )
            ],
            competitions=[{"sport_key": sport_key, "has_outrights": False}],
        )
        client = Client()

        with patch("soccer_auto.historical._client", return_value=client):
            result = run_featured(store, max_calls=1)

        migration = store.ops.writes[0]
        persisted = store.ops.rows[("HISTORICAL_CURSOR", f"FEATURED#{sport_key}")]
        self.assertEqual(migration["status"], "PENDING")
        self.assertEqual(migration["snapshot_at"], "2020-06-06T10:05:00Z")
        self.assertEqual(
            migration["prestart_schema_quarantine_from"],
            "2020-06-06T00:00:00Z",
        )
        self.assertEqual(migration["recovery_reason"], "OFFICIAL_SPORT_START_CORRECTION")
        self.assertNotIn("last_error", migration)
        self.assertNotIn("last_error_at", migration)
        self.assertEqual(client.featured_calls[0][1], "2020-06-06T10:05:00Z")
        self.assertEqual(result["calls"], 1)
        self.assertEqual(persisted["calls_completed"], 1)
        self.assertEqual(persisted["status"], "RUNNING")
        self.assertNotIn("last_error", persisted)
        self.assertNotIn("last_error_at", persisted)

    def test_later_sport_starts_at_its_2026_snapshot_for_both_modes(self) -> None:
        sport_key = "soccer_france_coupe_de_france"
        official_start = "2026-02-26T13:35:37Z"
        self.assertEqual(
            _cursor_start("FEATURED", sport_key, "2020-06-06T10:05:00Z"),
            (official_start, True),
        )
        self.assertEqual(
            _cursor_start("ADDITIONAL", sport_key, "2023-05-03T05:30:00Z"),
            (official_start, True),
        )
        self.assertEqual(
            _cursor_start("ADDITIONAL", "soccer_epl", "2023-05-03T05:30:00Z"),
            ("2023-05-03T05:30:00Z", True),
        )

        competition = [{"sport_key": sport_key, "has_outrights": False}]
        observed = datetime(2026, 8, 14, tzinfo=timezone.utc)
        featured_store = Store(competitions=competition)
        featured_client = Client()
        with patch(
            "soccer_auto.historical._client", return_value=featured_client
        ), patch("soccer_auto.historical.now_utc", return_value=observed):
            run_featured(featured_store, max_calls=1)
        self.assertEqual(featured_client.featured_calls[0][1], official_start)

        additional_store = Store(competitions=competition, budget=[False])
        with patch("soccer_auto.historical.now_utc", return_value=observed):
            result = run_additional(additional_store, max_calls=1)
        persisted = additional_store.ops.rows[
            ("HISTORICAL_CURSOR", f"ADDITIONAL#{sport_key}")
        ]
        self.assertTrue(result["deferred"])
        self.assertEqual(persisted["snapshot_at"], official_start)

    def test_schema_quarantine_migration_does_not_bypass_real_malformed_response(self) -> None:
        sport_key = "soccer_epl"
        store = Store(
            [
                cursor_row(
                    "FEATURED",
                    sport_key,
                    "2020-06-06T00:00:00Z",
                    status="QUARANTINED_PROVIDER_SCHEMA",
                    calls_completed=1,
                )
            ],
            competitions=[{"sport_key": sport_key, "has_outrights": False}],
        )
        client = Client()
        with patch("soccer_auto.historical._client", return_value=client):
            result = run_featured(store, max_calls=1)
        persisted = store.ops.rows[("HISTORICAL_CURSOR", f"FEATURED#{sport_key}")]
        self.assertEqual(result["calls"], 0)
        self.assertEqual(client.featured_calls, [])
        self.assertEqual(persisted["status"], "QUARANTINED_PROVIDER_SCHEMA")
        self.assertEqual(persisted["snapshot_at"], "2020-06-06T00:00:00Z")

    def test_provider_timestamps_are_normalized_and_must_advance(self) -> None:
        provider_at, next_at = _provider_timestamps(
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "next_timestamp": "2026-01-01T00:05:00+00:00",
            },
            "2026-01-01T00:00:00Z",
        )
        self.assertEqual(provider_at, "2026-01-01T00:00:00Z")
        self.assertEqual(next_at, "2026-01-01T00:05:00Z")
        with self.assertRaises(HistoricalTimestampError):
            _provider_timestamps(
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "next_timestamp": "2026-01-01T00:00:00Z",
                },
                "2026-01-01T00:00:00Z",
            )
        with self.assertRaises(HistoricalTimestampError):
            _provider_timestamps(
                {
                    "timestamp": "2026-01-01T00:01:00Z",
                    "next_timestamp": "2026-01-01T00:05:00Z",
                },
                "2026-01-01T00:00:00Z",
            )

    def test_http_200_historical_wrappers_require_exact_envelope_shapes(self) -> None:
        with self.assertRaisesRegex(HistoricalWrapperSchemaError, "missing timestamp"):
            _validated_wrapper({}, operation="historical_featured")
        with self.assertRaisesRegex(HistoricalWrapperSchemaError, "event list"):
            _validated_wrapper(
                {"timestamp": "2026-01-01T00:00:00Z", "data": {}},
                operation="historical_events",
            )
        with self.assertRaisesRegex(HistoricalWrapperSchemaError, "incomplete event identity"):
            _validated_wrapper(
                {"timestamp": "2026-01-01T00:00:00Z", "data": {}},
                operation="historical_event_odds",
            )

    def test_schema_error_includes_bounded_provider_diagnostics(self) -> None:
        payload = {
            "error_code": "HISTORICAL_DATA_UNAVAILABLE",
            "message": "no snapshot " * 100,
            **{f"diagnostic_{index}": index for index in range(20)},
        }
        with self.assertRaises(HistoricalWrapperSchemaError) as caught:
            _validated_wrapper(payload, operation="historical_featured")
        error = caught.exception
        self.assertLessEqual(len(error.top_level_keys), 13)
        self.assertEqual(error.error_code, "HISTORICAL_DATA_UNAVAILABLE")
        self.assertEqual(len(error.provider_message), 256)
        self.assertIn("error_code='HISTORICAL_DATA_UNAVAILABLE'", str(error))
        self.assertIn("message='no snapshot", str(error))

    def test_malformed_http_200_featured_wrapper_quarantines_without_advancing(self) -> None:
        sport_key = "soccer_future_league"
        requested_at = "2026-01-01T00:00:00Z"
        store = Store(
            [cursor_row("FEATURED", sport_key, requested_at)],
            competitions=[{"sport_key": sport_key, "has_outrights": False}],
        )
        client = Client()
        client.historical_odds = lambda *args, **kwargs: ApiResponse(
            data={"timestamp": requested_at},
            status=200,
            request_url="https://example.test/history/featured",
        )

        with patch("soccer_auto.historical._client", return_value=client), self.assertRaises(
            HistoricalWrapperSchemaError
        ):
            run_featured(store, max_calls=1)

        persisted = store.ops.rows[("HISTORICAL_CURSOR", f"FEATURED#{sport_key}")]
        self.assertEqual(persisted["snapshot_at"], requested_at)
        self.assertEqual(persisted["status"], "QUARANTINED_PROVIDER_SCHEMA")
        self.assertEqual(persisted["calls_completed"], 0)
        self.assertEqual(store.archives, [])

    def test_status_mode_is_provider_free_even_while_disabled(self) -> None:
        store = Store(
            [cursor_row("FEATURED", "soccer_one", "2026-01-01T00:00:00Z")]
        )
        with patch("soccer_auto.historical.SoccerStore", return_value=store), patch(
            "soccer_auto.historical._client",
            side_effect=AssertionError("status must not create a provider client"),
        ), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOCCER_AUTO_HISTORICAL_BACKFILL_ENABLED", None)
            result = historical_handler({"mode": "status"}, None)
        self.assertTrue(result["ok"])
        self.assertFalse(result["enabled"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["modes"]["featured"]["cursors"], 1)

    def test_missing_enabled_environment_fails_closed_before_provider(self) -> None:
        store = Store()
        with patch("soccer_auto.historical.SoccerStore", return_value=store), patch(
            "soccer_auto.historical._client",
            side_effect=AssertionError("disabled backfill must not create a provider client"),
        ), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOCCER_AUTO_HISTORICAL_BACKFILL_ENABLED", None)
            result = historical_handler({"mode": "featured"}, None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(
            result["reason"], "SOCCER_AUTO_HISTORICAL_BACKFILL_DISABLED"
        )

    def test_event_max_calls_is_capped_by_environment_owned_limit(self) -> None:
        store = Store()
        captured = []

        def featured(_store, *, max_calls, sport_key=None):
            captured.append(max_calls)
            self.assertIsNone(sport_key)
            return {"provider_calls": 0, "calls": 0}

        with patch("soccer_auto.historical.SoccerStore", return_value=store), patch(
            "soccer_auto.historical.run_featured", side_effect=featured
        ), patch.dict(
            os.environ,
            {"SOCCER_AUTO_HISTORICAL_BACKFILL_ENABLED": "true"},
            clear=False,
        ):
            result = historical_handler(
                {"mode": "featured", "max_calls": MAX_CALLS_PER_INVOCATION + 1000},
                None,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["max_calls"], MAX_CALLS_PER_INVOCATION)
        self.assertEqual(captured, [MAX_CALLS_PER_INVOCATION])

    def test_materialization_cannot_exceed_event_call_budget(self) -> None:
        store = Store()
        captured = []

        def materialize(_store, *, max_events, event_key=None):
            captured.append((max_events, event_key))
            return {"provider_calls": 0}

        with patch("soccer_auto.historical.SoccerStore", return_value=store), patch(
            "soccer_auto.historical_materializer.run_materialization",
            side_effect=materialize,
        ), patch.dict(
            os.environ,
            {"SOCCER_AUTO_HISTORICAL_BACKFILL_ENABLED": "true"},
            clear=False,
        ):
            result = historical_handler(
                {
                    "mode": "materialize",
                    "max_calls": 0,
                    "max_events": 5,
                    "event_key": "EVENT#soccer_epl#one",
                },
                None,
            )
        self.assertEqual(captured, [(0, "EVENT#soccer_epl#one")])
        self.assertEqual(result["provider_calls"], 0)

    def test_historical_manifest_is_never_training_eligible(self) -> None:
        store = Store()
        _manifest(
            store,
            mode="FEATURED",
            sport_key="soccer_one",
            requested_at="2026-01-01T00:00:00Z",
            provider_at="2026-01-01T00:00:00Z",
            raw_uri="s3://raw/item.json",
            payload_hash="hash",
            markets=["h2h"],
        )
        manifest = store.ops.writes[-1]
        self.assertFalse(manifest["training_eligible"])
        self.assertEqual(
            manifest["supervised_label_status"],
            "UNAVAILABLE_FROM_ODDS_API_HISTORICAL_ENDPOINTS",
        )


if __name__ == "__main__":
    unittest.main()
