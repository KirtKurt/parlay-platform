from __future__ import annotations

from datetime import datetime, timezone
import math

from config import TennisConfig
from signal_engine import (
    american_implied_probability,
    build_feature_vector,
    directional_signal,
    no_vig_pair,
)


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def config() -> TennisConfig:
    return TennisConfig(
        odds_api_key="test-key",
        snapshots_table="snapshots",
        signals_table="signals",
    )


def test_two_way_no_vig_probabilities_sum_to_one():
    pair = no_vig_pair(-120, 110)

    assert pair is not None
    assert round(pair[0] + pair[1], 12) == 1.0


def test_non_finite_prices_are_rejected():
    assert american_implied_probability(math.nan) is None
    assert american_implied_probability(math.inf) is None
    assert american_implied_probability(-math.inf) is None
    assert no_vig_pair(math.nan, -110) is None


def test_existing_steam_and_guarded_score_are_layered_in():
    series = [
        {
            "observed_at_utc": "2026-07-22T10:00:00+00:00",
            "probabilities": {
                "player_a": 0.50,
                "player_b": 0.50,
                "book_count": 3,
                "book_divergence": 0.01,
            },
        },
        {
            "observed_at_utc": "2026-07-22T10:15:00+00:00",
            "probabilities": {
                "player_a": 0.53,
                "player_b": 0.47,
                "book_count": 3,
                "book_divergence": 0.01,
            },
        },
        {
            "observed_at_utc": "2026-07-22T10:30:00+00:00",
            "probabilities": {
                "player_a": 0.57,
                "player_b": 0.43,
                "book_count": 3,
                "book_divergence": 0.01,
            },
        },
    ]

    signal = directional_signal(series, "player_a")

    assert "STEAM" in signal["tags"]
    assert "MOMENTUM" in signal["tags"]
    assert "CERTAINTY_ANCHOR" in signal["tags"]
    assert signal["grade"] == "STRONG_SOLID"
    assert 0 <= signal["market_signal_score"] <= 100
    assert signal["score_guard_applied"] is True


def snapshot(slot: str, commence: str, prices):
    books = {
        key: {"player_a": pair[0], "player_b": pair[1]} for key, pair in prices.items()
    }
    return {
        "observed_at_utc": slot,
        "data": {
            "event_id": "event-1",
            "commence_time": commence,
            "player_a": "Player A",
            "player_b": "Player B",
            "tournament_key": "tennis_atp_test",
            "books": books,
        },
    }


def current_event(commence: str):
    return {
        "event_id": "event-1",
        "slate_date_et": "2026-07-22",
        "commence_time": commence,
        "player_a": "Player A",
        "player_b": "Player B",
        "tour": "ATP",
        "discipline": "singles",
        "tournament_key": "tennis_atp_test",
        "tournament_title": "ATP Test",
    }


def test_feature_vector_excludes_post_start_observation_and_is_not_a_pick():
    commence = "2026-07-22T18:00:00+00:00"
    rows = [
        snapshot(
            "2026-07-22T17:30:00+00:00",
            commence,
            {"a": (-110, -110), "b": (-105, -115), "c": (-108, -112)},
        ),
        snapshot(
            "2026-07-22T17:45:00+00:00",
            commence,
            {"a": (-125, 105), "b": (-120, 100), "c": (-122, 102)},
        ),
        snapshot(
            "2026-07-22T18:01:00+00:00",
            commence,
            {"a": (-300, 240), "b": (-290, 230), "c": (-280, 220)},
        ),
    ]

    feature = build_feature_vector(
        rows,
        current_event(commence),
        datetime.fromisoformat("2026-07-22T17:45:00+00:00").astimezone(timezone.utc),
        config(),
    )

    assert feature["data_quality"]["prematch_snapshot_count"] == 2
    assert feature["data_quality"]["valid_signal_pull_count"] == 2
    assert feature["market_signal_score"] is not None
    assert feature["model_probability"] is None
    assert feature["prediction_eligible"] is False
    assert feature["model_state"] == "RULE_BASED_SHADOW"
    assert "TRAP" in feature["unavailable_signals"]


def test_one_common_book_fails_closed_without_a_signal_score():
    commence = "2026-07-22T18:00:00+00:00"
    rows = [
        snapshot(
            "2026-07-22T17:30:00+00:00",
            commence,
            {"only": (-110, -110)},
        ),
        snapshot(
            "2026-07-22T17:45:00+00:00",
            commence,
            {"only": (-120, 100)},
        ),
    ]

    feature = build_feature_vector(
        rows,
        current_event(commence),
        datetime.fromisoformat("2026-07-22T17:45:00+00:00").astimezone(timezone.utc),
        config(),
    )

    assert feature["research_status"] == "INSUFFICIENT_MARKET_COVERAGE"
    assert feature["market_signal_score"] is None
    assert feature["prediction_eligible"] is False


def test_provider_side_flip_is_not_mixed_into_the_same_player_series():
    commence = "2026-07-22T18:00:00+00:00"
    first = snapshot(
        "2026-07-22T17:30:00+00:00",
        commence,
        {"a": (-110, -110), "b": (-105, -115)},
    )
    flipped = snapshot(
        "2026-07-22T17:45:00+00:00",
        commence,
        {"a": (-120, 100), "b": (-118, -102)},
    )
    flipped["data"]["player_a"] = "Player B"
    flipped["data"]["player_b"] = "Player A"

    feature = build_feature_vector(
        [first, flipped],
        current_event(commence),
        datetime.fromisoformat("2026-07-22T17:45:00+00:00").astimezone(timezone.utc),
        config(),
    )

    assert feature["data_quality"]["prematch_snapshot_count"] == 1
    assert feature["market_signal_score"] is None
    assert feature["prediction_eligible"] is False


def test_empty_market_pull_does_not_poison_later_covered_history():
    commence = "2026-07-22T18:00:00+00:00"
    rows = [
        snapshot("2026-07-22T17:15:00+00:00", commence, {}),
        snapshot(
            "2026-07-22T17:30:00+00:00",
            commence,
            {"a": (-110, -110), "b": (-105, -115), "c": (-108, -112)},
        ),
        snapshot(
            "2026-07-22T17:45:00+00:00",
            commence,
            {"a": (-125, 105), "b": (-120, 100), "c": (-122, 102)},
        ),
    ]

    feature = build_feature_vector(
        rows,
        current_event(commence),
        dt("2026-07-22T17:45:00+00:00"),
        config(),
    )

    assert feature["data_quality"]["prematch_snapshot_count"] == 3
    assert feature["data_quality"]["low_or_no_book_snapshot_count"] == 1
    assert feature["data_quality"]["valid_signal_pull_count"] == 2
    assert feature["market_signal_score"] is not None


def test_feature_builder_enforces_as_of_cutoff_for_replay_safety():
    commence = "2026-07-22T18:00:00+00:00"
    rows = [
        snapshot(
            "2026-07-22T10:00:00+00:00",
            commence,
            {"a": (-110, -110), "b": (-105, -115)},
        ),
        snapshot(
            "2026-07-22T10:15:00+00:00",
            commence,
            {"a": (-115, -105), "b": (-110, -110)},
        ),
        snapshot(
            "2026-07-22T10:30:00+00:00",
            commence,
            {"a": (-300, 240), "b": (-290, 230)},
        ),
    ]

    feature = build_feature_vector(
        rows,
        current_event(commence),
        dt("2026-07-22T10:15:00+00:00"),
        config(),
    )

    assert feature["data_quality"]["prematch_snapshot_count"] == 2
    assert feature["data_quality"]["valid_signal_pull_count"] == 2


def test_feature_builder_fails_closed_at_match_start():
    commence = "2026-07-22T18:00:00+00:00"
    rows = [
        snapshot(
            "2026-07-22T17:30:00+00:00",
            commence,
            {"a": (-110, -110), "b": (-105, -115)},
        ),
        snapshot(
            "2026-07-22T17:45:00+00:00",
            commence,
            {"a": (-120, 100), "b": (-118, -102)},
        ),
    ]

    feature = build_feature_vector(
        rows,
        current_event(commence),
        dt(commence),
        config(),
    )

    assert feature["research_status"] == "MATCH_STARTED_NO_FEATURE"
    assert feature["market_signal_score"] is None
    assert feature["prediction_eligible"] is False


def test_guarded_score_tie_uses_raw_score_before_latest_probability():
    commence = "2026-07-22T18:00:00+00:00"
    rows = []
    for index in range(12):
        probability_a = 0.44 + (0.01 * index / 11)
        player_a = 100.0 * (1.0 - probability_a) / probability_a
        player_b = -100.0 * (1.0 - probability_a) / probability_a
        rows.append(
            snapshot(
                f"2026-07-22T{10 + index // 4:02d}:{(index % 4) * 15:02d}:00+00:00",
                commence,
                {
                    "a": (player_a, player_b),
                    "b": (player_a, player_b),
                    "c": (player_a, player_b),
                },
            )
        )

    feature = build_feature_vector(
        rows,
        current_event(commence),
        dt("2026-07-22T12:45:00+00:00"),
        config(),
    )

    assert feature["selected_side"] == "player_a"
    assert feature["selection_tiebreaker"] == "raw_score_before_guard"
    assert feature["research_status"] == "SHADOW_FEATURE_READY"


def test_exact_even_market_has_no_selected_side():
    commence = "2026-07-22T18:00:00+00:00"
    rows = [
        snapshot(
            f"2026-07-22T{10 + index // 4:02d}:{(index % 4) * 15:02d}:00+00:00",
            commence,
            {"a": (-110, -110), "b": (-105, -105), "c": (-115, -115)},
        )
        for index in range(12)
    ]

    feature = build_feature_vector(
        rows,
        current_event(commence),
        dt("2026-07-22T12:45:00+00:00"),
        config(),
    )

    assert feature["selected_side"] is None
    assert feature["selected_player"] is None
    assert feature["selection_tiebreaker"] == "no_selection_exact_tie"
    assert feature["research_status"] == "PASS_TIED_SIGNAL"


def test_publish_readiness_requires_three_books_on_latest_pull():
    commence = "2026-07-22T18:00:00+00:00"
    rows = []
    for index in range(12):
        prices = {
            "a": (-110 - index, -110 + index),
            "b": (-108 - index, -112 + index),
            "c": (-112 - index, -108 + index),
        }
        if index == 11:
            prices.pop("c")
        rows.append(
            snapshot(
                f"2026-07-22T{10 + index // 4:02d}:{(index % 4) * 15:02d}:00+00:00",
                commence,
                prices,
            )
        )

    feature = build_feature_vector(
        rows,
        current_event(commence),
        dt("2026-07-22T12:45:00+00:00"),
        config(),
    )

    assert feature["data_quality"]["valid_signal_pull_count"] == 12
    assert feature["data_quality"]["latest_book_count"] == 2
    assert feature["research_status"] == "WATCHLIST_LOW_BOOK_COVERAGE"
