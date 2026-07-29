#!/usr/bin/env python3
"""Operational entrypoint for the historical BBS fundamentals backfill.

The optional V8 fundamentals stack may not exist yet. Historical BBS learning does
not require that Lambda stack; it only requires an immutable S3 bucket. When the
isolated stack is absent, this adapter maps its expected bucket output to the live
historical optimizer's versioned artifacts bucket. BBS objects remain isolated by
their `mlb/v8/historical-bbs/` prefix and the shadow-only manifest pointer.
"""
from __future__ import annotations

import os
from typing import Any

from botocore.exceptions import ClientError

import run_mlb_v8_historical_bbs_backfill as backfill

VERSION = "MLB-V8-HISTORICAL-BBS-BUCKET-FALLBACK-v1"


def _stack_missing(exc: ClientError) -> bool:
    error = exc.response.get("Error") or {}
    return bool(
        str(error.get("Code") or "") == "ValidationError"
        and "does not exist" in str(error.get("Message") or exc).lower()
    )


def install_bucket_fallback(
    module: Any,
    *,
    historical_stack: str,
    fundamentals_stack: str,
) -> Any:
    """Use the historical artifacts bucket only when the optional V8 stack is absent."""
    if getattr(module, "_INQSI_HISTORICAL_BBS_BUCKET_FALLBACK_INSTALLED", False):
        return module
    original_outputs = module._outputs

    def outputs(cloudformation: Any, stack_name: str):
        try:
            return original_outputs(cloudformation, stack_name)
        except ClientError as exc:
            if stack_name != fundamentals_stack or not _stack_missing(exc):
                raise
            historical = original_outputs(cloudformation, historical_stack)
            bucket = str(historical.get("HistoricalArtifactsBucketName") or "").strip()
            if not bucket:
                raise RuntimeError(
                    "historical BBS fallback artifacts bucket output is missing"
                ) from exc
            return {
                "FundamentalsArtifactsBucketName": bucket,
                "HistoricalBbsManifestBucketSource": VERSION,
            }

    module._outputs = outputs
    module._INQSI_HISTORICAL_BBS_BUCKET_FALLBACK_INSTALLED = True
    return module


def main() -> int:
    install_bucket_fallback(
        backfill,
        historical_stack=os.environ.get(
            "HISTORICAL_STACK", backfill.DEFAULT_HISTORICAL_STACK
        ),
        fundamentals_stack=os.environ.get(
            "FUNDAMENTALS_STACK", backfill.DEFAULT_FUNDAMENTALS_STACK
        ),
    )
    return backfill.main()


if __name__ == "__main__":
    raise SystemExit(main())
