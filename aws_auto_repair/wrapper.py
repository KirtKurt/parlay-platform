"""Lambda entrypoint hardening for the AWS-native repair controller.

The target invocation client has a bounded synchronous read timeout and exactly
one total delivery attempt. A network timeout must never cause an automatic
SDK retry that could invoke the same sport controller twice.
"""
from __future__ import annotations

from typing import Any, Mapping

import boto3
from botocore.config import Config

try:
    from . import handler as core
except ImportError:
    import handler as core  # type: ignore

TARGET_INVOKE_CONFIG = Config(
    connect_timeout=5,
    read_timeout=840,
    retries={"mode": "standard", "total_max_attempts": 1},
)

# Discovery calls retain adaptive retries. Only the mutating target invocation
# client is restricted to one delivery attempt.
core.LAMBDA = boto3.client("lambda", config=TARGET_INVOKE_CONFIG)


def lambda_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    return core.lambda_handler(event, context)
