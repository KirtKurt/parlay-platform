"""Refresh integration checks use synthetic source envelopes, never live keys."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ks1 import daily
from ks1.inventory import encode
from ks1.live_inputs import ProviderFailure, lineup_feeds
from ks1.refresh import fingerprint, observe, pregame_status
from ks1.publish import parquet_bytes

DATE = '2026-09-10'
AT = DATE+'T10:00:00+00:00'
WARMUP = {'abstractGameCode': 'L', 'abstractGameState': 'Live', 'codedGameState': 'P',
          'detailedState': 'Warmup', 'statusCode': 'PW', 'startTimeTBD': False}


def envelope(payload, at=AT):
    return {'payload': payload, 'receipt': {'status': 200, 'as_of': at,
                                          'sha256': hashlib.sha256(encode(payload)).hexdigest()}}


def feed(game, confirmed=True):
    boxes, people = {}, {}
    for side in ('home', 'away'):
        tid = game['teams'][side]['team']['id']
        order = list(range(tid*100, tid*100+9)) if confirmed else []
        boxes[side] = {'team': game['teams'][side]['team'], 'battingOrder': order,
                       'players': {'ID'+str(pid): {'person': {'id': pid}, 'battingOrder': str(slot*100),
                                   'gameStatus': {'isSubstitute': False}} for slot, pid in enumerate(order, 1)}}
        for pid in order:
            people[str(pid)] = {'id': pid, 'batSide': {'code': 'R' if side == 'home' else 'L'}}
        starter = game['teams'][side].get('probablePitcher', {}).get('id')
        if starter:
            people[str(starter)] = {'id': starter, 'pitchHand': {'code': 'L' if side == 'home' else 'R'}}
    return {'gameData': {'game': {'pk': game['gamePk']}, 'datetime': {'dateTime': game['gameDate']},
                         'status': {'abstractGameState': 'Preview'},
                         'players': people,
                         'probablePitchers': {s: deepcopy(game['teams'][s].get('probablePitcher', {})) for s in ('home', 'away')}},
            'liveData': {'boxscore': {'teams': boxes}}}


@pytest.fixture
def capture(tmp_path, monkeypatch):
    folder = tmp_path/'inputs'; folder.mkdir()
    games, history, bbs, odds = [], [], [], []
    for n in (1, 2):
        teams = {s: {'team': {'id': n*10+i, 'name': f'Team {n*10+i}'},
                      'probablePitcher': {'id': n*100+i, 'fullName': f'Pitcher {n*100+i}'}}
                 for s, i in [('home', 1), ('away', 2)]}
        game = {'gamePk': n, 'gameDate': DATE+'T20:00:00Z', 'gameType': 'R', 'teams': teams,
                'status': {'abstractGameState': 'Preview', 'detailedState': 'Scheduled'}}
        games.append(game)
        history.append({'officialGamePk': n+10, 'startAtUtc': '2026-09-09T18:00:00Z',
                        'completedAtUtc': '2026-09-09T21:00:00Z', 'gameType': 'R',
                        'teams': {s: {'id': t['team']['id'], 'name': t['team']['name'],
                                     'batting': {'atBats': 30, 'hits': 8, 'baseOnBalls': 3, 'hitByPitch': 0,
                                                 'sacFlies': 0, 'doubles': 1, 'triples': 0, 'homeRuns': 1},
                                     'priorStarters': {'strikeOuts': 6, 'baseOnBalls': 2, 'battersFaced': 24},
                                     'relief': {'pitches': 40, 'outs': 9}} for s, t in teams.items()}})
        bbs.append({'id': f'synthetic-bbs-{n}', 'kickoff_utc': game['gameDate'], 'sport': 'baseball', 'league': 'MLB',
                    'status': 'scheduled', **{s: {'id': f'synthetic-{t["team"]["id"]}', 'name': t['team']['name']} for s, t in teams.items()}})
        odds.append({'id': f'synthetic-odds-{n}', 'home_team': teams['home']['team']['name'],
                     'away_team': teams['away']['team']['name'], 'commence_time': game['gameDate'],
                     'bookmakers': [{'key': 'test', 'last_update': AT, 'markets': [
                         {'key': 'h2h', 'outcomes': [{'name': teams['home']['team']['name'], 'price': -120},
                                                    {'name': teams['away']['team']['name'], 'price': 110}]},
                         {'key': 'totals', 'outcomes': [{'name': 'Over', 'point': 8.5}, {'name': 'Under', 'point': 8.5}]}]}]})
    for name, payload in [('official', {'totalGames': 2, 'dates': [{'games': games}]}), ('bbs', {'data': bbs}), ('odds', odds)]:
        (folder/(name+'.json')).write_bytes(encode(envelope(payload)))
    (folder/'feeds.json').write_bytes(encode({'games': {str(g['gamePk']): envelope(feed(g)) for g in games}}))
    (folder/'history.json.gz').write_bytes(gzip.compress(encode({'games': history, 'prior_observed_at': AT}), mtime=0))
    # In CI only the classifier is a spy; real accepted Poisson weights and
    # the production feature builder / Arrow writer still run end to end.
    model_root = tmp_path/'models'; model_root.mkdir()
    poisson_bytes = (daily.ROOT/'poisson_model.json').read_bytes()
    (model_root/'poisson_model.json').write_bytes(poisson_bytes)
    (folder/'model.txt').write_bytes(b'synthetic-classifier-spy')
    (model_root/'model_refs.json').write_bytes(encode({
        'lightgbm': {'sha256': hashlib.sha256((folder/'model.txt').read_bytes()).hexdigest()},
        'poisson': {'file': 'poisson_model.json', 'sha256': hashlib.sha256(poisson_bytes).hexdigest()}}))
    calls = []
    class Classifier:
        def feature_name(self):
            return ['home_offense_ops_30d', 'market_home_prob']

        def predict(self, x):
            calls.append(len(x))
            return np.full(len(x), .55)
    monkeypatch.setattr(daily, 'ROOT', model_root)
    monkeypatch.setattr(daily.lgb, 'Booster', lambda **kwargs: Classifier())
    seal(folder)
    return folder, tmp_path/'output', calls, games


def seal(folder, at=AT):
    (folder/'capture.json').write_bytes(encode({'system': 'KS1', 'phase': 5, 'date': DATE, 'as_of': at, 'errors': [],
        'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.name != 'capture.json'}}))


def advance(folder, output, at=DATE+'T10:01:00+00:00'):
    (folder/'previous.parquet').write_bytes((output/'predictions.parquet').read_bytes())
    for provider in ('official', 'bbs', 'odds'):
        path = folder/(provider+'.json'); payload = json.loads(path.read_bytes())['payload']
        if provider == 'odds':
            for event in payload:
                for book in event['bookmakers']:
                    book['last_update'] = at
        path.write_bytes(encode(envelope(payload, at)))
    feeds = json.loads((folder/'feeds.json').read_bytes())
    for pk, entry in feeds['games'].items():
        if entry.get('payload') is not None:
            feeds['games'][pk] = envelope(entry['payload'], at)
    (folder/'feeds.json').write_bytes(encode(feeds)); seal(folder, at)


def change_feed(folder, modify, pk='1'):
    path = folder/'feeds.json'; feeds = json.loads(path.read_bytes())
    modify(feeds['games'][pk]['payload'])
    feeds['games'][pk] = envelope(feeds['games'][pk]['payload'], feeds['games'][pk]['receipt']['as_of'])
    path.write_bytes(encode(feeds))
    seal(folder, json.loads((folder/'capture.json').read_bytes())['as_of'])


def test_unchanged_poll_reuses_every_row_and_parquet_bytes_without_inference(capture):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output)
    for row in first.to_pylist():
        profile = json.loads(row['starter_profile_json'])
        assert row['starter_profile_contract'] == 'KS1-starter-profile-v2'
        assert row['starter_profile_sha256'] == profile['sha256']
        assert row['starter_profile_semantic_sha256'] == profile['semantic_sha256']
        assert profile['source_roles']['fixture_crosscheck'] == 'Big_Balls_Data_matches_only'
        assert profile['source_roles']['market_context'] == 'The_Odds_API_only'
        assert profile['sides']['home']['starter_id'] == row['home_starter_id']
        assert profile['sides']['home']['metrics']['pitch_hand_left'] == 1
        assert profile['sides']['home']['metrics']['opponent_lhb_pct'] == 100
        assert profile['sides']['home']['window_statuses']['30d'] == 'SOURCE_INCOMPLETE'
        assert 'xera_30d' in profile['sides']['home']['unavailable_exact_metrics']
    body = (out/'predictions.parquet').read_bytes()
    advance(folder, out)
    history = json.loads(gzip.decompress((folder/'history.json.gz').read_bytes()))
    history['prior_observed_at'] = DATE+'T10:01:00+00:00'
    (folder/'history.json.gz').write_bytes(gzip.compress(encode(history), mtime=0)); seal(folder, DATE+'T10:01:00+00:00')
    second, report, out = daily.predict(folder, output)
    assert first.equals(second) and body == (out/'predictions.parquet').read_bytes()
    assert calls == [2] and report['newly_scored'] == 0 and report['unchanged_rows'] == 2
    assert all(r['status'] == 'confirmed_lineups' for r in second.to_pylist())


def test_incomplete_pitcher_history_masks_derived_context(capture, monkeypatch):
    folder, output, _, _ = capture
    seen = []
    class ContextClassifier:
        def feature_name(self):
            return ['home_pitcher_context_quality', 'away_pitcher_context_quality']

        def predict(self, values):
            seen.append(values.copy())
            return np.full(len(values), .5)

    monkeypatch.setattr(daily.lgb, 'Booster', lambda **kwargs: ContextClassifier())
    daily.predict(folder, output)
    assert len(seen) == 1
    assert seen[0].isna().all().all()


def test_savant_delay_preserves_verified_official_pitcher_results(capture):
    from tests.ks1_recent.test_recent import full_game
    folder, output, _, games = capture
    stats = {'outs': 18, 'earnedRuns': 2, 'runs': 3, 'hits': 4,
             'homeRuns': 1, 'baseOnBalls': 2, 'hitBatsmen': 1,
             'strikeOuts': 7, 'battersFaced': 25, 'wins': 1, 'losses': 0,
             'gamesStarted': 1, 'numberOfPitches': 90}
    history = []
    for pk, date in ((11, '2026-09-09'), (12, '2026-09-04'), (13, '2026-08-30')):
        game = full_game(pk, date, 101, stats)
        for side in ('home', 'away'):
            game['teams'][side]['team'] = games[0]['teams'][side]['team']
        history.append(game)
    payload = {'games': history, 'prior_observed_at': AT,
               'current30_history_complete': True, 'current_year_history_complete': True,
               'prior_year_history_complete': True, 'statcast_coverage_complete': False,
               'current_year_statcast_complete': False, 'prior_year_statcast_complete': False}
    path = folder/'history.json.gz'
    path.write_bytes(gzip.compress(encode(payload), mtime=0)); seal(folder)
    rows, _, _ = daily.predict(folder, output)
    profile = json.loads(rows.to_pylist()[0]['starter_profile_json'])['sides']['home']
    for window, wins in (('7d', 2), ('30d', 3), ('last3', 3)):
        assert profile['metrics']['era_'+window] == 3
        assert profile['metrics']['ra9_'+window] == 4.5
        assert profile['metrics']['wins_'+window] == wins
        assert profile['metrics']['fip_'+window] is not None
        assert profile['metrics']['velocity_'+window] is None
        assert profile['metrics']['xfip_'+window] is None
        assert profile['results_window_statuses'][window] == 'COMPLETE'
        assert profile['window_statuses'][window] == 'SOURCE_INCOMPLETE'
    payload['current30_history_complete'] = False
    path.write_bytes(gzip.compress(encode(payload), mtime=0)); seal(folder)
    rows, _, _ = daily.predict(folder, output)
    profile = json.loads(rows.to_pylist()[0]['starter_profile_json'])['sides']['home']
    assert profile['metrics']['era_30d'] is None
    assert profile['results_window_statuses']['30d'] == 'SOURCE_INCOMPLETE'


def test_profile_semantics_refresh_but_audit_timestamp_does_not():
    row = {'game_id': '1', 'starter_profile_semantic_sha256': 'semantic-one',
           'starter_profile_sha256': 'full-one', 'starter_profile_json': '{"as_of":"one"}',
           'as_of': '2026-09-10T10:00:00Z'}
    features = {'market_home_prob': .5}
    first = fingerprint(row, features, {'market_home_prob'})
    audit_only = {**row, 'starter_profile_sha256': 'full-two',
                  'starter_profile_json': '{"as_of":"two"}', 'as_of': '2026-09-10T10:01:00Z'}
    assert fingerprint(audit_only, features, {'market_home_prob'}) == first
    changed = {**audit_only, 'starter_profile_semantic_sha256': 'semantic-two'}
    assert fingerprint(changed, features, {'market_home_prob'}) != first


def test_profile_window_is_not_complete_when_exact_pitch_coverage_is_partial():
    row = {'starter_source': 'official_MLB_probable_pitcher',
           '_pitcher_history_coverage': {'7d': True},
           'home_starter_id': '101', 'home_starter_name': 'Home Starter',
           'home_starter_status': 'RESOLVED',
           'away_starter_id': '102', 'away_starter_name': 'Away Starter',
           'away_starter_status': 'RESOLVED'}
    features = {
        'home_starter_appearances_7d': 1, 'home_starter_complete_7d': 0,
        'away_starter_appearances_7d': 1, 'away_starter_complete_7d': 0,
    }

    profile = daily.starter_profile(row, features, AT, AT)

    assert profile['sides']['home']['window_statuses']['7d'] == 'SOURCE_INCOMPLETE'
    assert profile['sides']['away']['window_statuses']['7d'] == 'SOURCE_INCOMPLETE'


def start_adjusted_inputs(folder):
    official = json.loads((folder/'official.json').read_bytes())['payload']
    games = official['dates'][0]['games']
    games[0]['doubleHeader'] = 'N'
    games[0]['status']['startTimeTBD'] = False
    games[1]['gameDate'] = DATE+'T22:00:00Z'
    bbs = json.loads((folder/'bbs.json').read_bytes())['payload']
    bbs['data'][0]['kickoff_utc'] = DATE+'T19:55:00Z'
    bbs['data'][1]['kickoff_utc'] = games[1]['gameDate']
    (folder/'official.json').write_bytes(encode(envelope(official)))
    (folder/'bbs.json').write_bytes(encode(envelope(bbs)))
    seal(folder)


@pytest.mark.parametrize('abstract,detailed,bbs_status', [
    ('Preview', 'Pre-Game', 'inprogress'), ('Live', 'In Progress', 'live'), ('Final', 'Final', 'finished')])
def test_adjusted_fixture_status_transition_preserves_lock_and_refreshes_later_game(capture, abstract, detailed, bbs_status):
    folder, output, calls, _ = capture
    start_adjusted_inputs(folder)
    first, _, out = daily.predict(folder, output)
    original_lock = next(r for r in first.to_pylist() if r['game_id'] == '1')
    at = DATE+'T20:01:00+00:00'
    advance(folder, out, at)
    official = json.loads((folder/'official.json').read_bytes())['payload']
    official['dates'][0]['games'][0]['status'].update(abstractGameState=abstract, detailedState=detailed)
    official['dates'][0]['games'][1]['teams']['home']['probablePitcher'] = {'id': 777, 'fullName': 'New starter'}
    (folder/'official.json').write_bytes(encode(envelope(official, at)))
    bbs = json.loads((folder/'bbs.json').read_bytes())['payload']
    bbs['data'][0]['status'] = bbs_status
    (folder/'bbs.json').write_bytes(encode(envelope(bbs, at)))
    seal(folder, at)
    result, report, _ = daily.predict(folder, output)
    assert next(r for r in result.to_pylist() if r['game_id'] == '1') == original_lock
    assert calls == [2, 1] and report['preserved_pregame_rows'] == 1
    assert report['changes'] == [{'game_id': '2', 'reason': 'starter_changed'}]


def test_adjusted_identity_does_not_admit_new_nonpregame_predictions(capture):
    folder, output, calls, _ = capture
    start_adjusted_inputs(folder)
    bbs = json.loads((folder/'bbs.json').read_bytes())['payload']
    bbs['data'][0]['status'] = 'live'
    (folder/'bbs.json').write_bytes(encode(envelope(bbs)))
    seal(folder)
    with pytest.raises(ValueError, match='BBS and official pregame status disagree'):
        daily.predict(folder, output)
    assert calls == []
    official = json.loads((folder/'official.json').read_bytes())['payload']
    official['dates'][0]['games'][0]['status']['detailedState'] = 'Postponed'
    (folder/'official.json').write_bytes(encode(envelope(official)))
    seal(folder)
    result, report, _ = daily.predict(folder, output)
    assert result['game_id'].to_pylist() == ['2']
    assert report['exclusions'] == [{'game_id': '1', 'reason': 'not_scheduled_before_T10'}]


def test_existing_prediction_survives_early_bbs_live_status_and_other_game_refreshes(capture):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output)
    original = next(r for r in first.to_pylist() if r['game_id'] == '1')
    advance(folder, out)

    official = json.loads((folder/'official.json').read_bytes())['payload']
    official['dates'][0]['games'][0]['status'].update(
        abstractGameState='Preview', detailedState='Delayed Start', reason='Wet Grounds')
    official['dates'][0]['games'][1]['teams']['home']['probablePitcher'] = {
        'id': 777, 'fullName': 'New starter'}
    (folder/'official.json').write_bytes(encode(envelope(official, DATE+'T10:01:00+00:00')))
    change_feed(folder, lambda p: p['gameData']['probablePitchers'].update(
        home={'id': 777, 'fullName': 'New starter'}), pk='2')

    bbs = json.loads((folder/'bbs.json').read_bytes())['payload']
    bbs['data'][0]['status'] = 'live'
    (folder/'bbs.json').write_bytes(encode(envelope(bbs, DATE+'T10:01:00+00:00')))
    seal(folder, DATE+'T10:01:00+00:00')

    result, report, _ = daily.predict(folder, output)
    assert next(r for r in result.to_pylist() if r['game_id'] == '1') == original
    assert next(r for r in result.to_pylist() if r['game_id'] == '2')['home_starter_id'] == '777'
    assert calls == [2, 1]
    assert report['provider_status_disagreement_retained_rows'] == 1
    assert report['provider_status_disagreement_retained_game_ids'] == ['1']
    assert report['exclusions'] == [{
        'game_id': '1',
        'reason': 'provider_pregame_status_disagreement_retained',
        'official_detailed_state': 'Delayed Start',
        'bbs_status': 'live',
    }]


def test_scratch_rebuilds_only_affected_game_and_next_rerun_is_noop(capture):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output); advance(folder, out)
    change_feed(folder, lambda p: p['gameData']['probablePitchers'].update(home={'id': 9999, 'fullName': 'Synthetic replacement'}))
    second, report, out = daily.predict(folder, output)
    assert calls == [2, 1] and report['changes'] == [{'game_id': '1', 'reason': 'starter_changed'}]
    assert first.to_pylist()[1] == second.to_pylist()[1]
    assert second.to_pylist()[0]['home_starter_id'] == '9999'
    assert second.to_pylist()[0]['as_of'] != first.to_pylist()[0]['as_of']
    body = (out/'predictions.parquet').read_bytes(); advance(folder, out, DATE+'T10:02:00+00:00')
    third, report, out = daily.predict(folder, output)
    assert third.equals(second) and body == (out/'predictions.parquet').read_bytes()
    assert report['newly_scored'] == 0 and calls == [2, 1]


def test_missing_then_confirmed_lineup_changes_only_one_game(capture):
    folder, output, calls, games = capture
    change_feed(folder, lambda p: p['liveData']['boxscore']['teams']['home'].update(battingOrder=[]))
    first, _, out = daily.predict(folder, output)
    row = first.to_pylist()[0]
    assert row['lineup_status'] == row['home_lineup_status'] == 'projected'
    assert row['home_lineup_ids'] is None and row['home_offense_source'] == 'team_prior'
    advance(folder, out)
    change_feed(folder, lambda p: p['liveData']['boxscore']['teams'].update(home=feed(games[0])['liveData']['boxscore']['teams']['home']))
    second, report, _ = daily.predict(folder, output)
    assert report['changes'] == [{'game_id': '1', 'reason': 'lineup_changed'}] and calls == [2, 1]
    assert second.to_pylist()[0]['lineup_status'] == 'confirmed'
    assert first.to_pylist()[1] == second.to_pylist()[1]
    # Identity evidence does not silently redefine frozen team offense inputs.
    assert first['lambda_home'].to_pylist() == second['lambda_home'].to_pylist()


def test_order_change_with_nine_confirmed_players_only_rebuilds_that_game(capture):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output); advance(folder, out)
    def swap(payload):
        box = payload['liveData']['boxscore']['teams']['home']
        box['battingOrder'][0], box['battingOrder'][1] = box['battingOrder'][1], box['battingOrder'][0]
        for slot, pid in enumerate(box['battingOrder'], 1):
            box['players']['ID'+str(pid)]['battingOrder'] = str(slot*100)
    change_feed(folder, swap)
    second, report, _ = daily.predict(folder, output)
    assert calls == [2, 1] and report['changes'] == [{'game_id': '1', 'reason': 'lineup_changed'}]
    assert first.to_pylist()[1] == second.to_pylist()[1]
    assert second.to_pylist()[0]['lineup_status'] == 'confirmed'


def test_market_total_change_updates_only_its_game_even_when_win_probability_same(capture):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output); advance(folder, out)
    path = folder/'odds.json'; value = json.loads(path.read_bytes())
    for outcome in value['payload'][0]['bookmakers'][0]['markets'][1]['outcomes']:
        outcome['point'] = 9.5
    path.write_bytes(encode(envelope(value['payload'], DATE+'T10:01:00+00:00'))); seal(folder, DATE+'T10:01:00+00:00')
    second, report, _ = daily.predict(folder, output)
    assert report['newly_scored'] == 1 and calls == [2, 1]
    assert first.to_pylist()[1] == second.to_pylist()[1]
    assert second.to_pylist()[0]['edge_total'] == pytest.approx(first.to_pylist()[0]['edge_total']-1)


@pytest.mark.parametrize('fault', ['empty', 'duplicate', 'substitute', 'wrong_slot', 'malformed_players', 'wrong_game', 'wrong_team', 'live', 'future', 'stale', 'failed'])
def test_invalid_lineup_evidence_falls_back_without_crashing(capture, fault):
    _, _, _, games = capture
    game = games[0]; payload = feed(game); box = payload['liveData']['boxscore']['teams']['home']
    at = AT
    if fault == 'empty': box['battingOrder'] = []
    if fault == 'duplicate': box['battingOrder'][1] = box['battingOrder'][0]
    if fault == 'substitute': next(iter(box['players'].values()))['gameStatus']['isSubstitute'] = True
    if fault == 'wrong_slot': next(iter(box['players'].values()))['battingOrder'] = '200'
    if fault == 'malformed_players': box['players'] = []
    if fault == 'wrong_game': payload['gameData']['game']['pk'] = 88
    if fault == 'wrong_team': box['team'] = {'id': 88}
    if fault == 'live': payload['gameData']['status']['abstractGameState'] = 'Live'
    if fault == 'future': at = DATE+'T10:01:00+00:00'
    if fault == 'stale': at = DATE+'T09:00:00+00:00'
    entry = {'payload': None, 'receipt': {'status': 503}} if fault == 'failed' else envelope(payload, at)
    row = observe(game, entry, AT)
    assert row['lineup_status'] == row['home_lineup_status'] == 'projected'
    assert row['home_offense_source'] == 'team_prior' and row['status']


def test_cleared_pitcher_is_not_resurrected_from_earlier_schedule(capture):
    _, _, _, games = capture; payload = feed(games[0])
    payload['gameData']['probablePitchers'].pop('home')
    row = observe(games[0], envelope(payload), AT)
    assert row['home_starter_id'] is None and row['home_starter_status'] == 'missing'
    assert row['status'] == 'projected_missing_starter'


def test_frozen_rows_do_not_change_or_rescore_after_cutoff(capture):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output); advance(folder, out, DATE+'T19:51:00+00:00')
    change_feed(folder, lambda p: p['gameData']['probablePitchers'].update(home={'id': 9999}))
    second, report, _ = daily.predict(folder, output)
    assert first.equals(second) and calls == [2] and report['preserved_pregame_rows'] == 2


@pytest.mark.parametrize('mutation', [
    {'codedGameState': 'I'}, {'statusCode': 'I'}, {'detailedState': 'In Progress'},
    {'codedGameState': 'T', 'statusCode': 'T', 'detailedState': 'Suspended'},
    {'abstractGameState': 'Final'}, {'statusCode': None},
])
def test_only_exact_warmup_tuple_is_additionally_pregame(mutation):
    assert pregame_status(WARMUP)
    assert not pregame_status(dict(WARMUP, **mutation))


def test_warmup_refreshes_and_collects_feed_only_before_existing_cutoff(capture):
    folder, output, calls, games = capture
    first, _, out = daily.predict(folder, output)
    at = DATE+'T19:42:00+00:00'
    advance(folder, out, at)
    official = json.loads((folder/'official.json').read_bytes())['payload']
    game = official['dates'][0]['games'][0]; game['status'] = dict(WARMUP)
    (folder/'official.json').write_bytes(encode(envelope(official, at)))
    change_feed(folder, lambda p: p['gameData'].update(status=dict(WARMUP)))
    seal(folder, at)
    second, report, out = daily.predict(folder, output)
    assert second['game_id'].to_pylist() == ['1', '2']
    assert report['removed_game_ids'] == [] and report['preserved_pregame_rows'] == 0
    assert second.to_pylist()[0]['lineup_status'] == 'confirmed'
    assert second.to_pylist()[0]['lineup_source_status'] == 'verified_pregame_feed'
    requested = []
    def requester(provider, base, path, params):
        requested.append(path)
        return envelope(feed(game), at)
    collected = lineup_feeds(DATE, [game], requester=requester,
                             now=datetime.fromisoformat(at))
    assert set(collected['games']) == {'1'} and len(requested) == 1
    assert lineup_feeds(DATE, [game], requester=requester,
                        now=datetime.fromisoformat(DATE+'T19:50:01+00:00')) == {'games': {}}
    assert len(requested) == 1
    before_calls = list(calls)
    advance(folder, out, DATE+'T19:51:00+00:00')
    change_feed(folder, lambda p: p['gameData']['probablePitchers'].update(home={'id': 9999}))
    third, report, _ = daily.predict(folder, output)
    assert third.equals(second) and calls == before_calls
    assert report['preserved_pregame_rows'] == 2


@pytest.mark.parametrize('transition', ['Live', 'Final', 'earlier_start'])
def test_early_ineligible_transition_cannot_erase_a_published_pick(capture, transition):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output)
    original_bytes = (out/'predictions.parquet').read_bytes()
    at = DATE+'T19:42:00+00:00'  # Eight minutes before the stored T-10.
    advance(folder, out, at)
    official = json.loads((folder/'official.json').read_bytes())['payload']
    game = official['dates'][0]['games'][0]
    if transition == 'earlier_start':
        game['gameDate'] = DATE+'T19:49:00Z'
        bbs = json.loads((folder/'bbs.json').read_bytes())['payload']
        bbs['data'][0]['kickoff_utc'] = game['gameDate']
        (folder/'bbs.json').write_bytes(encode(envelope(bbs, at)))
    else:
        game['status'].update(abstractGameState=transition, detailedState=transition)
    (folder/'official.json').write_bytes(encode(envelope(official, at))); seal(folder, at)
    with pytest.raises(ValueError, match='unexpected published prediction removal: 1'):
        daily.predict(folder, output)
    assert (out/'predictions.parquet').read_bytes() == original_bytes
    # The failed attempt cannot invent a lock or overwrite history. A later
    # capture can preserve the original bytes using the existing stored T-10.
    advance(folder, out, DATE+'T19:51:00+00:00')
    after, report, _ = daily.predict(folder, output)
    assert after.equals(first) and report['preserved_pregame_rows'] == 2


@pytest.mark.parametrize('reason', ['Postponed', 'Cancelled'])
def test_explicit_pre_cutoff_withdrawal_remains_possible(capture, reason):
    folder, output, _, _ = capture
    _, _, out = daily.predict(folder, output); advance(folder, out)
    official = json.loads((folder/'official.json').read_bytes())['payload']
    official['dates'][0]['games'][0]['status']['detailedState'] = reason
    (folder/'official.json').write_bytes(encode(envelope(official, DATE+'T10:01:00+00:00')))
    seal(folder, DATE+'T10:01:00+00:00')
    table, report, _ = daily.predict(folder, output)
    assert table['game_id'].to_pylist() == ['2']
    assert report['withdrawn_games'] == [{'date': DATE, 'game_id': '1', 'reason': reason}]


def test_stale_previous_and_unbound_cache_fail_before_inference(capture):
    folder, output, calls, _ = capture
    _, _, out = daily.predict(folder, output); advance(folder, out, DATE+'T09:59:00+00:00')
    with pytest.raises(ValueError, match='newer'):
        daily.predict(folder, output)
    manifest = json.loads((folder/'capture.json').read_bytes()); manifest['files'].pop('previous.parquet')
    (folder/'capture.json').write_bytes(encode(manifest))
    with pytest.raises(ValueError, match='unbound'):
        daily.predict(folder, output)
    assert calls == [2]


def test_missing_passive_context_reaches_model_as_explicit_missingness(capture, monkeypatch):
    folder, output, _, _ = capture
    seen = []

    class MissingContextClassifier:
        def feature_name(self):
            return ['home_lineup_ops_30d_missing',
                    'away_bullpen_context_fip_30d_missing']

        def predict(self, values, pred_contrib=False):
            seen.append(values.copy())
            if pred_contrib:
                return np.column_stack((np.zeros((len(values), 2)),
                                        np.zeros(len(values))))
            return np.full(len(values), .55)

    monkeypatch.setattr(daily.lgb, 'Booster', lambda **kwargs: MissingContextClassifier())
    table, report, _ = daily.predict(folder, output)
    assert len(table) == 2
    assert len(seen) >= 1
    assert (seen[0].to_numpy() == 1.0).all()
    assert report['lineup_bullpen_profile_rows'] == 0
    assert all('FAIL_CLOSED' in value
               for value in table['lineup_bullpen_profile_status'].to_pylist())


def test_feed_collection_is_bounded_and_missing_feed_is_optional(capture):
    _, _, _, games = capture; calls = []
    def requester(provider, base, path, params):
        calls.append(path)
        raise ProviderFailure({'provider': provider, 'status': 503, 'body_shape': {'error': 'str'}})
    result = lineup_feeds(DATE, games, requester=requester, now=datetime(2026, 9, 10, 10, tzinfo=timezone.utc))
    assert len(calls) == 2 and all(v['payload'] is None for v in result['games'].values())
    assert lineup_feeds(DATE, games, requester=requester, now=datetime(2026, 9, 10, 20, tzinfo=timezone.utc)) == {'games': {}}
    assert len(calls) == 2


def test_phase4_rows_upgrade_once_including_frozen_status_defaults(capture):
    folder, output, calls, _ = capture
    table, _, out = daily.predict(folder, output)
    new_columns = ['status', 'home_lineup_status', 'away_lineup_status', 'home_lineup_ids', 'away_lineup_ids',
                   'home_offense_source', 'away_offense_source', 'lineup_source_status', 'starter_feature_source']
    old = table.drop(new_columns)
    (out/'predictions.parquet').write_bytes(parquet_bytes(old))
    advance(folder, out)
    upgraded, report, out = daily.predict(folder, output)
    assert calls == [2, 2] and all(r['reason'] == 'phase4_upgrade' for r in report['changes'])
    (out/'predictions.parquet').write_bytes(parquet_bytes(old))
    advance(folder, out, DATE+'T19:51:00+00:00')
    frozen, report, out = daily.predict(folder, output)
    assert frozen.select(['p_home', 'lambda_home', 'lambda_away', 'as_of']).equals(old.select(['p_home', 'lambda_home', 'lambda_away', 'as_of']))
    assert all(r['status'] and r['lineup_status'] == 'projected' for r in frozen.to_pylist())
    assert report['migrated_frozen_game_ids'] == ['1', '2'] and calls == [2, 2]
    advance(folder, out, DATE+'T19:52:00+00:00')
    repeated, report, _ = daily.predict(folder, output)
    assert repeated.equals(frozen) and report['migrated_frozen_game_ids'] == []


def test_scratch_overwrites_same_date_object_and_preserves_other_date(capture, monkeypatch):
    folder, output, _, _ = capture
    # In-memory store models conditional object replacement; it cannot access AWS.
    objects, writes = {}, []
    class Store:
        def get_object(self, Bucket, Key, **kwargs):
            from botocore.exceptions import ClientError
            import io
            if Key not in objects:
                raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
            return {'Body': io.BytesIO(objects[Key]), 'ETag': hashlib.sha256(objects[Key]).hexdigest()}

        def put_object(self, Bucket, Key, Body, **kwargs):
            if Key in objects:
                assert kwargs['IfMatch'] == hashlib.sha256(objects[Key]).hexdigest()
            else:
                assert kwargs['IfNoneMatch'] == '*'
            objects[Key] = Body; writes.append(Key)
            return {}
    for key, value in {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'KirtKurt/parlay-platform',
                       'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'schedule',
                       'GITHUB_WORKFLOW_REF': 'KirtKurt/parlay-platform/.github/workflows/mlb-research-ingestion.yml@refs/heads/main'}.items():
        monkeypatch.setenv(key, value)
    other = daily.PREFIX+'date=2026-09-09/predictions.parquet'; objects[other] = b'untouched'
    table, report, out = daily.predict(folder, output); store = Store()
    daily.publish(store, 'test', table, report, out)
    key = daily.PREFIX+'date='+DATE+'/predictions.parquet'
    old = objects[key]; advance(folder, out)
    change_feed(folder, lambda p: p['gameData']['probablePitchers'].update(home={'id': 9999}))
    current, report, out = daily.predict(folder, output)
    report['source_capture']['previous_etag'] = hashlib.sha256(old).hexdigest()
    daily.publish(store, 'test', current, report, out)
    assert objects[key] != old and objects[other] == b'untouched'
    assert all(k.startswith(daily.PREFIX+'date='+DATE+'/') for k in writes)
    assert pq.read_table(pa.BufferReader(objects[key])).to_pylist()[1] == pq.read_table(pa.BufferReader(old)).to_pylist()[1]
    assert daily.publish(store, 'test', current, report, out)['write_keys'] == []
    report['source_capture']['verification_only'] = True
    with pytest.raises(ValueError, match='synthetic'):
        daily.publish(store, 'test', current, report, out)
