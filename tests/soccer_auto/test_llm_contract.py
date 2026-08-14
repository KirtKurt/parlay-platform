from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from botocore.exceptions import ClientError  # noqa: E402
from soccer_auto.canonical import digest  # noqa: E402
from soccer_auto.llm_analyst import (  # noqa: E402
    ANALYSIS_ORIGIN,
    _context,
    latest_llm_trials,
    llm_analyst_handler,
    validate_analysis,
)
from soccer_auto.storage import ddb_safe, plain  # noqa: E402


class Ops:
    def __init__(self, rows=None, latest=None, autonomy=None):
        self.rows = rows or []
        self.latest = latest
        self.autonomy = autonomy or {}
        self.writes = []

    def scan(self, **kwargs):
        return {"Items": self.rows}

    def get_item(self, *, Key, **kwargs):
        if Key == {"PK": "AUTONOMY", "SK": "STATE"}:
            return {"Item": self.autonomy}
        if Key == {"PK": "LLM_ANALYSIS", "SK": "LATEST"} and self.latest:
            return {"Item": self.latest}
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

    def test_success_writes_validated_analysis_latest_and_attempt(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        ops = Ops()
        store = Store(ops)
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
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
        ):
            result = llm_analyst_handler({}, None)

        self.assertEqual(result["status"], "ANALYZED")
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

    def test_primary_daily_token_throttle_uses_real_bedrock_fallback(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        ops = Ops()
        store = Store(ops)
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
                ("us.amazon.nova-lite-v1:0", "us.amazon.nova-micro-v1:0"),
            ),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            patch("soccer_auto.llm_analyst.now_utc", return_value=observed),
        ):
            result = llm_analyst_handler({}, None)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ANALYZED")
        self.assertEqual(result["model_id"], "us.amazon.nova-lite-v1:0")
        self.assertEqual(
            result["attempted_model_ids"],
            ["us.amazon.nova-2-lite-v1:0", "us.amazon.nova-lite-v1:0"],
        )
        self.assertEqual(
            [call.kwargs["modelId"] for call in bedrock.converse.call_args_list],
            ["us.amazon.nova-2-lite-v1:0", "us.amazon.nova-lite-v1:0"],
        )
        analysis, latest, attempt = ops.writes
        self.assertEqual(analysis["model_id"], "us.amazon.nova-lite-v1:0")
        self.assertEqual(latest["model_id"], "us.amazon.nova-lite-v1:0")
        self.assertEqual(attempt["model_id"], "us.amazon.nova-lite-v1:0")
        self.assertEqual(attempt["attempted_model_ids"], result["attempted_model_ids"])

    def test_all_model_daily_token_throttles_are_deferred_without_latest_write(self) -> None:
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        ops = Ops()
        store = Store(ops)
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
            self.assertRaisesRegex(
                RuntimeError,
                "all configured real Bedrock analyst models are temporarily unavailable",
            ),
        ):
            llm_analyst_handler({}, None)

        expected_models = [
            "us.amazon.nova-2-lite-v1:0",
            "us.amazon.nova-lite-v1:0",
            "us.amazon.nova-micro-v1:0",
        ]
        self.assertEqual(bedrock.converse.call_count, 3)
        self.assertEqual([row["SK"] for row in ops.writes], ["LAST_ATTEMPT"])
        attempt = ops.writes[0]
        self.assertEqual(attempt["status"], "DEFERRED_QUOTA")
        self.assertEqual(attempt["reason"], "BEDROCK_ALL_FALLBACK_MODELS_UNAVAILABLE")
        self.assertEqual(attempt["retry_after"], "2026-08-14T10:00:00Z")
        self.assertEqual(attempt["attempted_model_ids"], expected_models)
        self.assertEqual(
            attempt["expires_at"],
            int((observed + timedelta(days=30)).timestamp()),
        )

    def test_nonquota_bedrock_client_error_is_reraised(self) -> None:
        store = Store(Ops())
        bedrock = Mock()
        bedrock.converse.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
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

    def test_malformed_model_json_is_reraised_fail_closed(self) -> None:
        store = Store(Ops())
        bedrock = Mock()
        bedrock.converse.return_value = {
            "output": {"message": {"content": [{"text": "not-json"}]}}
        }
        with (
            patch("soccer_auto.llm_analyst.MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
            patch("soccer_auto.llm_analyst.SoccerStore", return_value=store),
            patch("soccer_auto.llm_analyst.boto3.client", return_value=bedrock),
            self.assertRaises(ValueError),
        ):
            llm_analyst_handler({}, None)
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
