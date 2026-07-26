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

import run_mlb_v7_v8_today_test as runner  # noqa: E402

# Current canonical DynamoDB signal rows contain Decimal values. Preserve numeric
# types in the durable report instead of failing after the analysis has completed.
_original_dumps = runner.json.dumps


def _dumps(value, *args, **kwargs):
    kwargs.setdefault("default", runner._plain)
    return _original_dumps(value, *args, **kwargs)


runner.json.dumps = _dumps
main = runner.main


if __name__ == "__main__":
    raise SystemExit(main())
