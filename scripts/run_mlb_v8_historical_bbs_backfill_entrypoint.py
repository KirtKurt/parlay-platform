#!/usr/bin/env python3
"""Operational entrypoint for the historical BBS fundamentals backfill.

The optional V8 fundamentals stack may not exist yet. Historical BBS learning does
not require that Lambda stack; it only requires an immutable S3 bucket. When the
isolated stack is absent, this adapter maps its expected bucket output to the live
historical optimizer's versioned artifacts bucket. Historical game discovery uses
BBS's stored-match surface and begins with the newest canonical games because the
provider's current MLB archive is 2026-first rather than complete for early 2025.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from botocore.exceptions import ClientError

import run_mlb_v8_historical_bbs_backfill as backfill

VERSION = "MLB-V8-HISTORICAL-BBS-OPERATIONAL-ENTRYPOINT-v3-coverage-window"


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


def install_stored_match_surface(client_class: Any) -> Any:
    """Force historical discovery through `/v1/stored/matches`."""
    if getattr(client_class, "_INQSI_HISTORICAL_BBS_STORED_MATCHES_INSTALLED", False):
        return client_class
    original = client_class.list_mlb_matches

    def list_stored(
        self: Any,
        game_date: str,
        *,
        limit: int = 50,
        as_of: str | None = None,
        stored: bool = False,
    ):
        return original(
            self,
            game_date,
            limit=limit,
            as_of=as_of,
            stored=True,
        )

    client_class.list_mlb_matches = list_stored
    client_class._INQSI_HISTORICAL_BBS_STORED_MATCHES_INSTALLED = True
    return client_class


def install_newest_coverage_window(module: Any) -> Any:
    """Probe the newest unprocessed games first without consulting outcomes.

    The immutable corpus loader is chronological ascending. Reversing only its
    traversal order lets the existing `pending[:limit]` selection reach the current
    provider coverage window. All identity, lock, and outcome-exclusion gates remain
    unchanged, and the final manifest still sorts records chronologically.
    """
    if getattr(module, "_INQSI_HISTORICAL_BBS_NEWEST_WINDOW_INSTALLED", False):
        return module
    original = module._load_canonical_games

    def newest_first(state: Mapping[str, Any], s3: Any):
        return list(reversed(original(state, s3)))

    module._load_canonical_games = newest_first
    module._INQSI_HISTORICAL_BBS_NEWEST_WINDOW_INSTALLED = True
    return module


def install_safe_diagnostics(module: Any) -> Any:
    """Add value-free coverage and eligibility counts to the durable report."""
    if getattr(module, "_INQSI_HISTORICAL_BBS_DIAGNOSTICS_INSTALLED", False):
        return module

    original_crosswalk = module.crosswalk_provider_rows
    original_snapshot = module.build_training_snapshot
    original_run = module.run
    discovery: list[dict[str, Any]] = []
    eligibility_errors: Counter[str] = Counter()

    def crosswalk(
        provider_rows: Sequence[Mapping[str, Any]],
        canonical_games: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ):
        result = original_crosswalk(provider_rows, canonical_games, **kwargs)
        day = str(canonical_games[0].get("slateDateEt") or "") if canonical_games else ""
        discovery.append(
            {
                "slateDateEt": day,
                "providerRowCount": len(provider_rows),
                "canonicalGameCount": len(canonical_games),
                "acceptedCrosswalkCount": int(result.get("acceptedCount") or 0),
                "quarantinedProviderRowCount": int(result.get("quarantinedCount") or 0),
                "unmatchedCanonicalGameCount": max(
                    0,
                    len(canonical_games) - int(result.get("acceptedCount") or 0),
                ),
            }
        )
        return result

    def snapshot(*args: Any, **kwargs: Any):
        value = original_snapshot(*args, **kwargs)
        for error in value.get("eligibilityErrors") or []:
            eligibility_errors[str(error)] += 1
        return value

    def run(*args: Any, **kwargs: Any):
        discovery.clear()
        eligibility_errors.clear()
        report = original_run(*args, **kwargs)
        report["selectionOrder"] = "newest_unprocessed_canonical_games_first"
        report["providerDiscovery"] = list(discovery)
        report["providerDiscoveryDateCount"] = len(discovery)
        report["providerRowsReturned"] = sum(
            int(row.get("providerRowCount") or 0) for row in discovery
        )
        report["acceptedCrosswalkCount"] = sum(
            int(row.get("acceptedCrosswalkCount") or 0) for row in discovery
        )
        report["unmatchedCanonicalGameCount"] = sum(
            int(row.get("unmatchedCanonicalGameCount") or 0) for row in discovery
        )
        report["eligibilityErrorCounts"] = dict(sorted(eligibility_errors.items()))
        report["diagnosticsContainProviderValues"] = False
        output = kwargs.get("output")
        if isinstance(output, Path):
            output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report

    module.crosswalk_provider_rows = crosswalk
    module.build_training_snapshot = snapshot
    module.run = run
    module._INQSI_HISTORICAL_BBS_DIAGNOSTICS_INSTALLED = True
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
    install_stored_match_surface(backfill.BigBallsDataClient)
    install_newest_coverage_window(backfill)
    install_safe_diagnostics(backfill)
    return backfill.main()


if __name__ == "__main__":
    raise SystemExit(main())
