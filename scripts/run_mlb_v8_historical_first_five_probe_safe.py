#!/usr/bin/env python3
"""Execute the first-five probe with strict cost and evidence safeguards."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

import run_mlb_v8_historical_first_five_probe as probe

# A retry could create a second billable historical request. The one-time probe
# therefore allows exactly one HTTP attempt for each of its 40 market requests.
probe.HTTP_RETRIES = 1

# The base probe derives a semantic artifact digest before pretty-printing the
# JSON body. Override only the storage helper so the final immutable S3 key is
# addressed by the exact bytes actually written.
_original_immutable_put = probe._immutable_put


def _exact_content_addressed_put(s3, bucket, _key, body):
    digest = hashlib.sha256(body).hexdigest()
    key = f"mlb/v8/historical-first-five-probes/{digest}.json"
    return _original_immutable_put(s3, bucket, key, body)


probe._immutable_put = _exact_content_addressed_put


def _redact(message: str) -> str:
    value = str(message)
    secret = str(os.environ.get("ODDS_API_KEY") or "")
    if secret:
        value = value.replace(secret, "[REDACTED]")
    # requests exceptions can include a URL-encoded query string. Redact the
    # parameter independently so an unusual API-key character cannot leak.
    return re.sub(r"(?i)(apiKey=)[^&\s\"']+", r"\1[REDACTED]", value)


def main() -> int:
    try:
        return int(probe.main())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": _redact(f"{type(exc).__name__}:{exc}"),
                    "authority": "SHADOW_ONLY",
                    "trainingEligible": False,
                    "productionAuthorityChanged": False,
                    "automaticWagerAllowed": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
