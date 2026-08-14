from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from botocore.exceptions import ClientError, ReadTimeoutError  # noqa: E402
from soccer_auto import llm_analyst as llm_analyst_module  # noqa: E402
from soccer_auto.canonical import canonical_json, digest, iso_utc  # noqa: E402
from soccer_auto.llm_analyst import (  # noqa: E402
    ANALYSIS_ORIGIN,
    BEDROCK_MAX_OUTPUT_TOKENS,
    MAX_BEDROCK_REQUEST_BYTES,
    MAX_CONTEXT_CANONICAL_BYTES,
    _coverage_diagnostics,
    _context,
    _model_ids,
    _put_newer_llm_pointer,
    latest_llm_trials,
    llm_analyst_handler,
    validate_analysis,
)
from soccer_auto.market_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION  # noqa: E402
from soccer_auto.storage import (  # noqa: E402
    COVERAGE_DISPATCH_MANIFEST_VERSION,
    COVERAGE_PLAN_VERSION,
    EVENT_INVENTORY_AUTHORITY_VERSION,
    coverage_expected_batch_digests,
    coverage_plan_digest,
    ddb_safe,
    now_utc,
    plain,
)


class Ops:
    def __init__(self, rows=None, latest=None, autonomy=None, last_attempt=None):
        self.rows = rows or []
        self.latest = latest
        self.autonomy = autonomy or {}
        self.last_attempt = last_attempt
        self.writes = []

    def scan(self, **kwargs):
        return {"Items": self.rows}

    def get_item(self, *, Key, **kwargs):
        if Key == {"PK": "AUTONOMY", "SK": "STATE"}:
            return {"Item": self.autonomy}
        if Key == {"PK": "LLM_ANALYSIS", "SK": "LATEST"} and self.latest:
            return {"Item": self.latest}
        if (
            Key == {"PK": "LLM_ANALYSIS", "SK": "LAST_ATTEMPT"}
            and self.last_attempt
        ):
            return {"Item": self.last_attempt}
        return {}

    def put_item(self, **kwargs):
        self.writes.append(kwargs["Item"])
        return {}


class Store:
    def __init__(self, ops):
        self.ops = ops

    def list_competitions(self):
        return [{"sport_key": "soccer_epl", "active": True, "has_outrights": False}]

    def model_items(self):
        return []


class ExactCoverageStore(Store):
    def __init__(self, ops, cycles):
        super().__init__(ops)
        self.cycles = cycles

    def latest_coverage_cycles(self, **kwargs):
        rows = [
            {
                "commence_time": "2026-08-14T14:00:00Z",
                "schedule_revision": 1,
                "schedule_identity": f"identity-{row['event_key']}",
                **row,
            }
            for row in self.cycles
        ]
        for row in rows:
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
        return rows

    def latest_coverage_dispatch_manifest(self):
        from soccer_auto import llm_analyst as analyst_module

        rows = self.latest_coverage_cycles()
        observed_at = iso_utc(analyst_module.now_utc())
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
        return {
            **self.inventory_binding,
            "authority_state": "COMPLETED",
        }


class LlmBoundaryTests(unittest.TestCase):
    @staticmethod
    def _response(payload):
        return {
            "stopReason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 40, "totalTokens": 140},
            "output": {
                "message": {
                    "content": [{"text": json.dumps(payload)}],
                }
            }
        }

    def test_untrusted_trials_are_clamped_and_deduplicated(self) -> None:
        payload = {
            "summary": "soccer only",
            "coverage_findings": ["one"],
            "warnings": ["no leakage"],
            "recommended_trials": [
                {"learning_rate": 0.03, "l2": 0.001, "epochs": 60, "rationale": "valid"},
                {"learning_rate": 0.03, "l2": 0.001, "epochs": 60, "rationale": "duplicate"},
                {"learning_rate": 5, "l2": 0.001, "epochs": 60, "rationale": "unsafe"},
                {"learning_rate": 0.02, "l2": 0.001, "epochs": 5000, "rationale": "unsafe"},
            ],
        }
        result = validate_analysis(payload)
        self.assertEqual(result["validation_status"], "VALIDATED")
        self.assertEqual(len(result["recommended_trials"]), 1)
        self.assertIn("analysis_digest", result)

    def test_production_fallback_chain_is_ordered_and_allowlisted(self) -> None:
        fallbacks = (
            "mistral.ministral-3-14b-instruct",
            "us.meta.llama4-scout-17b-instruct-v1:0",
            "us.meta.llama4-maverick-17b-instruct-v1:0",
            "global.amazon.nova-2-lite-v1:0",
            "us.amazon.nova-pro-v1:0",
            "us.amazon.nova-lite-v1:0",
            "us.amazon.nova-micro-v1:0",
        )
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch("soccer_auto.llm_analyst.FALLBACK_MODEL_IDS", fallbacks),
        ):
            self.assertEqual(
                _model_ids(),
                ("us.amazon.nova-2-lite-v1:0", *fallbacks),
            )

    def test_mutable_llm_pointers_reject_an_older_slow_completion(self) -> None:
        class ChronologyOps:
            def __init__(self):
                self.row = None

            def put_item(
                self,
                *,
                Item,
                ConditionExpression=None,
                ExpressionAttributeValues=None,
                **kwargs,
            ):
                incoming = plain(Item)
                threshold = plain(ExpressionAttributeValues or {}).get(
                    ":attempt_started_order"
                )
                if (
                    ConditionExpression
                    and self.row
                    and int(self.row.get("attempt_started_order") or 0)
                    >= int(threshold or 0)
                ):
                    raise ClientError(
                        {
                            "Error": {
                                "Code": "ConditionalCheckFailedException",
                                "Message": "newer pointer already published",
                            }
                        },
                        "PutItem",
                    )
                self.row = incoming

        class PointerStore:
            def __init__(self):
                self.ops = ChronologyOps()

        store = PointerStore()
        newer = {
            "PK": "LLM_ANALYSIS",
            "SK": "LATEST",
            "attempt_id": "newer",
            "attempt_started_at": "2026-08-14T04:01:00Z",
        }
        older = {
            **newer,
            "attempt_id": "older",
            "attempt_started_at": "2026-08-14T04:00:00Z",
        }
        self.assertTrue(
            _put_newer_llm_pointer(
                store,
                newer,
                attempt_started_at=newer["attempt_started_at"],
            )
        )
        self.assertFalse(
            _put_newer_llm_pointer(
                store,
                older,
                attempt_started_at=older["attempt_started_at"],
            )
        )
        self.assertEqual(store.ops.row["attempt_id"], "newer")

    def test_trial_precision_is_canonical_before_provenance_digest(self) -> None:
        result = validate_analysis(
            {
                "summary": "high precision remains digest stable",
                "recommended_trials": [
                    {
                        "learning_rate": 0.03333333333333333,
                        "l2": 0.000123456789123456,
                        "epochs": 61,
                    }
                ],
            }
        )
        self.assertEqual(
            result["recommended_trials"][0]["learning_rate"], 0.03333333
        )
        self.assertEqual(
            result["recommended_trials"][0]["l2"], 0.0001234568
        )
        round_tripped = plain(ddb_safe(result))
        self.assertEqual(
            validate_analysis(round_tripped)["analysis_digest"],
            result["analysis_digest"],
        )

    def test_llm_output_cannot_add_training_controls(self) -> None:
        result = validate_analysis(
            {
                "summary": "unknown controls are stripped",
                "recommended_trials": [],
                "promotion_gate": "disable",
                "prediction": {"home": 1.0},
                "target": "away",
            }
        )
        self.assertNotIn("promotion_gate", result)
        self.assertNotIn("prediction", result)
        self.assertNotIn("target", result)

    def test_context_contains_missing_pair_and_failure_diagnostics(self) -> None:
        rows = [
            {
                "entity_type": "SOCCER_MARKET_INVENTORY",
                "inventory": {"book": {"markets": ["h2h", "totals"]}},
            },
            {
                "entity_type": "SOCCER_EVENT_COVERAGE_PLAN",
                "event_key": "event",
                "observed_at": "2026-08-14T04:00:00Z",
                "expected_pairs": ["book|h2h", "book|totals"],
            },
            {
                "entity_type": "SOCCER_EVENT_COVERAGE_FETCH",
                "event_key": "event",
                "plan_observed_at": "2026-08-14T04:00:00Z",
                "returned_pairs": ["book|h2h"],
            },
            {
                "entity_type": "SOCCER_COLLECTION_FAILURE",
                "event_key": "event",
                "operation": "event_odds",
                "permanent": True,
                "observed_at": "2026-08-14T04:01:00Z",
                "detail": "unsupported singleton",
            },
        ]
        context = _context(
            Store(
                Ops(
                    rows,
                    autonomy={
                        "authority": "DEGRADED",
                        "component_liveness_complete": False,
                        "component_liveness": {"freeze": {"healthy": False}},
                    },
                )
            )
        )
        coverage = context["coverage"]
        self.assertEqual(coverage["unique_bookmakers_seen"], 1)
        self.assertEqual(coverage["unique_markets_seen"], 2)
        self.assertEqual(coverage["expected_pairs"], 2)
        self.assertEqual(coverage["fetched_pairs"], 1)
        self.assertEqual(coverage["missing_pairs"], 1)
        self.assertEqual(coverage["permanent_collection_failures"], 1)
        self.assertFalse(context["autonomy"]["component_liveness_complete"])
        self.assertEqual(context["autonomy"]["liveness"]["unhealthy"], 1)
        self.assertEqual(
            context["autonomy"]["liveness"]["unhealthy_sample"],
            [{"component": "freeze", "reason": "UNKNOWN"}],
        )
        self.assertNotIn("component_liveness", context["autonomy"])

    def test_exact_context_exposes_every_unresolved_coverage_cause(self) -> None:
        expected = ["book|btts", "book|draw_no_bet", "book|h2h", "book|totals"]
        diagnostics = _coverage_diagnostics(
            ExactCoverageStore(
                Ops([]),
                [
                    {
                        "event_key": "event",
                        "plan_observed_at": "2026-08-14T04:00:00Z",
                        "plan_digest": "plan",
                        "discovery_observed_at": "2026-08-14T03:59:59Z",
                        "discovery_status": "HTTP_200",
                        "required_pairs": expected,
                        "probe_pairs": [],
                        "expected_digest": digest(expected),
                        "attempted_incomplete_pairs": ["book|btts"],
                        "quota_deferred_pairs": ["book|draw_no_bet"],
                        "failed_pairs": ["book|h2h"],
                    }
                ],
            )
        )
        self.assertEqual(diagnostics["attempted_incomplete_pairs"], 1)
        self.assertEqual(diagnostics["quota_deferred_pairs"], 1)
        self.assertEqual(diagnostics["failed_pairs"], 1)
        self.assertEqual(diagnostics["never_attempted_pairs"], 1)
        self.assertEqual(diagnostics["discovery_status_counts"], {"HTTP_200": 1})
        self.assertEqual(diagnostics["coverage_integrity_failures"], 0)

    def test_unsampled_empty_cycle_still_blocks_llm_coverage_authority(self) -> None:
        cycles = []
        for index in range(9):
            required = [] if index == 0 else ["book|h2h"]
            cycles.append(
                {
                    "event_key": f"event-{index}",
                    "plan_observed_at": f"2026-08-14T04:0{index}:00Z",
                    "discovery_observed_at": f"2026-08-14T04:0{index}:00Z",
                    "discovery_status": "HTTP_200",
                    "required_pairs": required,
                    "probe_pairs": [],
                    "expected_digest": digest(required),
                    "returned_pairs": list(required),
                }
            )
        diagnostics = _coverage_diagnostics(ExactCoverageStore(Ops([]), cycles))
        self.assertEqual(len(diagnostics["latest_event_cycles"]), 8)
        self.assertEqual(diagnostics["incomplete_latest_event_cycles"], 1)
        self.assertEqual(diagnostics["incomplete_request_cycles"], 1)
        self.assertFalse(diagnostics["coverage_complete"])
        self.assertFalse(diagnostics["request_cycles_complete"])

    def test_context_has_a_hard_canonical_byte_ceiling(self) -> None:
        huge = "\\\"" * 500
        rows = [
            {
                "entity_type": "SOCCER_MARKET_INVENTORY",
                "inventory": {
                    f"book-{index}-{huge}": {
                        "markets": [f"market-{market}-{huge}" for market in range(20)]
                    }
                    for index in range(20)
                },
            }
        ]
        for index in range(40):
            event_key = f"event-{index}-{huge}"
            observed_at = f"2026-08-14T04:{index:02d}:00Z"
            rows.extend(
                [
                    {
                        "entity_type": "SOCCER_EVENT_COVERAGE_PLAN",
                        "event_key": event_key,
                        "observed_at": observed_at,
                        "expected_pairs": [f"book-{pair}|h2h-{huge}" for pair in range(10)],
                    },
                    {
                        "entity_type": "SOCCER_COLLECTION_FAILURE",
                        "event_key": event_key,
                        "operation": huge,
                        "permanent": True,
                        "observed_at": observed_at,
                        "detail": huge,
                    },
                ]
            )
        autonomy = {
            "authority": huge,
            "reason": huge,
            "component_liveness_complete": False,
            "component_liveness": {
                f"component-{index}-{huge}": {
                    "healthy": False,
                    "reason": huge,
                    "function_name": huge,
                    "error": huge,
                }
                for index in range(30)
            },
        }

        context = _context(Store(Ops(rows, autonomy=autonomy)))

        self.assertLessEqual(
            len(canonical_json(context).encode("utf-8")),
            MAX_CONTEXT_CANONICAL_BYTES,
        )
        _, _, request_bytes = llm_analyst_module._bedrock_request(context)
        self.assertLessEqual(
            request_bytes,
            MAX_BEDROCK_REQUEST_BYTES,
        )
        self.assertLessEqual(
            len(context["autonomy"]["liveness"].get("unhealthy_sample", [])), 8
        )

    def test_production_shape_context_fits_with_exact_authority_proof(self) -> None:
        observed = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
        pairs = [f"book-{index}|h2h" for index in range(8)]
        cycles = [
            {
                "event_key": f"event-{index:02d}-0123456789abcdef0123456789abcdef",
                "plan_observed_at": f"2026-08-14T08:{index:02d}:00Z",
                "discovery_observed_at": f"2026-08-14T08:{index:02d}:00Z",
                "discovery_status": "HTTP_200",
                "required_pairs": pairs,
                "probe_pairs": [],
                "expected_digest": digest(pairs),
                "returned_pairs": pairs,
            }
            for index in range(43)
        ]
        autonomy = {
            "authority": "SHADOW_LEARNING",
            "reason": "INSUFFICIENT_TRAINING_ROWS",
            "promotion_blocked": True,
            "counts": {
                "competitions": 67,
                "events": 568,
                "snapshot_slots": 512,
                "locks": 12,
                "settlements": 32,
                "predictions": 0,
                "models": 0,
            },
            "queues": {"collection": 0, "dead_letter": 0},
            "latest_quota": {
                "operation": "historical_featured",
                "remaining": 2_081_053,
                "used": 2_918_947,
                "last_cost": 270,
                "observed_at": "2026-08-14T09:00:05.203564Z",
            },
            "component_liveness": {
                f"component-{index}": {"healthy": True, "reason": "HEALTHY"}
                for index in range(18)
            },
            "component_liveness_complete": True,
            "updated_at": "2026-08-14T09:00:12.615948Z",
        }
        class ProductionShapeStore(ExactCoverageStore):
            def list_competitions(self):
                return [
                    {
                        "sport_key": f"soccer_competition_{index}",
                        "active": True,
                        "has_outrights": False,
                    }
                    for index in range(67)
                ]

        store = ProductionShapeStore(Ops([], autonomy=autonomy), cycles)

        with patch("soccer_auto.llm_analyst.now_utc", return_value=observed):
            context = _context(store)
            _, _, request_bytes = llm_analyst_module._bedrock_request(context)

        context_bytes = len(canonical_json(context).encode("utf-8"))
        self.assertLessEqual(context_bytes, MAX_CONTEXT_CANONICAL_BYTES)
        self.assertLessEqual(request_bytes, MAX_BEDROCK_REQUEST_BYTES)
        self.assertTrue(context["coverage"]["dispatch_manifest"]["authoritative"])
        self.assertTrue(
            context["coverage"]["dispatch_manifest"][
                "inventory_authority_current"
            ]
        )
        self.assertEqual(
            context["coverage"]["dispatch_manifest"]["manifest_events"], 43
        )
        self.assertEqual(context["coverage"]["expected_pairs"], 43 * len(pairs))
        self.assertEqual(context["coverage"]["fetched_pairs"], 43 * len(pairs))
        self.assertEqual(
            context["coverage"]["discovery_status_counts"], {"HTTP_200": 43}
        )
        self.assertEqual(
            context["feature_summary"]["direct_feature_count"],
            sum(
                not str(name).startswith(
                    ("league_bucket_", "market_bucket_", "market_bucket_movement_")
                )
                for name in FEATURE_NAMES
            ),
        )
        self.assertEqual(
            context["feature_summary"]["feature_names_digest"],
            digest(
                {
                    "feature_schema_version": FEATURE_SCHEMA_VERSION,
                    "feature_names": list(FEATURE_NAMES),
                }
            ),
        )
        self.assertNotIn("direct_feature_names", context["feature_summary"])

    def test_expired_analysis_cannot_control_a_future_training_search(self) -> None:
        validated = validate_analysis(
            {
                "summary": "expired bounded soccer research",
                "recommended_trials": [
                    {"learning_rate": 0.03, "l2": 0.001, "epochs": 60}
                ]
            }
        )
        created_at = "2026-08-10T00:00:00Z"
        expires_at = int(datetime(2026, 8, 11, tzinfo=timezone.utc).timestamp())
        row = {
            **validated,
            "analysis_origin": ANALYSIS_ORIGIN,
            "model_id": "us.amazon.nova-2-lite-v1:0",
            "context_digest": "expired-context-digest",
            "created_at": created_at,
            "expires_at": expires_at,
            "stop_reason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 40, "totalTokens": 140},
        }
        row["analysis_digest"] = digest(
            {
                **{key: value for key, value in validated.items() if key != "analysis_digest"},
                "analysis_origin": ANALYSIS_ORIGIN,
                "model_id": row["model_id"],
                "context_digest": row["context_digest"],
                "created_at": created_at,
                "expires_at": expires_at,
                "stop_reason": row["stop_reason"],
                "usage": row["usage"],
            }
        )
        with patch(
            "soccer_auto.llm_analyst.now_utc",
            return_value=datetime(2026, 8, 14, tzinfo=timezone.utc),
        ):
            trials, analysis_digest = latest_llm_trials(Store(Ops(latest=row)))
        self.assertEqual(trials, [])
        self.assertIsNone(analysis_digest)

    def test_fresh_legacy_analysis_without_context_authority_is_rejected(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        validated = validate_analysis(
            {
                "summary": "legacy analysis predates exact context authority",
                "recommended_trials": [
                    {"learning_rate": 0.03, "l2": 0.001, "epochs": 60}
                ],
            }
        )
        legacy_content = {
            **validated,
            "analysis_version": "soccer-auto-llm-analyst-v2",
        }
        created_at = "2026-08-14T03:00:00Z"
        expires_at = int((observed + timedelta(hours=12)).timestamp())
        row = {
            **legacy_content,
            "analysis_origin": ANALYSIS_ORIGIN,
            "model_id": "us.amazon.nova-lite-v1:0",
            "context_digest": "legacy-context-digest",
            "created_at": created_at,
            "expires_at": expires_at,
            "stop_reason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 40, "totalTokens": 140},
        }
        row["analysis_digest"] = digest(
            {
                **{
                    key: value
                    for key, value in legacy_content.items()
                    if key != "analysis_digest"
                },
                "analysis_origin": row["analysis_origin"],
                "model_id": row["model_id"],
                "context_digest": row["context_digest"],
                "created_at": created_at,
                "expires_at": expires_at,
                "stop_reason": row["stop_reason"],
                "usage": row["usage"],
            }
        )
        with patch("soccer_auto.llm_analyst.now_utc", return_value=observed):
            trials, analysis_digest = latest_llm_trials(Store(Ops(latest=row)))
        self.assertEqual(trials, [])
        self.assertIsNone(analysis_digest)

    def test_success_writes_validated_analysis_latest_and_attempt(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        ops = Ops()
        store = ExactCoverageStore(ops, [])
        bedrock = Mock()
        bedrock.converse.return_value = self._response(
            {
                "summary": "bounded soccer research",
                "recommended_trials": [
                    {"learning_rate": 0.03, "l2": 0.001, "epochs": 60}
                ],
            }
        )
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch(
                "soccer_auto.llm_analyst.boto3.client", return_value=bedrock
            ) as client_mock,
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
        ):
            result = llm_analyst_handler({}, None)

        self.assertEqual(result["status"], "ANALYZED")
        self.assertTrue(result["attempt_id"])
        self.assertEqual(result["attempt_started_at"], iso_utc(observed))
        self.assertEqual([row["SK"] for row in ops.writes][-2:], ["LATEST", "LAST_ATTEMPT"])
        self.assertTrue(str(ops.writes[0]["SK"]).startswith("ANALYSIS#"))
        self.assertEqual(ops.writes[0]["model_id"], "us.amazon.nova-2-lite-v1:0")
        self.assertEqual(
            ops.writes[0]["attempted_model_ids"],
            ["us.amazon.nova-2-lite-v1:0"],
        )
        self.assertEqual(ops.writes[-1]["status"], "ANALYZED")
        self.assertEqual(ops.writes[-1]["model_id"], "us.amazon.nova-2-lite-v1:0")
        self.assertEqual(ops.writes[-1]["analysis_digest"], result["analysis_digest"])
        request = bedrock.converse.call_args.kwargs
        self.assertEqual(
            request["inferenceConfig"]["maxTokens"], BEDROCK_MAX_OUTPUT_TOKENS
        )
        self.assertLessEqual(
            result["context_byte_count"], MAX_CONTEXT_CANONICAL_BYTES
        )
        self.assertEqual(
            result["context_byte_count"], ops.writes[0]["context_byte_count"]
        )
        self.assertEqual(
            result["request_byte_count"], ops.writes[-1]["request_byte_count"]
        )
        self.assertLessEqual(result["request_byte_count"], MAX_BEDROCK_REQUEST_BYTES)
        self.assertEqual(
            ops.writes[-1]["max_output_tokens"], BEDROCK_MAX_OUTPUT_TOKENS
        )
        client_mock.assert_called_once()
        self.assertEqual(client_mock.call_args.args, ("bedrock-runtime",))
        config = client_mock.call_args.kwargs["config"]
        self.assertEqual(config.connect_timeout, 3)
        self.assertEqual(config.read_timeout, 30)
        self.assertEqual(
            config.retries,
            {"mode": "standard", "total_max_attempts": 1},
        )

    def test_nonauthoritative_coverage_defers_before_bedrock_or_writes(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        ops = Ops()

        class NonAuthoritativeCoverageStore(ExactCoverageStore):
            def event_inventory_authority(self):
                return {
                    "authority_state": "RUNNING",
                    "authority_version": EVENT_INVENTORY_AUTHORITY_VERSION,
                    "generation_id": "newer-inventory-generation",
                    "authority_revision": 3,
                    "completed_at": "",
                }

        store = NonAuthoritativeCoverageStore(ops, [])
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
            patch("soccer_auto.llm_analyst.boto3.client") as client_mock,
        ):
            result = llm_analyst_handler({"force_refresh": True}, None)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "DEFERRED_CONTEXT_AUTHORITY")
        self.assertEqual(
            result["reason"],
            "COVERAGE_DISPATCH_MANIFEST_NOT_AUTHORITATIVE",
        )
        self.assertEqual(result["attempted_model_ids"], [])
        self.assertEqual(result["inventory_authority_state"], "RUNNING")
        self.assertEqual(ops.writes, [])
        client_mock.assert_not_called()

    def test_primary_daily_token_throttle_uses_real_bedrock_fallback(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        ops = Ops()
        store = ExactCoverageStore(ops, [])
        bedrock = Mock()

        def converse(**kwargs):
            if kwargs["modelId"] == "us.amazon.nova-2-lite-v1:0":
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ThrottlingException",
                            "Message": "Too many tokens per day, please wait before trying again.",
                        }
                    },
                    "Converse",
                )
            return self._response(
                {
                    "summary": "bounded soccer research from the fallback",
                    "recommended_trials": [
                        {"learning_rate": 0.02, "l2": 0.002, "epochs": 50}
                    ],
                }
            )

        bedrock.converse.side_effect = converse
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch(
                "soccer_auto.llm_analyst.FALLBACK_MODEL_IDS",
                (
                    "mistral.ministral-3-14b-instruct",
                    "us.amazon.nova-micro-v1:0",
                ),
            ),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
        ):
            result = llm_analyst_handler({}, None)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ANALYZED")
        self.assertEqual(
            result["model_id"], "mistral.ministral-3-14b-instruct"
        )
        self.assertEqual(
            result["attempted_model_ids"],
            [
                "us.amazon.nova-2-lite-v1:0",
                "mistral.ministral-3-14b-instruct",
            ],
        )
        self.assertEqual(
            [call.kwargs["modelId"] for call in bedrock.converse.call_args_list],
            [
                "us.amazon.nova-2-lite-v1:0",
                "mistral.ministral-3-14b-instruct",
            ],
        )
        analysis, latest, attempt = ops.writes
        self.assertEqual(
            analysis["model_id"], "mistral.ministral-3-14b-instruct"
        )
        self.assertEqual(
            latest["model_id"], "mistral.ministral-3-14b-instruct"
        )
        self.assertEqual(
            attempt["model_id"], "mistral.ministral-3-14b-instruct"
        )
        self.assertEqual(attempt["attempted_model_ids"], result["attempted_model_ids"])
        self.assertEqual(
            result["model_errors"],
            [
                {
                    "model_id": "us.amazon.nova-2-lite-v1:0",
                    "error_code": "ThrottlingException",
                    "category": "DAILY_TOKEN_QUOTA",
                    "message": "Too many tokens per day, please wait before trying again.",
                }
            ],
        )
        self.assertEqual(attempt["model_errors"], result["model_errors"])

    def test_transport_timeout_is_bounded_and_falls_through_to_next_model(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        ops = Ops()
        store = ExactCoverageStore(ops, [])
        bedrock = Mock()
        bedrock.converse.side_effect = [
            ReadTimeoutError(
                endpoint_url=(
                    "https://bedrock-runtime.us-east-1.amazonaws.com/"
                    "?sensitive-token=must-not-persist"
                )
            ),
            self._response(
                {
                    "summary": "fallback recovered after bounded client timeout",
                    "recommended_trials": [],
                }
            ),
        ]
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch(
                "soccer_auto.llm_analyst.FALLBACK_MODEL_IDS",
                ("us.amazon.nova-lite-v1:0",),
            ),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
        ):
            result = llm_analyst_handler({}, None)

        self.assertEqual(result["status"], "ANALYZED")
        self.assertEqual(result["model_id"], "us.amazon.nova-lite-v1:0")
        self.assertEqual(
            result["attempted_model_ids"],
            ["us.amazon.nova-2-lite-v1:0", "us.amazon.nova-lite-v1:0"],
        )
        self.assertEqual(
            result["model_errors"],
            [
                {
                    "model_id": "us.amazon.nova-2-lite-v1:0",
                    "error_code": "ReadTimeoutError",
                    "category": "TRANSIENT_CLIENT",
                    "message": "Bedrock Runtime client transport failure",
                }
            ],
        )
        attempt = ops.writes[-1]
        self.assertEqual(attempt["status"], "ANALYZED")
        self.assertEqual(attempt["model_errors"], result["model_errors"])
        self.assertNotIn("sensitive-token", canonical_json(attempt))

    def test_all_model_daily_token_throttles_are_deferred_without_latest_write(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        ops = Ops()
        store = ExactCoverageStore(ops, [])
        bedrock = Mock()
        bedrock.converse.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ThrottlingException",
                    "Message": "Too many tokens per day, please wait before trying again.",
                }
            },
            "Converse",
        )
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch(
                "soccer_auto.llm_analyst.FALLBACK_MODEL_IDS",
                ("us.amazon.nova-lite-v1:0", "us.amazon.nova-micro-v1:0"),
            ),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
        ):
            result = llm_analyst_handler({}, None)

        expected_models = [
            "us.amazon.nova-2-lite-v1:0",
            "us.amazon.nova-lite-v1:0",
            "us.amazon.nova-micro-v1:0",
        ]
        self.assertEqual(bedrock.converse.call_count, 3)
        self.assertEqual([row["SK"] for row in ops.writes], ["LAST_ATTEMPT"])
        attempt = ops.writes[0]
        self.assertFalse(result["ok"])
        self.assertFalse(result["analysis_available"])
        self.assertEqual(result["validated_trials"], 0)
        self.assertEqual(result["status"], "DEFERRED_QUOTA")
        self.assertEqual(result["reason"], "BEDROCK_ALL_FALLBACK_MODELS_UNAVAILABLE")
        self.assertEqual(result["retry_after"], "2026-08-14T10:00:00Z")
        self.assertEqual(result["attempted_model_ids"], expected_models)
        self.assertEqual(attempt["status"], "DEFERRED_QUOTA")
        self.assertEqual(attempt["reason"], "BEDROCK_ALL_FALLBACK_MODELS_UNAVAILABLE")
        self.assertEqual(attempt["retry_after"], "2026-08-14T10:00:00Z")
        self.assertEqual(attempt["attempted_model_ids"], expected_models)
        self.assertEqual(
            [error["model_id"] for error in attempt["model_errors"]],
            expected_models,
        )
        self.assertEqual(
            {error["error_code"] for error in attempt["model_errors"]},
            {"ThrottlingException"},
        )
        self.assertEqual(
            {error["category"] for error in attempt["model_errors"]},
            {"DAILY_TOKEN_QUOTA"},
        )
        self.assertEqual(result["model_errors"], attempt["model_errors"])
        self.assertEqual(
            attempt["expires_at"],
            int((observed + timedelta(days=30)).timestamp()),
        )

    def test_mixed_recoverable_errors_are_redacted_and_not_mislabeled_quota(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        store = ExactCoverageStore(Ops(), [])
        bedrock = Mock()
        errors = [
            ("ThrottlingException", "Account quota exceeded", 429, "quota-request"),
            (
                "ServiceUnavailableException",
                "Unavailable for arn:aws:bedrock:us-east-1:123456789012:inference-profile/example",
                503,
                "service-request",
            ),
            ("ModelTimeoutException", "Timed out", 408, "timeout-request"),
        ]
        bedrock.converse.side_effect = [
            ClientError(
                {
                    "Error": {"Code": code, "Message": message},
                    "ResponseMetadata": {
                        "HTTPStatusCode": status,
                        "RequestId": request_id,
                    },
                },
                "Converse",
            )
            for code, message, status, request_id in errors
        ]
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch(
                "soccer_auto.llm_analyst.FALLBACK_MODEL_IDS",
                ("us.amazon.nova-lite-v1:0", "us.amazon.nova-micro-v1:0"),
            ),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
            self.assertRaisesRegex(
                RuntimeError,
                r"nova-2-lite-v1:0=ThrottlingException/ACCOUNT_QUOTA.*"
                r"nova-lite-v1:0=ServiceUnavailableException/TRANSIENT_SERVICE.*"
                r"nova-micro-v1:0=ModelTimeoutException/TRANSIENT_SERVICE",
            ),
        ):
            llm_analyst_handler({}, None)

        attempt = store.ops.writes[0]
        self.assertEqual(attempt["status"], "DEFERRED_TRANSIENT")
        self.assertEqual(attempt["retry_after"], "2026-08-14T04:15:00Z")
        self.assertEqual(
            [error["http_status"] for error in attempt["model_errors"]],
            [429, 503, 408],
        )
        self.assertEqual(
            [error["request_id"] for error in attempt["model_errors"]],
            ["quota-request", "service-request", "timeout-request"],
        )
        service_message = attempt["model_errors"][1]["message"]
        self.assertIn("[REDACTED_ARN]", service_message)
        self.assertNotIn("123456789012", service_message)

    def test_generic_account_throttle_uses_short_retry_not_daily_retry(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        store = ExactCoverageStore(Ops(), [])
        bedrock = Mock()
        bedrock.converse.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ThrottlingException",
                    "Message": "Account request quota exceeded",
                }
            },
            "Converse",
        )
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch("soccer_auto.llm_analyst.FALLBACK_MODEL_IDS", ()),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
        ):
            result = llm_analyst_handler({}, None)

        attempt = store.ops.writes[0]
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "DEFERRED_QUOTA")
        self.assertEqual(attempt["status"], "DEFERRED_QUOTA")
        self.assertEqual(attempt["retry_after"], "2026-08-14T04:15:00Z")
        self.assertEqual(attempt["model_errors"][0]["category"], "ACCOUNT_QUOTA")

    def test_global_profile_access_denial_falls_through_to_nova_pro(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        store = ExactCoverageStore(Ops(), [])
        bedrock = Mock()

        def converse(**kwargs):
            if kwargs["modelId"] == "global.amazon.nova-2-lite-v1:0":
                raise ClientError(
                    {
                        "Error": {
                            "Code": "AccessDeniedException",
                            "Message": "Global CRIS is blocked by account policy",
                        }
                    },
                    "Converse",
                )
            return self._response(
                {"summary": "Nova Pro recovered the bounded analysis"}
            )

        bedrock.converse.side_effect = converse
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "global.amazon.nova-2-lite-v1:0"),
            patch(
                "soccer_auto.llm_analyst.FALLBACK_MODEL_IDS",
                ("us.amazon.nova-pro-v1:0",),
            ),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
        ):
            result = llm_analyst_handler({}, None)

        self.assertEqual(result["status"], "ANALYZED")
        self.assertEqual(result["model_id"], "us.amazon.nova-pro-v1:0")
        self.assertEqual(
            result["attempted_model_ids"],
            ["global.amazon.nova-2-lite-v1:0", "us.amazon.nova-pro-v1:0"],
        )
        self.assertEqual(
            result["model_errors"][0]["category"],
            "CONFIGURATION_UNAVAILABLE",
        )

    def test_all_configuration_failures_are_persisted_as_blocked(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        store = ExactCoverageStore(Ops(), [])
        bedrock = Mock()
        bedrock.converse.side_effect = ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "Candidate profile is unavailable",
                }
            },
            "Converse",
        )
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "global.amazon.nova-2-lite-v1:0"),
            patch(
                "soccer_auto.llm_analyst.FALLBACK_MODEL_IDS",
                ("us.amazon.nova-pro-v1:0",),
            ),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
            self.assertRaisesRegex(
                RuntimeError,
                "AccessDeniedException/CONFIGURATION_UNAVAILABLE",
            ),
        ):
            llm_analyst_handler({}, None)

        attempt = store.ops.writes[0]
        self.assertEqual(attempt["status"], "BLOCKED_CONFIGURATION")
        self.assertEqual(
            attempt["reason"],
            "BEDROCK_ALL_FALLBACK_MODELS_CONFIGURATION_UNAVAILABLE",
        )
        self.assertEqual(len(attempt["model_errors"]), 2)

    def test_non_candidate_client_error_is_reraised(self) -> None:
        store = ExactCoverageStore(Ops(), [])
        bedrock = Mock()
        bedrock.converse.side_effect = ClientError(
            {"Error": {"Code": "UnrecognizedClientException", "Message": "denied"}},
            "Converse",
        )
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            self.assertRaises(ClientError),
        ):
            llm_analyst_handler({}, None)
        self.assertEqual(store.ops.writes, [])

    def test_malformed_model_json_is_persisted_and_raised_fail_closed(self) -> None:
        store = ExactCoverageStore(Ops(), [])
        bedrock = Mock()
        bedrock.converse.return_value = {
            "output": {"message": {"content": [{"text": "not-json"}]}}
        }
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch("soccer_auto.llm_analyst.FALLBACK_MODEL_IDS", ()),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            self.assertRaisesRegex(RuntimeError, "INVALID_RESPONSE"),
        ):
            llm_analyst_handler({}, None)
        self.assertEqual(len(store.ops.writes), 1)
        attempt = store.ops.writes[0]
        self.assertEqual(attempt["status"], "BLOCKED_INVALID_RESPONSE")
        self.assertEqual(
            attempt["reason"],
            "BEDROCK_ALL_FALLBACK_MODELS_INVALID_RESPONSE",
        )
        self.assertEqual(attempt["model_errors"][0]["error_code"], "INVALID_RESPONSE")

    def test_active_daily_quota_deferral_is_reused_without_bedrock_calls(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        last_attempt = {
            "PK": "LLM_ANALYSIS",
            "SK": "LAST_ATTEMPT",
            "entity_type": "SOCCER_LLM_ATTEMPT",
            "status": "DEFERRED_QUOTA",
            "reason": "BEDROCK_ALL_FALLBACK_MODELS_UNAVAILABLE",
            "model_id": "us.amazon.nova-2-lite-v1:0",
            "model_errors": [
                {
                    "model_id": "us.amazon.nova-2-lite-v1:0",
                    "error_code": "ThrottlingException",
                    "category": "DAILY_TOKEN_QUOTA",
                }
            ],
            "retry_after": "2026-08-14T10:00:00Z",
            "attempt_id": "prior-attempt",
            "attempt_started_at": "2026-08-14T03:00:00Z",
        }
        store = Store(Ops(last_attempt=last_attempt))
        with (
            patch(
                "soccer_auto.llm_analyst.MODEL_ID",
                "us.amazon.nova-2-lite-v1:0",
            ),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
            patch("soccer_auto.llm_analyst._context") as context_mock,
            patch("soccer_auto.llm_analyst.boto3.client") as client_mock,
        ):
            result = llm_analyst_handler({}, None)

        self.assertEqual(result["status"], "DEFERRED_QUOTA")
        self.assertTrue(result["reused_deferral"])
        self.assertEqual(result["attempted_model_ids"], [])
        self.assertEqual(result["attempt_id"], "prior-attempt")
        context_mock.assert_not_called()
        client_mock.assert_not_called()
        self.assertEqual(store.ops.writes, [])

    def test_fresh_validated_latest_is_reused_before_context_or_converse(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        validated = validate_analysis(
            {"summary": "fresh bounded soccer research", "recommended_trials": []}
        )
        created_at = "2026-08-14T03:00:00Z"
        expires_at = int((observed + timedelta(hours=12)).timestamp())
        latest = {
            **validated,
            "analysis_origin": ANALYSIS_ORIGIN,
            "model_id": "us.amazon.nova-lite-v1:0",
            "context_digest": "fresh-context-digest",
            "created_at": created_at,
            "expires_at": expires_at,
            "stop_reason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 40, "totalTokens": 140},
        }
        latest["analysis_digest"] = digest(
            {
                **{key: value for key, value in validated.items() if key != "analysis_digest"},
                "analysis_origin": ANALYSIS_ORIGIN,
                "model_id": latest["model_id"],
                "context_digest": latest["context_digest"],
                "created_at": created_at,
                "expires_at": expires_at,
                "stop_reason": latest["stop_reason"],
                "usage": latest["usage"],
            }
        )
        store = Store(Ops(latest=latest))
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
            patch("soccer_auto.llm_analyst._context") as context_mock,
            patch("soccer_auto.llm_analyst.boto3.client") as client_mock,
        ):
            result = llm_analyst_handler({}, None)

        self.assertEqual(result["status"], "FRESH_ANALYSIS_REUSED")
        context_mock.assert_not_called()
        client_mock.assert_not_called()
        self.assertEqual(store.ops.writes, [])


if __name__ == "__main__":
    unittest.main()
