from nfl_auto.canonical import digest
from nfl_auto.features import FEATURE_NAMES
from nfl_auto.model import TrainingRow, adaptive_split


def make_row(season: int, index: int) -> TrainingRow:
    features = [0.0] * len(FEATURE_NAMES)
    features[0] = (index % 10) / 10
    return TrainingRow(
        target="total_over",
        event_key=f"{season}_{index:03d}",
        season=season,
        week=index // 16 + 1,
        kickoff_utc=f"{season}-09-{(index % 20) + 1:02d}T17:00:00Z",
        features=tuple(features),
        market_prior=0.5,
        label=index % 2,
        feature_hash=digest([season, index]),
        bbd_digest=digest(["bbd", season, index]),
        odds_digest=digest(["odds", season, index]),
    )


def test_historical_audit_is_preserved_before_live_sample_threshold() -> None:
    rows = [make_row(season, index) for season in range(2020, 2026) for index in range(10)]
    rows.extend(make_row(2026, index) for index in range(80))
    split, mode = adaptive_split(rows, min_live_rows=144, live_validation_rows=48, live_audit_rows=48)
    assert mode == "HISTORICAL_2025_AUDIT"
    assert {row.season for row in split.audit} == {2025}


def test_live_rows_eventually_become_prospective_validation_and_audit() -> None:
    rows = [make_row(season, index) for season in range(2020, 2026) for index in range(10)]
    rows.extend(make_row(2026, index) for index in range(160))
    split, mode = adaptive_split(rows, min_live_rows=144, live_validation_rows=48, live_audit_rows=48)
    assert mode == "LIVE_EXPANDING_PROSPECTIVE_AUDIT"
    assert len(split.validation) == 48
    assert len(split.audit) == 48
    assert {row.season for row in split.validation} == {2026}
    assert {row.season for row in split.audit} == {2026}
    assert any(row.season == 2026 for row in split.train)
