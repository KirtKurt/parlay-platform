import math

from nfl_auto.canonical import digest
from nfl_auto.features import FEATURE_NAMES
from nfl_auto.model import TrainingRow, season_split, select_candidate


def rows() -> list[TrainingRow]:
    output = []
    for season in range(2020, 2026):
        for index in range(220):
            signal = ((index * 17 + season) % 100) / 100.0 - 0.5
            prior = 0.5
            label = int(signal > 0)
            features = [0.0] * len(FEATURE_NAMES)
            features[0] = signal
            features[2] = signal * 0.7
            output.append(
                TrainingRow(
                    target="moneyline_home_win",
                    event_key=f"{season}_{index:03d}",
                    season=season,
                    week=index // 16 + 1,
                    kickoff_utc=f"{season}-09-{(index % 20) + 1:02d}T17:00:00Z",
                    features=tuple(features),
                    market_prior=prior,
                    label=label,
                    feature_hash=digest([season, index, features]),
                    bbd_digest=digest(["bbd", season, index]),
                    odds_digest=digest(["odds", season, index]),
                )
            )
    return output


def test_explicit_out_of_time_season_split_and_training() -> None:
    split = season_split(rows())
    assert {row.season for row in split.train} == {2020, 2021, 2022, 2023}
    assert {row.season for row in split.validation} == {2024}
    assert {row.season for row in split.audit} == {2025}
    model, report = select_candidate(split, "moneyline_home_win")
    assert model.target == "moneyline_home_win"
    assert report["audit"]["candidate"]["accuracy"] > 0.90
    assert report["audit"]["log_loss_skill"] > 0
    assert math.isfinite(report["audit_market_skill_lower_bound_95"])
    assert report["audit_market_skill_lower_bound_95"] > 0
