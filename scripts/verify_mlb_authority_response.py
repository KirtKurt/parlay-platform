#!/usr/bin/env python3
"""Verify the public MLB authority boundary without permitting retired fallback.

Exactly two runtime states are valid:

* explicit ``NO_QUALIFIED_CHAMPION`` with HTTP 503 and publication closed; or
* HTTP 200 with a qualified, deployed R7 champion and no legacy authority.

The verifier is safe for deployment capacity probes because an intentional 503
fail-closed response proves the Lambda is reachable and enforcing the contract.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

VERSION = "MLB-R7-PUBLIC-AUTHORITY-READ-VERIFIER-v1"
AUTHORITY_CONTRACT = "MLB-AUTO-R7-QUALIFIED-CHAMPION-ONLY-v1"
RETIRED_MARKERS = ("v15.10", "v15_10", "15.10", "ranked-winner")


def _contains_retired_marker(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, Mapping):
        return any(
            _contains_retired_marker(key) or _contains_retired_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_retired_marker(item) for item in value)
    text = str(value).lower()
    return any(marker in text for marker in RETIRED_MARKERS)


def _nonempty(value: Any) -> bool:
    if isinstance(value, Mapping):
        return bool(value)
    return bool(str(value or "").strip())


def verify_payload(http_status: int, payload: Any) -> Dict[str, Any]:
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return {
            "ok": False,
            "version": VERSION,
            "state": "INVALID",
            "httpStatus": int(http_status),
            "errors": ["response_body_not_object"],
        }

    body = dict(payload)
    contract = body.get("authorityContractVersion")
    common_false = {
        "legacyFallbackAllowed": False,
        "automaticLegacyRestoreAllowed": False,
        "legacyRecommendationAuthority": False,
        "retiredV15_10Eligible": False,
    }
    for field, expected in common_false.items():
        if body.get(field) is not expected:
            errors.append(f"{field}_must_be_false")
    if body.get("retiredAuthoritySuppressed") is not True:
        errors.append("retiredAuthoritySuppressed_must_be_true")
    if contract != AUTHORITY_CONTRACT:
        errors.append("authority_contract_version_mismatch")

    authority_projection = {
        key: body.get(key)
        for key in (
            "model_version",
            "primaryAlgorithm",
            "soleProductionAlgorithm",
            "game_winner_model",
            "productionAuthoritySource",
            "requestedAuthority",
            "r7DeploymentIdentity",
        )
    }
    if _contains_retired_marker(authority_projection):
        errors.append("retired_v15_10_authority_marker_present")

    no_champion = (
        int(http_status) == 503
        and body.get("ok") is False
        and body.get("status") == "NO_QUALIFIED_CHAMPION"
        and body.get("error") == "NO_QUALIFIED_CHAMPION"
    )
    qualified = int(http_status) == 200 and body.get("ok") is True

    if no_champion:
        state = "NO_QUALIFIED_CHAMPION"
        expected = {
            "publicationClosed": True,
            "productionSelectionAllowed": False,
            "qualifiedChampionRequired": True,
            "qualifiedChampionPresent": False,
            "r7ChampionQualified": False,
            "primaryAlgorithmActive": False,
            "automaticWagerAllowed": False,
            "rowLevelAutomaticWagerAllowed": False,
        }
        for field, value in expected.items():
            if body.get(field) is not value:
                errors.append(f"{field}_mismatch_for_no_champion")
        for field in (
            "model_version",
            "primaryAlgorithm",
            "soleProductionAlgorithm",
            "game_winner_model",
            "r7DeploymentIdentity",
        ):
            if body.get(field) not in (None, ""):
                errors.append(f"{field}_must_be_empty_without_champion")
        for field in ("winner_predictions", "predictions"):
            if field in body and body.get(field) not in (None, []):
                errors.append(f"{field}_must_be_empty_without_champion")
    elif qualified:
        state = "QUALIFIED_R7_CHAMPION"
        expected = {
            "publicationClosed": False,
            "productionSelectionAllowed": True,
            "qualifiedChampionRequired": True,
            "qualifiedChampionPresent": True,
            "r7ChampionQualified": True,
            "primaryAlgorithmActive": True,
        }
        for field, value in expected.items():
            if body.get(field) is not value:
                errors.append(f"{field}_mismatch_for_qualified_r7")
        if body.get("requestedAuthority") != "AWS_ML_PROSPECTIVE_R7":
            errors.append("qualified_authority_is_not_aws_ml_prospective_r7")
        for field in ("model_version", "primaryAlgorithm", "r7DeploymentIdentity"):
            if not _nonempty(body.get(field)):
                errors.append(f"{field}_missing_for_qualified_r7")
    else:
        state = "INVALID"
        errors.append("http_and_body_do_not_match_an_allowed_authority_state")

    return {
        "ok": not errors,
        "version": VERSION,
        "authorityContractVersion": contract,
        "state": state,
        "httpStatus": int(http_status),
        "publicationClosed": body.get("publicationClosed"),
        "productionSelectionAllowed": body.get("productionSelectionAllowed"),
        "qualifiedChampionPresent": body.get("qualifiedChampionPresent"),
        "r7ChampionQualified": body.get("r7ChampionQualified"),
        "modelIdentifier": body.get("model_version"),
        "primaryAlgorithm": body.get("primaryAlgorithm"),
        "r7DeploymentIdentity": body.get("r7DeploymentIdentity"),
        "retiredAuthoritySuppressed": body.get("retiredAuthoritySuppressed"),
        "retiredV15_10Eligible": body.get("retiredV15_10Eligible"),
        "errors": sorted(set(errors)),
    }


def fetch_json(url: str, timeout_seconds: float) -> Tuple[int, Dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "inqsi-mlb-r7-authority-verifier/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("response_body_not_object")
    return status, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url")
    parser.add_argument("--input")
    parser.add_argument("--http-status", type=int)
    parser.add_argument("--output")
    parser.add_argument("--request-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if bool(args.url) == bool(args.input):
        parser.error("provide exactly one of --url or --input")
    if args.url:
        status, payload = fetch_json(args.url, args.request_timeout_seconds)
    else:
        if args.http_status is None:
            parser.error("--http-status is required with --input")
        status = int(args.http_status)
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))

    report = verify_payload(status, payload)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    if not args.quiet or report.get("ok") is not True:
        sys.stdout.write(encoded)
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
