import hashlib
import io
import json

import pandas as pd
import pytest

import ks1.historical_individual_bullpen_enrichment as subject
from ks1.inventory import encode


def profile(player_id, appearances, fip, era, kbb):
    return {
        'player_id': str(player_id),
        'windows': {'30d': {
            'appearances': appearances,
            'fip': fip,
            'era': era,
            'k_bb_pct': kbb,
        }},
    }


def test_usage_ranks_are_prior_appearances_with_deterministic_ties():
    values = subject._ranked_values([
        profile(30, 5, 4.3, 4.0, 10.0),
        profile(20, 8, 2.8, 2.5, 21.0),
        profile(10, 8, 3.1, 2.9, 19.0),
        profile(40, 0, 1.0, 1.0, 30.0),
    ])
    assert values['individual_bullpen_rank1_fip_30d'] == 3.1
    assert values['individual_bullpen_rank2_fip_30d'] == 2.8
    assert values['individual_bullpen_rank3_fip_30d'] == 4.3
    assert values['individual_bullpen_rank1_k_bb_pct_30d'] == 19.0


def test_official_history_must_be_exactly_bound_to_input_proof():
    source = {
        'bucket': 'b', 'key': 'history.json', 'versionId': 'v1',
        'sha256': 'a' * 64, 'complete_years': [2025, 2026],
    }
    proof = {'official_history_source': source, 'source_receipts': [dict(source)]}
    assert subject._official_receipt(proof) == source
    proof['source_receipts'][0]['versionId'] = 'other'
    with pytest.raises(ValueError, match='receipt_unbound'):
        subject._official_receipt(proof)


class FakeS3:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        return {'Body': io.BytesIO(self.body)}


class FakeHistory:
    def bullpen_roster_at(self, cutoff, team_id, roster_ids, game_date=None):
        assert cutoff == '2026-06-01T18:50:00+00:00'
        assert roster_ids == ['10', '20', '30']
        assert game_date == '2026-06-01'
        offset = 0.0 if str(team_id) == '1' else 1.0
        return {'_reliever_profiles': [
            profile(10, 9, 2.8 + offset, 2.6 + offset, 22.0 - offset),
            profile(20, 7, 3.3 + offset, 3.1 + offset, 17.0 - offset),
            profile(30, 5, 4.0 + offset, 4.2 + offset, 11.0 - offset),
        ]}


def test_enrichment_uses_only_exact_bound_pret10_roster_and_no_labels(monkeypatch):
    entry = {'game_id': '99', 'payload': {'unused': True}}
    body = encode(entry)
    receipt = {
        'source_type': 'mlb_statsapi_timecoded_team_context',
        'provider': 'MLB Stats API',
        'bucket': 'retained', 'key': 'game99.json', 'versionId': 'v99',
        'sha256': hashlib.sha256(body).hexdigest(),
    }
    frame = pd.DataFrame([{
        'game_id': '99', 'date': '2026-06-01', 'season': 2026,
        'home_id': '1', 'away_id': '2',
        'home_win': True,
        'lineup_bullpen_context_evidence': 'historical_timecoded_mlb_feed',
        'historical_lineup_bullpen_context_status': 'SUPPORTED_V1_COMPLETE',
        'historical_lineup_bullpen_context_source': json.dumps(receipt),
    }])
    s3 = FakeS3(body)
    official = {
        'bucket': 'retained', 'key': 'official.json', 'versionId': 'vo',
        'sha256': 'b' * 64, 'complete_years': [2025, 2026],
    }
    monkeypatch.setattr(subject, '_history', lambda s3, proof, frame: (
        FakeHistory(), official, 5000))

    def fake_context(value):
        assert value['receipt']['versionId'] == 'v99'
        return {
            'game_id': '99', 'as_of': '2026-06-01T18:50:00+00:00',
            'sides': {
                'home': {'team_id': '1', 'bullpen_status': 'OBSERVED_ROSTER_ONLY',
                         'bullpen_roster_ids': ['10', '20', '30']},
                'away': {'team_id': '2', 'bullpen_status': 'OBSERVED_ROSTER_ONLY',
                         'bullpen_roster_ids': ['10', '20', '30']},
            },
        }
    monkeypatch.setattr(subject, 'feed_team_context', fake_context)

    enriched, report = subject.enrich_frame(frame, s3, {'unused': True}, minimum_nonmissing=1)
    assert enriched.loc[0, 'home_individual_bullpen_rank1_fip_30d'] == 2.8
    assert enriched.loc[0, 'away_individual_bullpen_rank1_fip_30d'] == 3.8
    assert report['exact_team_context_rows_verified'] == 1
    assert report['feature_rows_recovered'] == 1
    assert report['provider_requests'] == 0
    assert report['prediction_writes'] == 0
    assert report['official_ledger_writes'] == 0
    assert s3.calls == [{'Bucket': 'retained', 'Key': 'game99.json', 'VersionId': 'v99'}]
