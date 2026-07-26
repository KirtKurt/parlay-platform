#!/usr/bin/env python3
"""Execute the first-five probe with a strict one-attempt-per-market cost bound."""
from __future__ import annotations

import run_mlb_v8_historical_first_five_probe as probe

# A retry could create a second billable historical request. The one-time probe
# therefore allows exactly one HTTP attempt for each of its 40 market requests.
probe.HTTP_RETRIES = 1


if __name__ == "__main__":
    raise SystemExit(probe.main())
