from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.autonomous_controller import (  # noqa: E402
    COMPONENT_LIVENESS,
    _llm_state,
    authority_state,
    component_liveness,
)
from soccer_auto.llm_analyst import validate_analysis  # noqa: E402


class CloudWatch:
    def __init__(self, values=None):
        self.values = values or {}
        self.calls = []

    def get_metric_statistics(self, **kwargs):
        self.calls.append(kwargs)
        value = self.values.get((kwargs["Dimensions"][0]["Value"], kwargs["MetricName"]), 0)
        if value is None:
            return {"Datapoints": []}
        return {
            "Datapoints": [
                {
                    "Timestamp": datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc),
                    "Sum": value,
                }
            ]
        }


def function_environment() -> dict[str, str]:
    return {
        environment_key: f"soccer-{component}"
        for component, (environment_key, _) in COMPONENT_LIVENESS.items()
    }


class ComponentLivenessTests(unittest.TestCase):
    def test_every_scheduled_component_has_a_recent_error_free_heartbeat(self) -> None:
        values = {
            (f"soccer-{component}", "Invocations"): 1
            for component in COMPONENT_LIVENESS
        }
        cloudwatch = CloudWatch(values)
        with patch.dict("os.environ", function_environment(), clear=False):
            result = component_liveness(
                cloudwatch,
                datetime(2026, 8, 14, 4, 5, tzinfo=timezone.utc),
            )
        self.assertEqual(set(result), set(COMPONENT_LIVENESS))
        self.assertTrue(all(row["healthy"] for row in result.values()))
        self.assertEqual(len(cloudwatch.calls), len(COMPONENT_LIVENESS) * 2)

    def test_missing_invocation_and_lambda_error_fail_closed(self) -> None:
        values = {
            (f"soccer-{component}", "Invocations"): 1
            for component in COMPONENT_LIVENESS
        }
        values[("soccer-inventory", "Invocations")] = None
        values[("soccer-freeze", "Errors")] = 1
        with patch.dict("os.environ", function_environment(), clear=False):
            result = component_liveness(
                CloudWatch(values),
                datetime(2026, 8, 14, 4, 5, tzinfo=timezone.utc),
            )
        self.assertEqual(result["inventory"]["reason"], "NO_RECENT_INVOCATION")
        self.assertEqual(result["freeze"]["reason"], "RECENT_LAMBDA_ERRORS")
        self.assertFalse(result["inventory"]["healthy"])
        self.assertFalse(result["freeze"]["healthy"])

    def test_liveness_failure_overrides_a_promoted_champion(self) -> None:
        state = authority_state(
            model={"automatic_prediction_allowed": True},
            counts={"settlements": 1000},
            consecutive_failures=1,
            liveness_failed=True,
            validated_llm_missing=False,
        )
        self.assertEqual(state, ("DEGRADED", "SCHEDULED_COMPONENT_LIVENESS_FAILED"))

    def test_configured_llm_without_digest_validated_latest_fails_closed(self) -> None:
        observed = datetime(2026, 8, 14, 4, 5, tzinfo=timezone.utc)
        latest = {
            **validate_analysis({"summary": "valid", "recommended_trials": []}),
            "created_at": "2026-08-14T04:00:00Z",
            "expires_at": int(datetime(2026, 8, 15, tzinfo=timezone.utc).timestamp()),
        }

        class Ops:
            def get_item(self, **kwargs):
                return {"Item": latest}

        class Store:
            ops = Ops()

        with patch.dict(
            "os.environ",
            {"SOCCER_AUTO_LLM_MODEL_ID": "us.amazon.nova-2-lite-v1:0"},
            clear=False,
        ):
            self.assertTrue(_llm_state(Store(), observed)["fresh"])
            latest["summary"] = "tampered after validation"
            llm = _llm_state(Store(), observed)

        self.assertTrue(llm["configured"])
        self.assertFalse(llm["fresh"])
        state = authority_state(
            model={"automatic_prediction_allowed": True},
            counts={"settlements": 1000},
            consecutive_failures=0,
            liveness_failed=False,
            validated_llm_missing=True,
        )
        self.assertEqual(state, ("DEGRADED", "FRESH_VALIDATED_LLM_ANALYSIS_MISSING"))


if __name__ == "__main__":
    unittest.main()
