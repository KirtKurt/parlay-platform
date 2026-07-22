"""Manual-only Lambda entry point for the fixed BBS tennis capability probe."""

from __future__ import annotations

import os
from typing import Any, Dict

from bbs_results_provider import BBSResultsProvider


EXPECTED_EVENT = {
    "provider": "bbs",
    "sport": "tennis",
    "action": "probe",
}


def _build_provider() -> BBSResultsProvider:
    return BBSResultsProvider(secret_arn=os.environ.get("TENNIS_BBS_API_SECRET_ARN"))


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    del context
    if not isinstance(event, dict) or event != EXPECTED_EVENT:
        raise RuntimeError("TENNIS_RESULTS_PROBE_EVENT_REJECTED")
    provider = _build_provider()
    return provider.probe_capabilities().to_dict()


__all__ = ["EXPECTED_EVENT", "lambda_handler"]
