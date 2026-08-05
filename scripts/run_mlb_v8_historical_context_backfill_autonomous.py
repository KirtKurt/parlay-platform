#!/usr/bin/env python3
"""Run the official-only V8 context backfill without an optional stack dependency."""
from __future__ import annotations

import os
from typing import Any, Dict

from botocore.exceptions import ClientError

import run_mlb_v8_historical_context_backfill_entrypoint as official

VERSION = "MLB-V8-HISTORICAL-CONTEXT-AUTONOMY-v1-bucket-alias"


def _stack_missing(exc: ClientError) -> bool:
    error = exc.response.get("Error") or {}
    return bool(
        str(error.get("Code") or "") == "ValidationError"
        and "does not exist" in str(error.get("Message") or exc).lower()
    )


def install_artifact_bucket_alias(
    module: Any,
    *,
    historical_stack: str,
    fundamentals_stack: str,
) -> Any:
    """Map the absent optional fundamentals stack to the versioned history bucket."""

    if getattr(module, "_INQSI_MLB_V8_CONTEXT_BUCKET_ALIAS_INSTALLED", False):
        return module
    original_outputs = module._outputs

    def outputs(cloudformation: Any, stack_name: str) -> Dict[str, str]:
        try:
            values = dict(original_outputs(cloudformation, stack_name))
        except ClientError as exc:
            if stack_name != fundamentals_stack or not _stack_missing(exc):
                raise
            values = dict(original_outputs(cloudformation, historical_stack))
        if stack_name == fundamentals_stack:
            bucket = str(
                values.get("FundamentalsArtifactsBucketName")
                or values.get("HistoricalArtifactsBucketName")
                or ""
            ).strip()
            if not bucket:
                raise RuntimeError(
                    "V8 context artifacts bucket could not be resolved from the "
                    "fundamentals or historical stack"
                )
            values["FundamentalsArtifactsBucketName"] = bucket
            values["V8ContextArtifactsBucketResolution"] = VERSION
        return values

    module._outputs = outputs
    module._INQSI_MLB_V8_CONTEXT_BUCKET_ALIAS_INSTALLED = True
    return module


def install() -> Any:
    module = official.install()
    historical_stack = os.environ.get(
        "HISTORICAL_STACK", module.DEFAULT_HISTORICAL_STACK
    )
    fundamentals_stack = os.environ.get(
        "FUNDAMENTALS_STACK", module.DEFAULT_FUNDAMENTALS_STACK
    )
    install_artifact_bucket_alias(
        module,
        historical_stack=historical_stack,
        fundamentals_stack=fundamentals_stack,
    )
    return module


def main() -> int:
    return install().main()


if __name__ == "__main__":
    raise SystemExit(main())
