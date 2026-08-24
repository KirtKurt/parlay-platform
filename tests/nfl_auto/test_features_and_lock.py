from nfl_auto.features import Game, FEATURE_NAMES, materialize_game_rows


def snapshot(timestamp: str, p: float, spread: float, total: float) -> dict:
    return {
        "snapshot_at": timestamp,
        "moneyline": {
            "home_probability": p,
            "bookmaker_count": 5,
            "dispersion": 0.02,
        },
        "spread": {
            "home_probability": 0.51,
            "home_line": spread,
            "bookmaker_count": 5,
            "dispersion": 0.02,
        },
        "total": {
            "over_probability": 0.49,
            "total_line": total,
            "bookmaker_count": 5,
            "dispersion": 0.02,
        },
    }


def rolling() -> dict:
    base_home = {
        "offensive_epa": 0.12,
        "pass_epa": 0.18,
        "rush_epa": 0.03,
        "success_rate": 0.47,
        "explosive_rate": 0.12,
        "turnover_rate": 0.02,
        "early_down_pass_rate": 0.58,
        "third_down_success": 0.44,
        "defensive_epa_allowed": -0.03,
        "games_available": 8,
    }
    base_away = {
        "offensive_epa": 0.03,
        "pass_epa": 0.05,
        "rush_epa": 0.01,
        "success_rate": 0.41,
        "explosive_rate": 0.08,
        "turnover_rate": 0.04,
        "early_down_pass_rate": 0.51,
        "third_down_success": 0.36,
        "defensive_epa_allowed": 0.08,
        "games_available": 8,
    }
    return {"BUF": base_home, "MIA": base_away}


def game() -> Game:
    return Game(
        game_id="2025_01_MIA_BUF",
        season=2025,
        week=1,
        game_type="REG",
        kickoff_utc="2025-09-08T00:00:00Z",
        home_team="BUF",
        away_team="MIA",
        home_score=30,
        away_score=20,
        home_rest=7,
        away_rest=6,
    )


def test_dual_provider_frozen_rows_and_t10_lock() -> None:
    snapshots = {
        1440: snapshot("2025-09-07T00:00:00Z", 0.54, -2.5, 46.5),
        60: snapshot("2025-09-07T23:00:00Z", 0.56, -3.0, 47.0),
        10: snapshot("2025-09-07T23:50:00Z", 0.58, -3.5, 47.5),
    }
    rows, excluded = materialize_game_rows(
        game=game(),
        rolling=rolling(),
        snapshots=snapshots,
        bbd_provenance={"games": "s3://raw/bbd-games", "plays": "s3://raw/bbd-plays"},
        odds_provenance={"10": "s3://raw/odds-t10"},
        min_bookmakers=3,
    )
    assert not excluded
    assert len(rows) == 3
    assert all(len(row.features) == len(FEATURE_NAMES) for row in rows)
    assert all(row.bbd_digest and row.odds_digest for row in rows)
    labels = {row.target: row.label for row in rows}
    assert labels == {
        "moneyline_home_win": 1,
        "spread_home_cover": 1,
        "total_over": 1,
    }


def test_snapshot_inside_t10_is_rejected() -> None:
    snapshots = {10: snapshot("2025-09-07T23:51:00Z", 0.58, -3.5, 47.5)}
    rows, excluded = materialize_game_rows(
        game=game(),
        rolling=rolling(),
        snapshots=snapshots,
        bbd_provenance={"games": "present", "plays": "present"},
        odds_provenance={"10": "present"},
        min_bookmakers=3,
    )
    assert rows == []
    assert set(excluded.values()) == {"T10_SNAPSHOT_TOO_LATE"}
