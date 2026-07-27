from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))
sys.path.insert(0, str(ROOT / "scripts"))

import run_mlb_v8_historical_first_five_probe as probe


def test_probe_cost_is_bounded_to_400_credits_for_20_games():
    estimated = (
        probe.DEFAULT_LIMIT
        * len(probe.MARKETS)
        * len(probe.REGIONS)
        * probe.CREDITS_PER_HISTORICAL_MARKET_REGION
    )
    assert probe.DEFAULT_LIMIT == 20
    assert probe.MARKETS == (
        "h2h_1st_5_innings",
        "spreads_1st_5_innings",
    )
    assert probe.REGIONS == ("us",)
    assert estimated == 400
    assert estimated <= probe.DEFAULT_MAX_CREDITS == 500


def test_probe_selection_is_chronological_and_requires_identity_and_lock():
    rows = [
        {
            "slateDateEt": "2026-05-01",
            "officialGamePk": "1",
            "providerEventId": "event-1",
            "predictionLockAtUtc": "2026-05-01T17:00:00Z",
            "homeWon": 0,
        },
        {
            "slateDateEt": "2026-05-02",
            "officialGamePk": "2",
            "providerEventId": "event-2",
            "predictionLockAtUtc": "2026-05-02T17:00:00Z",
            "homeWon": 1,
        },
        {
            "slateDateEt": "2026-05-03",
            "officialGamePk": "3",
            "providerEventId": "",
            "predictionLockAtUtc": "2026-05-03T17:00:00Z",
            "homeWon": 1,
        },
        {
            "slateDateEt": "2026-05-04",
            "officialGamePk": "4",
            "providerEventId": "event-4",
            "predictionLockAtUtc": "",
            "homeWon": 0,
        },
    ]
    selected = probe._eligible(rows)
    assert [row["officialGamePk"] for row in selected] == ["2", "1"]
    # Opposite labels prove outcome is not the ordering key.
    assert [row["homeWon"] for row in selected] == [1, 0]


def test_probe_config_disables_unbounded_market_families():
    config = probe._config(500, 20)
    assert config.event_regions == ("us",)
    assert config.first_five_enabled is True
    assert config.alternates_enabled is False
    assert config.team_props_enabled is False
    assert config.player_props_enabled is False
    assert config.max_events_per_cycle == 20
    assert config.max_estimated_credits_per_cycle == 500


def test_safe_wrapper_redacts_plain_and_query_string_api_keys(monkeypatch):
    import run_mlb_v8_historical_first_five_probe_safe as safe

    monkeypatch.setenv("ODDS_API_KEY", "plain-secret")
    value = safe._redact(
        "plain-secret https://example.test?apiKey=url%2Fencoded-secret&markets=h2h"
    )
    assert "plain-secret" not in value
    assert "url%2Fencoded-secret" not in value
    assert "apiKey=[REDACTED]" in value


def test_probe_source_is_shadow_only_training_ineligible_and_exactly_addressed():
    source = Path("scripts/run_mlb_v8_historical_first_five_probe.py").read_text()
    safe = Path("scripts/run_mlb_v8_historical_first_five_probe_safe.py").read_text()
    assert '"authority": "SHADOW_ONLY"' in source
    assert '"trainingEligible": False' in source
    assert '"productionAuthorityChanged": False' in source
    assert '"automaticWagerAllowed": False' in source
    assert 'IfNoneMatch="*"' in source
    assert "put_item(" not in source
    assert "lambda.invoke(" not in source
    assert "probe.HTTP_RETRIES = 1" in safe
    assert "hashlib.sha256(body).hexdigest()" in safe
    assert 'apiKey=)[^&\\s\\\"\']+' in safe
