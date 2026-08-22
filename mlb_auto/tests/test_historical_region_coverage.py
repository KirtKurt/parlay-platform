from pathlib import Path

from mlb_auto.odds_api import OddsApiClient


EXPECTED_REGIONS = 'us,us2,uk,eu,au'


def test_isolated_lambda_sets_all_regions_for_every_odds_client(monkeypatch):
    template = (Path(__file__).resolve().parents[1] / 'template.yaml').read_text()
    assert "MLB_AUTO_ODDS_REGIONS: 'us,us2,uk,eu,au'" in template

    monkeypatch.setenv('MLB_AUTO_ODDS_REGIONS', EXPECTED_REGIONS)
    client = OddsApiClient(api_key='test-key')
    assert client.regions == EXPECTED_REGIONS
