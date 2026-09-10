from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json

import numpy as np
import pyarrow as pa
import pytest
from scipy.stats import skellam

from ks1 import daily
from ks1.inventory import encode
from ks1.publish import parquet_bytes
from ks1.sim_lifecycle import champion_gate, comparison, update
from ks1.sim_nightly import at_nightly_hour, locked_rows, publish_state
from ks1.simulation import IDENTITY, RECIPE, SlateSimulator, calibrate, paths, summarize
from tests.ks1_phase5.test_refresh import capture, advance, seal, DATE


def test_paths_are_decisive_reproducible_and_every_output_uses_same_paths():
    scores = paths('1', 5, 4, 'original')
    assert scores.shape == (5000, 2) and (scores >= 0).all()
    assert (scores[:, 0] != scores[:, 1]).all()
    np.testing.assert_array_equal(scores, paths('1', 5, 4, 'original'))
    assert not np.array_equal(scores, paths('2', 5, 4, 'original'))
    out = summarize(scores)
    assert out['p_home_sim_raw'] == np.mean(scores[:, 0] > scores[:, 1])
    assert out['lambda_home_sim']+out['lambda_away_sim'] == pytest.approx(out['proj_total_sim'])
    expected = skellam.sf(0, 5, 4)/(1-skellam.pmf(0, 5, 4))
    assert abs(out['p_home_sim_raw']-expected) < .025
    assert summarize(scores, {'version': 'test', 'slope': 1.2, 'intercept': -.1})['proj_total_sim'] == out['proj_total_sim']


@pytest.mark.parametrize('h,a,n', [(0, 3, 5000), (float('nan'), 4, 5000), (4, 3, 999), (51, 4, 5000)])
def test_invalid_inputs_do_not_emit_predictions(h, a, n):
    with pytest.raises(ValueError):
        paths('1', h, a, 'x', n)


def test_runtime_fallback_uses_previous_measurement_and_current_projection():
    sim = SlateSimulator(15, previous_seconds=241)
    assert sim.score('1', 4, 3, 'x')['sim_paths'] == '3000'
    ticks = iter([0, 0, 20, 21])
    sim = SlateSimulator(15, clock=lambda: next(ticks))
    assert sim.score('1', 4, 3, 'x')['sim_paths'] == '5000'
    assert sim.score('2', 4, 3, 'x')['sim_paths'] == '3000'


def test_daily_simulation_preserves_all_frozen_values_after_mapping_change(capture, monkeypatch):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output)
    for row in first.to_pylist():
        assert row['official_probability_field'] == 'p_home'
        assert row['p_home'] == .55 and row['sim_paths'] == '5000'
        assert row['proj_total'] == row['proj_total_poisson']
    import pyarrow.parquet as pq
    shadow = pq.ParquetFile(out/'simulation.parquet').read().to_pylist()
    assert shadow[0]['proj_total'] == first.to_pylist()[0]['proj_total_sim']
    advance(folder, out, DATE+'T19:51:00+00:00')
    (folder/'simulation_state.json').write_bytes(encode({'recipe': RECIPE,
        'mapping': {'version': 'changed', 'slope': .5, 'intercept': 1}}))
    seal(folder, DATE+'T19:51:00+00:00')
    monkeypatch.setattr(SlateSimulator, 'score', lambda *args: pytest.fail('locked row was rescored'))
    second, _, _ = daily.predict(folder, output)
    assert second.equals(first) and calls == [2]


def example(i, day_offset=0):
    at = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)+timedelta(days=day_offset)
    row = {'game_id': str(i), 'date': at.date().isoformat(), 'as_of': at.isoformat(),
           'commence_time': at.replace(hour=20).isoformat(), 'home_id': '1', 'away_id': '2',
           'p_home': .6, 'p_home_poisson': .55, 'p_home_sim_raw': .65 if i % 2 else .35,
           'p_home_sim': .65 if i % 2 else .35, 'proj_total_poisson': 8., 'proj_total_sim': 8.1,
           'sim_recipe': RECIPE, 'official_probability_field': 'p_home_sim'}
    evidence = {'version_id': str(i), 'sha256': 'a'*64, 'stored_at': (at+timedelta(seconds=2)).isoformat()}
    final = {'home_score': 5 if i % 2 else 3, 'away_score': 4, 'home_id': '1', 'away_id': '2',
             'completed_at': at.replace(hour=23).isoformat()}
    return {'row': row, 'evidence': evidence}, final


def test_grade_uses_original_official_sim_number_and_deduplicates():
    entry, final = example(1)
    state = update(None, [entry], {'1': final}, '2026-07-02T05:00:00Z')
    grade = state['grades']['1']
    assert grade['official_p_home'] == .65 and grade['official_brier'] == pytest.approx(.35**2)
    assert update(state, [entry], {'1': final}, '2026-07-02T05:00:00Z') == state
    final['home_score'] = 6
    assert update(state, [entry], {'1': final}, '2026-07-03T05:00:00Z')['grades'] == state['grades']
    entry['row']['p_home_sim'] = .9
    with pytest.raises(ValueError, match='locked row changed'):
        update(state, [entry], {'1': final}, '2026-07-03T05:00:00Z')


def test_missing_official_sim_cannot_be_substituted_and_late_snapshot_rejected():
    entry, final = example(1)
    entry['row']['p_home_sim'] = None
    with pytest.raises(ValueError, match='original official'):
        update(None, [entry], {'1': final}, '2026-07-02T05:00:00Z')
    entry, final = example(1)
    entry['evidence']['stored_at'] = '2026-07-02T00:00:00Z'
    with pytest.raises(ValueError, match='post-lock'):
        update(None, [entry], {'1': final}, '2026-07-02T05:00:00Z')


def test_cadences_use_settled_distinct_games_and_strictly_prior_train_labels():
    state = None
    for i in range(1, 51):
        entry, final = example(i, i)
        now = (datetime(2026, 7, 1, 5, tzinfo=timezone.utc)+timedelta(days=i+1)).isoformat()
        state = update(state, [entry], {str(i): final}, now)
    assert len(state['calibration_attempts']) == 7
    assert state['calibration_attempts'][0]['status'] == 'insufficient_prior_labels'
    for attempt in state['calibration_attempts']:
        assert not set(attempt['train_game_ids']) & set(attempt['test_game_ids'])
        for tid in attempt['train_game_ids']:
            assert all(state['grades'][tid]['graded_at'] < state['grades'][vid]['as_of']
                       for vid in attempt['test_game_ids'])
    assert [r['after_locked_games'] for r in state['refit_requests']] == [50]
    assert state['refit_requests'][0]['status'] == 'optional_disabled'
    before = deepcopy(state)
    assert update(state, [], {}, state['as_of']) == before


def test_champion_gate_keeps_worse_tied_or_unpaired_recipe():
    y = [0, 1]*4
    old = [.2, .8]*4
    assert not champion_gate(y, old, [.6]*8)['keep_candidate']
    assert not champion_gate(y, old, old)['keep_candidate']
    assert champion_gate(y, old, [.1, .9]*4)['keep_candidate']
    with pytest.raises(ValueError, match='identical paired'):
        champion_gate(y, old, [.1])


def test_locked_comparison_intersection_and_no_invented_lgb_totals():
    entry, final = example(1)
    row = entry['row'] | final
    second = dict(row, game_id='2', p_home_sim=None)
    report = comparison([row, second])
    assert report['game_ids'] == ['1'] and all(r['n_test'] == 1 for r in report['metrics'])
    assert report['metrics'][0]['totals_mae'] is None
    assert all(r['brier'] is None for r in comparison([])['metrics'])


class Versions:
    def __init__(self, versions):
        self.versions = versions
    def get_paginator(self, name):
        assert name == 'list_object_versions'
        return self
    def paginate(self, **kwargs):
        return [{'Versions': [v for v, body in self.versions]}]
    def get_object(self, **kwargs):
        return {'Body': io.BytesIO(next(b for v, b in self.versions if v['VersionId'] == kwargs['VersionId']))}


def test_lock_inventory_requires_actual_original_version_and_unchanged_latest_row():
    entry, _ = example(1)
    row = entry['row']
    key = daily.PREFIX+'date=2026-07-01/predictions.parquet'
    def version(n, hour, rows, latest=False):
        return ({'Key': key, 'VersionId': str(n), 'IsLatest': latest,
                 'LastModified': datetime(2026, 7, 1, hour, tzinfo=timezone.utc)},
                parquet_bytes(pa.Table.from_pylist(rows)))
    old, latest = version(1, 11, [row]), version(2, 23, [row], True)
    accepted, report = locked_rows(Versions([old, latest]), 'test', '2026-07-02T05:00:00Z')
    assert len(accepted) == 1 and accepted[0]['evidence']['version_id'] == '1'
    assert not locked_rows(Versions([latest]), 'test', '2026-07-02T05:00:00Z')[0]
    changed = version(2, 23, [dict(row, p_home_sim=.9)], True)
    assert not locked_rows(Versions([old, changed]), 'test', '2026-07-02T05:00:00Z')[0]


def test_nightly_dst_gate_and_no_publication_from_pr(monkeypatch):
    assert at_nightly_hour(datetime(2026, 7, 1, 5, tzinfo=timezone.utc))
    assert not at_nightly_hour(datetime(2026, 7, 1, 6, tzinfo=timezone.utc))
    assert at_nightly_hour(datetime(2026, 1, 1, 6, tzinfo=timezone.utc))
    monkeypatch.setenv('GITHUB_REF', 'refs/pull/704/merge')
    with pytest.raises(ValueError, match='existing main'):
        publish_state(None, {}, {})
