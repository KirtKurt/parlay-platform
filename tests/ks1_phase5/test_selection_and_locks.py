"""Serving gates and lock continuity. Synthetic envelopes only."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from ks1.daily import preserve_frozen, valid_pregame_lock
from ks1.platt_inputs import read_locked_predictions
from ks1.selection import apply


def test_totals_blend_halfway_to_market():
    row = {'market_total': 6.5, 'market_home_prob': 0.5, 'starter_feature_source': 'learned_starter',
           'status': 'confirmed_lineups', 'home_starter_status': 'probable',
           'away_starter_status': 'probable', 'lineup_status': 'confirmed'}
    out = apply(row, 0.51, 0.52, 4.5, 4.5)
    assert out['proj_total'] == pytest.approx(7.75)
    assert out['edge_total'] == pytest.approx(1.25)
    assert out['pick_status'] == 'bet'


def test_engine_disagreement_and_side_split_pass_and_shrink():
    row = {'market_total': 8.5, 'market_home_prob': 0.40, 'starter_feature_source': 'learned_starter',
           'status': 'confirmed_lineups', 'home_starter_status': 'probable',
           'away_starter_status': 'probable', 'lineup_status': 'confirmed'}
    out = apply(row, 0.64, 0.44, 4.0, 4.0)
    assert out['pick_status'] == 'pass'
    assert 'engine_disagreement' in out['selection_reason']
    assert 'side_disagreement' in out['selection_reason']
    assert out['p_lgb'] == out['p_home'] == 0.64


def test_projected_or_missing_starter_is_not_a_served_pick():
    row = {'market_total': 7.5, 'market_home_prob': 0.50,
           'status': 'projected', 'home_starter_status': 'probable',
           'away_starter_status': 'probable', 'lineup_status': 'projected'}
    out = apply(row, 0.64, 0.63, 4.5, 4.4)
    assert out['pick_status'] == 'pass'
    assert 'starter_unverified' in out['selection_reason']
    assert out['p_home'] == 0.64


def test_preserve_keeps_post_cutoff_row():
    old = pd.DataFrame([{'date': '2026-09-10', 'game_id': '823088',
                         'commence_time': '2026-09-10T20:10:00Z',
                         'as_of': '2026-09-10T07:21:00Z', 'p_home': .49}])
    kept = preserve_frozen(old.iloc[:0], old, '2026-09-10', '2026-09-11T01:08:00Z')
    assert list(kept.game_id) == ['823088']
    assert valid_pregame_lock(old.iloc[0])


class VersionStore:
    def __init__(self, versions):
        self.versions = versions

    def get_paginator(self, name):
        assert name == 'list_object_versions'
        store = self

        class Pages:
            def paginate(self, Bucket, Prefix):
                current = {k: max(vs, key=lambda v: v['LastModified']) for k, vs in store.versions.items()}
                entries = []
                for key, vs in store.versions.items():
                    for v in vs:
                        entries.append({**v, 'Key': key, 'IsLatest': v['VersionId'] == current[key]['VersionId']})
                yield {'Versions': entries, 'DeleteMarkers': []}
        return Pages()

    def get_object(self, Bucket, Key, VersionId=None):
        import io
        vs = self.versions[Key]
        body = next(v['Body'] for v in vs if v['VersionId'] == VersionId)
        return {'Body': io.BytesIO(body)}


def test_lock_reader_recovers_row_dropped_from_latest(tmp_path):
    from ks1.publish import parquet_bytes
    import pyarrow as pa
    from ks1.daily import SCHEMA

    def blob(rows):
        return parquet_bytes(pa.Table.from_pylist(rows, schema=SCHEMA))

    first = [{'date': '2026-09-10', 'game_id': '823088', 'home_id': '1', 'away_id': '2',
              'commence_time': '2026-09-10T20:10:00+00:00', 'as_of': '2026-09-10T07:21:00+00:00',
              'p_home': 0.49, 'model_version': 'x'}]
    later = [{'date': '2026-09-10', 'game_id': '823499', 'home_id': '3', 'away_id': '4',
              'commence_time': '2026-09-10T23:05:00+00:00', 'as_of': '2026-09-10T07:21:00+00:00',
              'p_home': 0.77, 'model_version': 'x'}]
    key = 'mlb/ks1/predictions-v1/date=2026-09-10/predictions.parquet'
    store = VersionStore({key: [
        {'VersionId': 'v1', 'LastModified': datetime(2026, 9, 10, 7, 22, tzinfo=timezone.utc),
         'Body': blob(first + later)},
        {'VersionId': 'v2', 'LastModified': datetime(2026, 9, 11, 1, 8, tzinfo=timezone.utc),
         'Body': blob(later)},
    ]})
    admitted, inventory = read_locked_predictions(store, 'test', '2026-09-11T05:00:00+00:00')
    ids = {e['row']['game_id'] for e in admitted}
    assert '823088' in ids
    recovered = next(e for e in admitted if e['row']['game_id'] == '823088')
    assert recovered['evidence']['recovered_missing_current'] is True
    assert not any(item.get('game_id') == '823088' and item.get('reason') == 'changed_or_missing_frozen_row'
                   for item in inventory['excluded'])
