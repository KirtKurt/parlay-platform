#!/usr/bin/env python3
"""Run V8 target-context reconstruction with live BBD explicitly unavailable.

Stored immutable BBD manifests may still be consumed by downstream learners, but
this backfill makes zero BBD HTTP requests.  Provider discovery degrades to the
canonical official MLB identity, then MLB Stats API and Open-Meteo reconstruct the
point-in-time target-game context.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import run_mlb_v8_historical_bbs_backfill as backfill
import run_mlb_v8_historical_context_backfill_entrypoint as entrypoint

VERSION = "MLB-V8-HISTORICAL-CONTEXT-NO-BBD-v1"


class BbdUnavailableClient:
    """Fail-fast capability stub; it never opens a network connection."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._api_disabled = True

    def list_mlb_matches(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise backfill.BBSClientError("BBS_API_UNAVAILABLE_EXPLICIT")

    def get_mlb_match_resource(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise backfill.BBSClientError("BBS_API_UNAVAILABLE_EXPLICIT")

    def _request(self, *args: Any, **kwargs: Any):
        raise backfill.BBSClientError("BBS_API_UNAVAILABLE_EXPLICIT")

    @staticmethod
    def _transport(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {
            "endpoint": kwargs.get("endpoint"),
            "requestedDate": kwargs.get("requested_date"),
            "requestedAsOfUtc": kwargs.get("requested_as_of"),
            "providerAvailable": False,
        }


def _argument(name: str) -> str | None:
    try:
        index = sys.argv.index(name)
    except ValueError:
        return None
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


def _install() -> None:
    os.environ["BBS_API_DISABLED"] = "true"
    os.environ.pop("BBS_API_KEY", None)
    os.environ.pop("BBS_API_SECRET_ARN", None)
    backfill.BigBallsDataClient = BbdUnavailableClient
    defaults = dict(getattr(backfill.run, "__kwdefaults__", {}) or {})
    defaults["client_factory"] = BbdUnavailableClient
    backfill.run.__kwdefaults__ = defaults


def _decorate(path: Path) -> None:
    if not path.exists() or not path.stat().st_size:
        return
    report = json.loads(path.read_text())
    logical_calls = int(report.get("providerCallsMade") or 0)
    report.update(
        {
            "noBbdModeVersion": VERSION,
            "provider": "official_mlb_stats_api_plus_open_meteo_archive",
            "providerCapability": "BBD_API_UNAVAILABLE_EXPLICIT",
            "liveBbdApiAvailable": False,
            "liveBbdApiRequired": False,
            "liveBbdHttpRequestsMade": 0,
            "logicalProviderSurfaceCalls": logical_calls,
            "officialFallbackRequired": True,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
            "selectionUsedOutcomes": False,
            "targetGameOutcomeUsed": False,
            "sameDayResultsExcluded": True,
        }
    )
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def main() -> int:
    _install()
    code = entrypoint.main()
    output = _argument("--output")
    if output:
        _decorate(Path(output))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
