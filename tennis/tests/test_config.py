from __future__ import annotations

import pytest

from config import TennisConfig


def test_runtime_rejects_decimal_odds_for_american_signal_math():
    config = TennisConfig(
        odds_api_key="test-key",
        snapshots_table="snapshots",
        signals_table="signals",
        archive_bucket="archive",
        odds_format="decimal",
    )

    with pytest.raises(RuntimeError, match="requires american odds"):
        config.validate_runtime()


@pytest.mark.parametrize("seconds", [240, 841])
def test_runtime_requires_a_bounded_lease_longer_than_lambda_timeout(seconds):
    config = TennisConfig(
        odds_api_key="test-key",
        snapshots_table="snapshots",
        signals_table="signals",
        archive_bucket="archive",
        slot_lease_seconds=seconds,
    )

    with pytest.raises(RuntimeError, match="between 241 and 840"):
        config.validate_runtime()
