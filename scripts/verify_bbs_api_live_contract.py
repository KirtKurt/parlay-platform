#!/usr/bin/env python3
"""Authenticate BBS and prove its minimal MLB envelope without leaking secrets.

This is a deployment preflight, not a claim that BBS fundamentals are model-ready.
The report intentionally excludes response rows, account email, and key material.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


BASE_URL = "https://api.bigballsdata.com"
SCHEMA_VERSION = "MLB-BBS-LIVE-CONTRACT-v2-live-key-documented-row-shape"
LIVE_KEY_PATTERN = re.compile(r"^bbs_live_[A-Za-z0-9_-]{12,}$")
TEST_KEY_PATTERN = re.compile(r"^bbs_test_[A-Za-z0-9_-]{12,}$")


class LiveContractError(RuntimeError):
    """A redacted BBS authentication or response-contract failure."""


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _schema_fingerprint(value: Any) -> str:
    """Hash shape only; never persist values returned by the account endpoint."""

    def shape(node: Any) -> Any:
        if isinstance(node, dict):
            return {str(key): shape(child) for key, child in sorted(node.items())}
        if isinstance(node, list):
            variants = sorted(
                {json.dumps(shape(child), sort_keys=True, separators=(",", ":")) for child in node[:20]}
            )
            return {"type": "array", "variants": variants}
        return _json_type(node)

    encoded = json.dumps(shape(value), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request_json(
    path: str,
    *,
    api_key: str,
    opener: Callable[..., Any],
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "inqsi-mlb-bbs-contract/1.0",
        },
        method="GET",
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", response.getcode()))
            headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise LiveContractError(f"BBS_AUTH_REJECTED_HTTP_{exc.code}") from None
        if exc.code == 429:
            raise LiveContractError("BBS_RATE_LIMITED") from None
        raise LiveContractError(f"BBS_HTTP_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LiveContractError("BBS_NETWORK_UNAVAILABLE") from None

    if status != 200:
        raise LiveContractError(f"BBS_UNEXPECTED_HTTP_{status}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LiveContractError("BBS_RESPONSE_NOT_JSON") from None
    if not isinstance(payload, dict):
        raise LiveContractError("BBS_RESPONSE_NOT_OBJECT")
    if not {"data", "meta", "error"}.issubset(payload):
        raise LiveContractError("BBS_STANDARD_ENVELOPE_INCOMPLETE")
    if payload.get("error") is not None:
        raise LiveContractError("BBS_ENVELOPE_REPORTED_ERROR")
    if not isinstance(payload.get("meta"), dict):
        raise LiveContractError("BBS_META_NOT_OBJECT")
    return payload, headers


def verify(
    api_key: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    if TEST_KEY_PATTERN.fullmatch(api_key or ""):
        raise LiveContractError("BBS_PRODUCTION_LIVE_KEY_REQUIRED")
    if not api_key or not LIVE_KEY_PATTERN.fullmatch(api_key):
        raise LiveContractError("BBS_API_KEY_MISSING_OR_MALFORMED")

    account, account_headers = _request_json(
        "/v1/user/me",
        api_key=api_key,
        opener=opener,
        timeout_seconds=timeout_seconds,
    )
    account_data = account.get("data")
    if not isinstance(account_data, dict):
        raise LiveContractError("BBS_ACCOUNT_DATA_NOT_OBJECT")
    if account_data.get("paused") is True:
        raise LiveContractError("BBS_ACCOUNT_PAUSED")

    query = urllib.parse.urlencode(
        {
            "sport": "baseball",
            "league": "mlb",
            "date": "today",
            "limit": 50,
        }
    )
    matches, match_headers = _request_json(
        f"/v1/matches?{query}",
        api_key=api_key,
        opener=opener,
        timeout_seconds=timeout_seconds,
    )
    match_data = matches.get("data")
    if not isinstance(match_data, list):
        raise LiveContractError("BBS_MLB_MATCH_DATA_NOT_ARRAY")
    for row in match_data:
        if not isinstance(row, dict):
            raise LiveContractError("BBS_MLB_MATCH_ROW_NOT_OBJECT")
        if not str(row.get("match_id") or "").strip():
            raise LiveContractError("BBS_MLB_MATCH_ID_MISSING")
        kickoff = str(row.get("kickoff_utc") or "")
        try:
            parsed_kickoff = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise LiveContractError("BBS_MLB_KICKOFF_UTC_INVALID") from None
        if parsed_kickoff.tzinfo is None:
            raise LiveContractError("BBS_MLB_KICKOFF_UTC_INVALID")
        if str(row.get("sport") or "").lower() != "baseball":
            raise LiveContractError("BBS_MLB_SPORT_FIELD_INVALID")
        for side in ("home", "away"):
            team = row.get(side)
            if not isinstance(team, dict):
                raise LiveContractError(f"BBS_MLB_{side.upper()}_TEAM_INVALID")
            if not str(
                team.get("display_name")
                or team.get("name")
                or team.get("team_name")
                or ""
            ).strip():
                raise LiveContractError(f"BBS_MLB_{side.upper()}_TEAM_NAME_MISSING")

    meta = matches.get("meta") or {}
    allowed_source = {
        "official-league",
        "aggregator-paid",
        "aggregator-free",
        "community-scraper",
        "mixed",
        "cache",
        "none",
        "unknown-source",
    }
    if meta.get("source") not in allowed_source:
        raise LiveContractError("BBS_MLB_SOURCE_ATTRIBUTION_INVALID")

    rate_limit = match_headers.get("x-ratelimit-limit") or account_headers.get("x-ratelimit-limit")
    rate_remaining = match_headers.get("x-ratelimit-remaining") or account_headers.get("x-ratelimit-remaining")
    return {
        "ok": True,
        "contractVersion": SCHEMA_VERSION,
        "verifiedAtUtc": datetime.now(timezone.utc).isoformat(),
        "authentication": "VERIFIED_REDACTED",
        "keyTransport": "AUTHORIZATION_BEARER",
        "keyEnvironmentName": "BBS_API_KEY",
        "account": {
            "active": account_data.get("paused") is not True,
            "plan": str(account_data.get("plan") or "unknown"),
            "githubConnected": account_data.get("github_connected") is True,
            "schemaFingerprint": _schema_fingerprint(account_data),
        },
        "mlbMatches": {
            "endpoint": "/v1/matches",
            "filters": {"sport": "baseball", "league": "mlb", "date": "today"},
            "rowCount": len(match_data),
            "dataType": "array",
            "documentedRowSchemaValidated": bool(match_data),
            "providerOfficialGameIdentityDocumented": False,
            "source": meta.get("source"),
            "confidence": meta.get("confidence"),
            "schemaFingerprint": _schema_fingerprint(match_data),
        },
        "rateLimit": {"limit": rate_limit, "remaining": rate_remaining},
        "activation": {
            "mode": "SHADOW_ONLY",
            "predictionAuthority": False,
            "trainingEligibility": False,
            "completenessCredit": False,
            "captureCoverage": "PARTIAL_SINGLE_UTC_DATE_PROBE",
            "completeSlateCoverageClaimed": False,
            "reviewMilestoneDefined": False,
            "officialIdentityCredit": False,
            "providerIdentityGateSatisfied": False,
        },
        "secretExposed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=15)
    args = parser.parse_args()

    try:
        report = verify(
            os.environ.get("BBS_API_KEY", ""),
            timeout_seconds=args.timeout_seconds,
        )
    except LiveContractError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "secretExposed": False}, sort_keys=True))
        return 1

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
