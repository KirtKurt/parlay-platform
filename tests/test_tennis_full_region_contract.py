from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / "tennis_learning" / "live_pipeline.py").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "tennis-template.yaml").read_text(encoding="utf-8")


def test_all_current_h2h_regions_are_required():
    required = {"us", "us2", "uk", "eu", "au", "fr", "se", "us_ex"}
    assert 'DEFAULT_H2H_REGIONS = "us,us2,uk,eu,au,fr,se,us_ex"' in PIPELINE
    for region in required:
        assert region in PIPELINE
    assert "TENNIS_ODDS_REGIONS: 'us,us2,uk,eu,au,fr,se,us_ex'" in TEMPLATE


def test_dfs_region_is_explicitly_non_h2h_not_silently_ignored():
    assert 'NON_H2H_PROVIDER_REGIONS = ("us_dfs",)' in PIPELINE
    assert '"non_h2h_provider_regions": list(NON_H2H_PROVIDER_REGIONS)' in PIPELINE


def test_sport_key_cap_is_removed():
    assert "MAX_ACTIVE_KEYS" not in PIPELINE
    assert "[:MAX_ACTIVE_KEYS]" not in PIPELINE
    assert '"sport_keys_truncated": 0' in PIPELINE


def test_provider_event_inventory_not_odds_response_defines_slate():
    assert 'f"/sports/{sport_key}/events"' in PIPELINE
    assert '"inventory_events": total_events' in PIPELINE
    assert '"resolved_events": resolved' in PIPELINE
    assert '"coverage_complete": coverage_complete' in PIPELINE


def test_all_regions_are_requested_independently_and_partial_failure_fails_closed():
    assert "for region in REGIONS:" in PIPELINE
    assert '"regions": region' in PIPELINE
    assert 'regional_successes == regional_requests' in PIPELINE
    assert "Tennis all-match/all-region coverage incomplete" in PIPELINE


def test_t10_and_model_authority_are_preserved():
    assert 'TENNIS_PREDICTION_CUTOFF_MINUTES", "10"' in PIPELINE
    assert 'evaluation_status="T10_CLOSED"' in PIPELINE
    assert "predict(" in PIPELINE
    assert "human" not in PIPELINE.lower()
