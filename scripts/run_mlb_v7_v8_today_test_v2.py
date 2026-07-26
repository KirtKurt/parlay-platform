#!/usr/bin/env python3
"""Install the exact extended V7 policy runtime, then run the one-time slate test."""
from __future__ import annotations

import mlb_historical_daily_optimizer_v1 as optimizer
import mlb_historical_derived_features_v1 as derived_features
import mlb_historical_policy_v1 as policy_runtime
import mlb_odds_pattern_features_v1 as odds_pattern_features

# Match the canonical historical optimizer entrypoint install order. These
# installers extend policy validation and live scoring for every field contained
# in the immutable round-5 V7 candidate artifact.
derived_features.install(optimizer, policy_runtime)
odds_pattern_features.install(optimizer, policy_runtime)

from run_mlb_v7_v8_today_test import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
