import hashlib
import io
import json

import pandas as pd
import pytest

import ks1.historical_individual_bullpen_enrichment as subject
from ks1.inventory import RESEARCH, encode


def profile(player_id, appearances, fip, era, kbb, xwoba=None):
    return {
        'player_id': str(player_id),
        'windows': {'30d': {
            'appearances': appearances,
            'fip': fip,
            'era': era,
            'k_bb_pct': kbb,
            'xwoba': xwoba,
        }},
    }


def _compact_statcast_receipts():
    return (
        {
            'bucket': 'retained', 'key': RESEARCH + 'statcast.json',
            'versionId': 'vp', 'sha256': 'd' * 64,
        },
        {
            'bucket': 'retained', 'key': RESEARCH + 'statcast/' + 'e' * 64 + '.json',
            'versionId': 'va', 'sha256': 'e' * 64,
        },
    )


def test_usage_ranks_are_prior_appearances_with_deterministic_ties():
    values = subject._ranked_values([
        profile(30, 5, 4.3, 4.0, 10.0, .340),
        profile(20, 8, 2.8, 2.5, 21.0, .280),
        profile(10, 8, 3.1, 2.9, 19.0, .300),
        profile(40, 0, 1.0, 1.0, 30.0, .200),
    ])
    assert values['individual_bullpen_rank1_fip_30d'] == 3.1
    assert values['individual_bullpen_rank2_fip_30d'] == 2.8
    assert values['individual_bullpen_rank3_fip_30d'] == 4.3
    assert values['individual_bullpen_rank1_k_bb_pct_30d'] == 19.0
    assert values['individual_bullpen_rank1_xwoba_30d'] == .300
    assert values['individual_bullpen_rank2_xwoba_30d'] == .280


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
            profile(10, 9, 2.8 + offset, 2.6 + offset, 22.0 - offset, .285 + offset / 100),
            profile(20, 7, 3.3 + offset, 3.1 + offset, 17.0 - offset, .310 + offset / 100),
            profile(30, 5, 4.0 + offset, 4.2 + offset, 11.0 - offset, .335 + offset / 100),
        ]}


def test_proof_bound_statcast_replay_requires_exact_report_and_receipts(monkeypatch):
    official = {
        'bucket': 'retained', 'key': 'official.json', 'versionId': 'vo',
        'sha256': 'b' * 64, 'complete_years': [2025, 2026],
    }
    pointer_receipt, artifact_receipt = _compact_statcast_receipts()
    replay_receipt = {
        'bucket': 'retained', 'key': 'sources/statcast-v2/2026-05-31.json',
        'versionId': 'vs', 'sha256': 'c' * 64,
    }
    expected_report = {
        'source': 'sources/statcast-v2/', 'provider_requests': 0,
        'verified_outcome_dates': 1, 'verified_physical_dates': 1,
        'retained_pitch_rows': 1,
    }
    proof = {
        'official_history_source': official,
        'historical_statcast_report': expected_report,
        'source_receipts': [
            dict(official), dict(pointer_receipt), dict(artifact_receipt),
            dict(replay_receipt),
        ],
    }
    bundle = {
        'official_history_source': dict(official),
        'source_receipts': [
            dict(official), dict(pointer_receipt), dict(artifact_receipt),
        ],
    }
    row = {'game_pk': '50', 'pitcher': '10', 'game_date': '2026-05-31'}

    monkeypatch.setattr(subject, 'load_existing', lambda cf, s3, bucket: bundle)

    def replay(value, s3, bucket):
        value['statcast'] = [row]
        value['statcast_retained_dates'] = ['2026-05-31']
        value['statcast_physical_dates'] = ['2026-05-31']
        value['statcast_verified_games'] = ['50']
        value['statcast_physical_games'] = ['50']
        value['source_receipts'].append(dict(replay_receipt))
        return dict(expected_report)

    monkeypatch.setattr(subject, 'load_training_statcast', replay)
    context, evidence = subject.proof_bound_statcast_context('cf', 's3', 'bucket', proof)
    assert context['rows'] == [row]
    assert context['retained_dates'] == ['2026-05-31']
    assert evidence['all_preloaded_statcast_receipts_bound_to_input_proof'] is True
    assert evidence['all_replay_receipts_bound_to_input_proof'] is True
    assert evidence['all_statcast_receipts_bound_to_input_proof'] is True
    assert evidence['preloaded_source_receipt_count'] == 2
    assert evidence['replay_source_receipt_count'] == 1
    assert evidence['bound_statcast_source_receipt_count'] == 3
    assert evidence['proof_report_sha256'] == evidence['replay_report_sha256']
    assert evidence['provider_requests'] == 0


def test_proof_bound_statcast_replay_rejects_unbound_preloaded_compact_artifact(monkeypatch):
    official = {
        'bucket': 'retained', 'key': 'official.json', 'versionId': 'vo',
        'sha256': 'b' * 64, 'complete_years': [2025, 2026],
    }
    pointer_receipt, artifact_receipt = _compact_statcast_receipts()
    expected_report = {'provider_requests': 0, 'retained_pitch_rows': 1}
    proof = {
        'official_history_source': official,
        'historical_statcast_report': expected_report,
        'source_receipts': [dict(official), dict(pointer_receipt), dict(artifact_receipt)],
    }
    advanced_artifact = dict(artifact_receipt, versionId='new-version', sha256='f' * 64)
    bundle = {
        'official_history_source': dict(official),
        'source_receipts': [dict(official), dict(pointer_receipt), advanced_artifact],
    }
    monkeypatch.setattr(subject, 'load_existing', lambda cf, s3, bucket: bundle)
    monkeypatch.setattr(
        subject, 'load_training_statcast', lambda value, s3, bucket: dict(expected_report))
    with pytest.raises(ValueError, match='preloaded_receipt_unbound'):
        subject.proof_bound_statcast_context('cf', 's3', 'bucket', proof)


def test_proof_bound_statcast_replay_rejects_preloaded_receipt_without_identity(monkeypatch):
    official = {
        'bucket': 'retained', 'key': 'official.json', 'versionId': 'vo',
        'sha256': 'b' * 64, 'complete_years': [2025, 2026],
    }
    pointer_receipt, artifact_receipt = _compact_statcast_receipts()
    invalid_artifact = dict(artifact_receipt, versionId=None)
    proof = {
        'official_history_source': official,
        'historical_statcast_report': {'provider_requests': 0},
        'source_receipts': [dict(official), dict(pointer_receipt), dict(artifact_receipt)],
    }
    bundle = {
        'official_history_source': dict(official),
        'source_receipts': [dict(official), dict(pointer_receipt), invalid_artifact],
    }
    monkeypatch.setattr(subject, 'load_existing', lambda cf, s3, bucket: bundle)
    with pytest.raises(ValueError, match='preloaded_receipt_invalid'):
        subject.proof_bound_statcast_context('cf', 's3', 'bucket', proof)


def test_proof_bound_statcast_replay_fails_closed_on_report_change(monkeypatch):
    official = {
        'bucket': 'retained', 'key': 'official.json', 'versionId': 'vo',
        'sha256': 'b' * 64, 'complete_years': [2025, 2026],
    }
    pointer_receipt, artifact_receipt = _compact_statcast_receipts()
    proof = {
        'official_history_source': official,
        'historical_statcast_report': {'provider_requests': 0, 'retained_pitch_rows': 1},
        'source_receipts': [dict(official), dict(pointer_receipt), dict(artifact_receipt)],
    }
    bundle = {
        'official_history_source': dict(official),
        'source_receipts': [dict(official), dict(pointer_receipt), dict(artifact_receipt)],
    }
    monkeypatch.setattr(subject, 'load_existing', lambda cf, s3, bucket: bundle)
    monkeypatch.setattr(subject, 'load_training_statcast',
                        lambda value, s3, bucket: {'provider_requests': 0, 'retained_pitch_rows': 2})
    with pytest.raises(ValueError, match='replay_report_mismatch'):
        subject.proof_bound_statcast_context('cf', 's3', 'bucket', proof)


def test_history_wires_proof_bound_statcast_into_shared_feature_engine(monkeypatch):
    payload = {'games': [{'officialGamePk': 1}]}
    body = encode(payload)
    official = {
        'bucket': 'retained', 'key': 'official.json', 'versionId': 'vo',
        'sha256': hashlib.sha256(body).hexdigest(), 'complete_years': [2026],
    }
    proof = {'official_history_source': official, 'source_receipts': [dict(official)]}
    context = {
        'rows': [{'game_pk': '1', 'pitcher': '10'}],
        'retained_dates': ['2026-05-31'],
        'verified_games': ['1'],
        'physical_dates': ['2026-05-31'],
        'physical_games': ['1'],
    }
    captured = {}

    class CapturingFeatures:
        def __init__(self, games, statcast_rows=None, **kwargs):
            captured['games'] = games
            captured['rows'] = statcast_rows
            captured['kwargs'] = kwargs

    monkeypatch.setattr(subject, 'Features', CapturingFeatures)
    history, source, count = subject._history(
        FakeS3(body), proof, pd.DataFrame([{'season': 2026}]), context)
    assert isinstance(history, CapturingFeatures)
    assert source == official
    assert count == 1
    assert captured['rows'] == context['rows']
    assert captured['kwargs']['statcast_retained_dates'] == ['2026-05-31']
    assert captured['kwargs']['statcast_physical_dates'] == ['2026-05-31']
    assert captured['kwargs']['statcast_complete'] is False


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
    monkeypatch.setattr(subject, '_history', lambda s3, proof, frame, statcast_context=None: (
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

    evidence = {'enabled': True, 'all_replay_receipts_bound_to_input_proof': True}
    enriched, report = subject.enrich_frame(
        frame, s3, {'unused': True}, minimum_nonmissing=1,
        statcast_context={'rows': []}, statcast_evidence=evidence)
    assert enriched.loc[0, 'home_individual_bullpen_rank1_fip_30d'] == 2.8
    assert enriched.loc[0, 'away_individual_bullpen_rank1_fip_30d'] == 3.8
    assert enriched.loc[0, 'home_individual_bullpen_rank1_xwoba_30d'] == .285
    assert enriched.loc[0, 'away_individual_bullpen_rank1_xwoba_30d'] == .295
    assert report['exact_team_context_rows_verified'] == 1
    assert report['feature_rows_recovered'] == 1
    assert report['statcast_replay'] == evidence
    assert report['provider_requests'] == 0
    assert report['prediction_writes'] == 0
    assert report['official_ledger_writes'] == 0
    assert s3.calls == [{'Bucket': 'retained', 'Key': 'game99.json', 'VersionId': 'v99'}]