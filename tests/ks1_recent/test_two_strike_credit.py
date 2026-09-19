from copy import deepcopy

import pytest

from ks1.inventory import RESEARCH
from ks1.official_outcomes import (INNING_METHOD, SUBSTITUTION_METHOD, digest, endpoint, reconcile,
                                  reconciled_rows, source_name, verified_batter_credits,
                                  verify_reconciliation)
from ks1.statcast_history import load_training_statcast
from ks1.statcast_recovery import recover, read_recovery
from tests.ks1_recent.test_official_outcomes import raw_receipt
from tests.ks1_recent.test_pitch_attribution import pitch_fixture, reseal
from tests.ks1_recent.test_recovery import MemoryS3, authorize


def fixture():
    bundle, raw, key, source = pitch_fixture('substitution')
    play = source['data']['liveData']['plays']['allPlays'][0]
    first, sub, last = play['playEvents']
    second = deepcopy(first)
    second.update(index=1, pitchNumber=2, startTime='2026-09-01T18:10:10Z',
                  endTime='2026-09-01T18:10:15Z', count={'balls': 0, 'strikes': 2})
    first['count'] = {'balls': 0, 'strikes': 1}
    sub.update(index=2, count={'balls': 0, 'strikes': 2},
               startTime='2026-09-01T18:10:20Z', endTime='2026-09-01T18:10:25Z')
    last.update(index=3, pitchNumber=3, count={'balls': 0, 'strikes': 3},
                startTime='2026-09-01T18:10:30Z', endTime='2026-09-01T18:10:35Z')
    play['playEvents'] = [first, second, sub, last]
    play['about']['endTime'] = last['endTime']
    play['result']['eventType'] = 'strikeout'
    play['count'] = dict(last['count'])
    raw['rows'].insert(1, {**raw['rows'][0], 'pitch_number': '2', 'description': 'foul'})
    raw['rows'][2].update(pitch_number='3', events='strikeout', type='S',
                          description='swinging_strike', woba_value='0', woba_denom='1')
    for player in bundle['full'][0]['teams']['away']['players'].values():
        if player['person']['id'] in (201, 202):
            player['stats']['batting']['plateAppearances'] = 0 if player['person']['id'] == 201 else 2
    bundle['full'][0]['teams']['home']['players']['151']['stats']['pitching']['numberOfPitches'] = 19
    reseal(source, raw)
    return bundle, raw, key, source


@pytest.mark.parametrize('cached', [False, True])
def test_two_strike_strikeout_retains_pitch_batters_and_credits_predecessor(monkeypatch, cached):
    authorize(monkeypatch)
    bundle, raw, key, source = fixture(); before = deepcopy(raw)
    s3 = MemoryS3(); s3.seed(key, raw)
    if cached:
        s3.seed(RESEARCH + source_name('1', raw['rows'], pitch_evidence=True),
                {k: source[k] for k in ('data', 'receipt')})
    def fetch(url):
        assert not cached, 'reuse the retained pitch source without the provider'
        return source['data'], source['receipt']
    initial = load_training_statcast(bundle, s3, 'b')
    assert initial['errors']
    report = recover(bundle, s3, 'b', initial, reconcile_official=True,
                     fetch_official=fetch, fetch=lambda _: pytest.fail('reuse raw'))
    assert report['recovered_dates'] == [raw['date']]
    assert report['prediction_writes'] == 0
    payload, receipts = read_recovery(s3, 'b', raw['date'])
    assert before == raw == payload['raw_statcast']
    assert payload['rows'] == before['rows']
    assert verified_batter_credits(payload) == {('1', '201'): '202'}
    assert load_training_statcast(bundle, s3, 'b')['errors'] == []
    assert all(r['versionId'] for r in receipts)
    altered = deepcopy(payload)
    altered['outcome_reconciliation']['derivations'][-1]['credited_batter'] = '201'
    with pytest.raises(ValueError): verified_batter_credits(altered)
    for k, v in list(s3.versions):
            if 'official-game-advisory-count-pitch-v1/' in k: del s3.versions[k, v]
    assert load_training_statcast(bundle, s3, 'b')['errors']
    assert raw['date'] not in bundle['statcast_retained_dates']


def test_uniform_predecessor_attribution_derives_only_terminal_pitch_batter():
    _, raw, _, source = fixture()
    before = deepcopy(raw)
    # Savant can attribute every pitch, including the terminal strike, to the
    # predecessor while MLB's live feed records the actual pinch hitter. The
    # official box still charges the strikeout to the predecessor.
    raw['rows'][2]['batter'] = '202'
    reseal(source, raw)
    payload = reconcile(
        raw, lambda *args, **kwargs: source, raw_receipt(raw),
        inspection_games={'1'})
    assert before['rows'][0]['batter'] == raw['rows'][0]['batter'] == '202'
    assert payload['raw_statcast'] == raw
    assert [row['batter'] for row in payload['rows'][:3]] == ['202', '202', '201']
    assert verified_batter_credits(payload) == {('1', '201'): '202'}
    changes = payload['outcome_reconciliation']['derivations']
    assert [change['derivation_kind'] for change in changes] == [
        'official_substitution_pitch_attribution', 'official_mid_at_bat_credit']


def test_required_game_still_forces_inspection_pitch_evidence():
    _, raw, _, source = fixture()
    raw['rows'][2]['batter'] = '202'
    raw['rows'][4]['woba_denom'] = ''
    reseal(source, raw)
    calls = []
    def get_official(*args, **kwargs):
        calls.append(kwargs)
        return source
    payload = reconcile(
        raw, get_official, raw_receipt(raw), inspection_games={'1'})
    assert calls == [{'force_pitch_evidence': True}]
    assert payload['rows'][2]['batter'] == '201'
    assert payload['rows'][4]['woba_denom'] == 1
    assert verified_batter_credits(payload) == {('1', '201'): '202'}


@pytest.mark.parametrize('defect', ['inherited', 'prior_missing', 'prior_boolean', 'prior_balls',
    'terminal_count', 'terminal_missing', 'play_count', 'terminal_time', 'non_pitch',
    'missing_weight', 'nonzero_weight', 'missing_denom', 'wrong_batter', 'wrong_pitcher',
    'wrong_speed', 'extra_substitution', 'missing_terminal'])
def test_two_strike_credit_rejects_unproven_or_contradictory_evidence(defect):
    _, raw, _, source = fixture()
    play = source['data']['liveData']['plays']['allPlays'][0]
    first, prior, sub, last = play['playEvents']
    if defect == 'inherited': sub['count']['strikes'] = 3
    elif defect == 'prior_missing': prior.pop('count')
    elif defect == 'prior_boolean': prior['count']['balls'] = False
    elif defect == 'prior_balls': prior['count']['balls'] = 1
    elif defect == 'terminal_count': last['count']['strikes'] = 2
    elif defect == 'terminal_missing': last.pop('count')
    elif defect == 'play_count': play['count']['strikes'] = 2
    elif defect == 'terminal_time': last['endTime'] = '2026-09-01T18:10:34Z'
    elif defect == 'non_pitch': last['isPitch'] = False
    elif defect == 'missing_weight': raw['rows'][2]['woba_value'] = ''
    elif defect == 'nonzero_weight': raw['rows'][2]['woba_value'] = '.9'
    elif defect == 'missing_denom': raw['rows'][2]['woba_denom'] = ''
    elif defect == 'wrong_batter': sub['replacedPlayer']['id'] = 203
    elif defect == 'wrong_pitcher': raw['rows'][1]['pitcher'] = '251'
    elif defect == 'wrong_speed': last['pitchData']['startSpeed'] = 80
    elif defect == 'extra_substitution': first['isSubstitution'] = True
    elif defect == 'missing_terminal': raw['rows'][2]['events'] = ''
    reseal(source, raw)
    with pytest.raises((ValueError, KeyError)):
        reconcile(raw, lambda *a: source, raw_receipt(raw))


def test_conflicting_official_batter_totals_keep_the_date_unqualified(monkeypatch):
    authorize(monkeypatch)
    bundle, raw, key, source = fixture()
    for player in bundle['full'][0]['teams']['away']['players'].values():
        if player['person']['id'] in (201, 202):
            player['stats']['batting']['plateAppearances'] = 1
    s3 = MemoryS3(); s3.seed(key, raw)
    report = recover(bundle, s3, 'b', load_training_statcast(bundle, s3, 'b'),
                     reconcile_official=True, fetch_official=lambda _: (source['data'], source['receipt']),
                     fetch=lambda _: raw)
    assert report['recovered_dates'] == []


@pytest.mark.parametrize('method', [SUBSTITUTION_METHOD, INNING_METHOD])
def test_older_methods_do_not_gain_two_strike_strikeout_policy(method):
    _, raw, _, source = fixture()
    source['receipt'].update(endpoint=endpoint('1', pitch_evidence=True,
                                               game_advisories=False),
                             sha256=digest(source['data']))
    source['retained_receipt'] = {
        'name': source_name('1', raw['rows'], pitch_evidence=True,
                            game_advisories=False),
        'versionId': 'official-old',
        'sha256': digest({key: source[key] for key in ('data', 'receipt')})}
    with pytest.raises(ValueError, match='unsupported official mid-at-bat substitution'):
        reconciled_rows(raw, {'1': source}, method=method)


def test_v7_inning_ending_artifact_still_reproduces():
    from tests.ks1_recent.test_inning_ending import fixture as inning_fixture
    bundle, raw, _, source = inning_fixture()
    source['receipt'].update(endpoint=endpoint('1', inning_evidence=True,
                                               game_advisories=False),
                             sha256=digest(source['data']))
    source['retained_receipt'] = {
        'name': source_name('1', raw['rows'], inning_evidence=True,
                            game_advisories=False),
        'versionId': 'official-v7',
        'sha256': digest({key: source[key] for key in ('data', 'receipt')})}
    rows, changes = reconciled_rows(raw, {'1': source}, method=INNING_METHOD)
    payload = {'date': raw['date'], 'raw_statcast': raw, 'rows': rows,
        'outcome_reconciliation': {'method': INNING_METHOD, 'official_sources': {'1': source},
            'raw_receipt': raw_receipt(raw), 'derivations': changes}}
    verify_reconciliation(payload, {'1': bundle['full'][0]['completedAtUtc']})
