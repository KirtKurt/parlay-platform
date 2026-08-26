from __future__ import annotations

from pathlib import Path

from hello_world import mlb_movement_feature_identity_v2 as identity


ROOT = Path(__file__).resolve().parents[2]


def _game(*, key: str, official_pk: str | None = "900001") -> dict:
    game = {
        "game_key": key,
        "game_id": key,
        "id": key,
        "home_team": "Home Club",
        "away_team": "Away Club",
        "official_commence_time": "2026-08-26T23:00:00+00:00",
        "books": {"book": {"ml": {"home": -110, "away": 100}}},
    }
    if official_pk is not None:
        game["official_game_pk"] = official_pk
    return game


def _snapshot(asof: str, game: dict) -> dict:
    return {"asof": asof, "data": {"games": [game]}}


def _delta(previous: dict, latest: dict) -> dict:
    return {
        "ok": True,
        "hot_team": latest["home_team"],
        "hot_delta": 0.01,
        "book_agreement": {
            "agreeing_books": 2,
            "disagreeing_books": 0,
        },
        "favorite": {
            "side": "home",
            "team": latest["home_team"],
            "dog_side": "away",
            "dog_team": latest["away_team"],
        },
        "reason_codes": ["multi_book_move"],
        "latest_consensus": {"home": 0.52, "away": 0.48},
        "previous_consensus": {"home": 0.51, "away": 0.49},
        "prediction_status": "WATCHLIST",
    }


def _strength(delta: float, agreeing: int, disagreeing: int) -> str:
    return "MEDIUM" if delta and agreeing >= 2 and disagreeing == 0 else "FLAT"


def test_official_identity_survives_provider_game_key_retirement() -> None:
    snapshots = [
        _snapshot("2026-08-26T20:00:00+00:00", _game(key="provider-old")),
        _snapshot("2026-08-26T21:00:00+00:00", _game(key="provider-new")),
    ]
    features = identity.derive_latest_features(
        snapshots,
        delta_for_game=_delta,
        movement_strength=_strength,
    )
    assert len(features) == 1
    feature = features[0]
    assert feature["stable_identity"] == "official:900001"
    assert feature["official_game_pk"] == "900001"
    assert feature["derived_from_immutable_pregame_snapshots"] is True
    assert feature["outcome_data_used"] is False
    assert feature["post_start_observation_used"] is False


def test_post_start_observations_are_excluded() -> None:
    snapshots = [
        _snapshot("2026-08-26T20:00:00+00:00", _game(key="one")),
        _snapshot("2026-08-26T21:00:00+00:00", _game(key="two")),
        _snapshot("2026-08-26T23:01:00+00:00", _game(key="post-start")),
    ]
    features = identity.derive_latest_features(
        snapshots,
        delta_for_game=_delta,
        movement_strength=_strength,
    )
    assert len(features) == 1
    assert features[0]["latest_asof"] == "2026-08-26T21:00:00+00:00"
    assert features[0]["source_observation_count"] == 2


def test_one_real_pregame_observation_never_fabricates_movement() -> None:
    features = identity.derive_latest_features(
        [_snapshot("2026-08-26T20:00:00+00:00", _game(key="one"))],
        delta_for_game=_delta,
        movement_strength=_strength,
    )
    assert features == []


def test_team_start_fallback_is_stable_when_official_pk_is_missing() -> None:
    first = _game(key="provider-one", official_pk=None)
    second = _game(key="provider-two", official_pk=None)
    assert identity.stable_identity(first) == identity.stable_identity(second)
    assert identity.stable_identity(first).startswith("teams:away club|home club|start:")


def test_runtime_writer_has_isolated_rebuild_mode_and_stable_identity() -> None:
    source = (ROOT / "hello_world/mlb_manual_pull.py").read_text(encoding="utf-8")
    assert '== "movement_identity_rebuild"' in source
    assert "_all_hot_snapshots_for_game_date" in source
    assert "derive_latest_features" in source
    assert '"official_game_pk"' in source
    assert '"directPredictionWrite": False' in source
    assert '"directLockWrite": False' in source
    assert '"directLabelWrite": False' in source


def test_trainer_invoker_retries_bounded_transport_through_execution_lease() -> None:
    source = (ROOT / "scripts/invoke_mlb_trainer_with_retry.py").read_text(
        encoding="utf-8"
    )
    assert "ConnectionClosedError" in source
    assert "lambda_transport_retry_exhausted" in source
    assert "tcp_keepalive=True" in source
    assert "retry_execution_lease or mode == STATUS_MODE" in source
