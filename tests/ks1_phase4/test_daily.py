from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError

from botocore.exceptions import ClientError
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from ks1.daily import Crosswalk, PREFIX, SCHEMA, bbs_assignments, market_for, preserve_frozen, publish
from ks1.features import Features
from ks1.live_inputs import ProviderFailure, bbs_catalogue, fetch
from ks1.publish import parquet_bytes


def schedule():
    return [{'gamePk': '1', 'gameDate': '2026-09-10T20:00:00Z', 'teams': {
        'home': {'team': {'id': 10, 'name': 'Home'}}, 'away': {'team': {'id': 20, 'name': 'Away'}}}}]


def test_market_vig_freshness_and_doubleheader_ambiguity():
    game = schedule()[0]; crosswalk = Crosswalk([], [game]); at = '2026-09-10T10:00:00Z'
    event = {'id': 'odds-1', 'home_team': 'Home', 'away_team': 'Away', 'commence_time': game['gameDate'],
             'bookmakers': [{'key': 'one', 'last_update': at, 'markets': [
                 {'key': 'h2h', 'outcomes': [{'name': 'Home', 'price': -150}, {'name': 'Away', 'price': 130}]},
                 {'key': 'totals', 'outcomes': [{'name': 'Over', 'point': 8.5}, {'name': 'Under', 'point': 8.5}]},
                 {'key': 'spreads', 'outcomes': [{'name': 'Home', 'point': -1.5}, {'name': 'Away', 'point': 1.5}]}]}]}
    result = market_for(game, [event], crosswalk, at)
    assert result['market_home_prob'] == pytest.approx(.6/(.6+100/230))
    assert result['market_total'] == 8.5 and result['market_spread'] == -1.5
    old = deepcopy(event); old['bookmakers'][0]['last_update'] = '2026-09-10T09:00:00Z'
    assert market_for(game, [old], crosswalk, at)['market_home_prob'] is None
    future = deepcopy(event); future['bookmakers'][0]['last_update'] = '2026-09-10T10:01:00Z'
    assert market_for(game, [future], crosswalk, at)['market_home_prob'] is None
    assert market_for(game, [event, {**event, 'id': 'other'}], crosswalk, at)['market_status'] == 'ambiguous_event'


def test_bbs_crosswalk_rejects_ambiguous_ids_and_excludes_other_et_dates():
    games = schedule(); cw = Crosswalk([], games)
    e = {'id': 'bbs-1', 'kickoff_utc': games[0]['gameDate'], 'sport': 'baseball', 'league': 'MLB',
         'home': {'id': 'home-bbs', 'name': 'Home'}, 'away': {'id': 'away-bbs', 'name': 'Away'}}
    yesterday = {**e, 'id': 'yesterday', 'kickoff_utc': '2026-09-10T02:00:00Z'}
    assert list(bbs_assignments({'data': [e, yesterday]}, games, cw, '2026-09-10')) == ['1']
    assert cw.bbs_ids['home-bbs'] == '10'
    with pytest.raises(ValueError, match='multiple BBS'):
        bbs_assignments({'data': [e, {**e, 'id': 'second'}]}, games, cw, '2026-09-10')
    with pytest.raises(ValueError, match='scores'):
        bbs_assignments({'data': {'scores': []}}, games, cw, '2026-09-10')


def test_capture_uses_all_utc_dates_and_never_treats_scores_as_match_ids():
    calls = []
    games = [{'gameDate': '2026-09-10T23:00:00Z'}, {'gameDate': '2026-09-11T01:00:00Z'}]
    def requester(provider, base, path, params, **kwargs):
        calls.append((path, params['date']))
        return {'payload': {'data': {'scores': {}} if path == '/v1/matches' else [{'id': params['date']}]},
                'receipt': {'as_of': '2026-09-10T10:00:00Z'}}
    result = bbs_catalogue('2026-09-10', games, 'unused-test-key', requester)
    assert [r['id'] for r in result['payload']['data']] == ['2026-09-10', '2026-09-11']
    assert calls == [('/v1/matches', '2026-09-10'), ('/v1/stored/matches', '2026-09-10'),
                     ('/v1/matches', '2026-09-11'), ('/v1/stored/matches', '2026-09-11')]


def adjusted_start_fixture():
    # Identity/times observed in retained production artifact 10187948389.
    game = {'gamePk': 823736, 'gameDate': '2026-09-11T23:45:00Z', 'doubleHeader': 'N',
            'status': {'abstractGameState': 'Preview', 'detailedState': 'Scheduled', 'startTimeTBD': False},
            'teams': {'home': {'team': {'id': 158, 'name': 'Milwaukee Brewers'}},
                      'away': {'team': {'id': 113, 'name': 'Cincinnati Reds'}}}}
    event = {'id': '9ba968c3-513f-4e43-81eb-34e1244b07a0', 'kickoff_utc': '2026-09-11T23:40:00Z',
             'sport': 'baseball', 'league': 'MLB', 'status': 'scheduled',
             'home': {'id': '3848ebb8-eb47-4135-b1ba-7354f1fb5b02', 'name': 'Milwaukee Brewers'},
             'away': {'id': '49dfd770-26ca-4b8c-8eb9-68ab2dce1bb2', 'name': 'Cincinnati Reds'}}
    return game, event


def test_observed_five_minute_start_difference_binds_only_unique_fixture():
    game, event = adjusted_start_fixture()
    originals = deepcopy((game, event))
    cw = Crosswalk([], [game])
    assert bbs_assignments({'data': [event]}, [game], cw, '2026-09-11') == {'823736': event}
    assert (game, event) == originals  # Official commence time/T-10 is never rewritten.
    assert cw.game_time_adjustments[0]['official_start'] == '2026-09-11T23:45:00Z'
    assert cw.game_time_adjustments[0]['difference_seconds'] == 300
    assert cw.bbs_ids[event['home']['id']] == '158'


@pytest.mark.parametrize('case', ['over_five_minutes', 'doubleheader', 'two_official_games',
                                  'two_provider_games', 'unknown_team', 'reversed_teams',
                                  'time_tbd', 'missing_dh_flag'])
def test_start_adjustment_refuses_unverified_or_ambiguous_identity(case):
    game, event = adjusted_start_fixture()
    games, events = [game], [event]
    if case == 'over_five_minutes': event['kickoff_utc'] = '2026-09-11T23:39:59Z'
    elif case == 'doubleheader': game['doubleHeader'] = 'Y'
    elif case == 'two_official_games': games.append(dict(deepcopy(game), gamePk=999, gameDate='2026-09-11T20:00:00Z'))
    elif case == 'two_provider_games': events.append(dict(deepcopy(event), id='second', kickoff_utc='2026-09-11T20:00:00Z'))
    elif case == 'unknown_team': event['home']['name'] = 'Unknown Team'
    elif case == 'reversed_teams': event['home'], event['away'] = event['away'], event['home']
    elif case == 'time_tbd': game['status']['startTimeTBD'] = True
    elif case == 'missing_dh_flag': del game['doubleHeader']
    with pytest.raises(ValueError, match='ambiguous or unmatched'):
        bbs_assignments({'data': events}, games, Crosswalk([], games), '2026-09-11')


def test_provider_failure_reports_status_shape_without_echoed_key_or_url():
    secret = 'not-a-real-key'
    def opener(request, **kwargs):
        raise HTTPError(request.full_url, 403, secret, {}, io.BytesIO(json.dumps({'error': {'message': secret}}).encode()))
    with pytest.raises(ProviderFailure) as failure:
        fetch('odds', 'https://api.the-odds-api.com', '/v4/sports/baseball_mlb/odds', {}, key=secret, opener=opener)
    assert failure.value.receipt['status'] == 403
    assert failure.value.receipt['body_shape'] == {'error': {'message': 'str'}}
    assert secret not in str(failure.value) and 'apiKey=' not in str(failure.value)


def test_early_forecast_windows_use_game_date_and_actual_completion_cutoff():
    def g(pk, date, completed):
        return {'officialGamePk': pk, 'startAtUtc': date+'T20:00:00Z', 'completedAtUtc': completed, 'gameType': 'R',
                'teams': {s: {'id': t, 'name': s, 'batting': {}, 'priorStarters': {}, 'relief': {}}
                          for s, t in [('home', 10), ('away', 20)]}}
    games = [g('1', '2026-09-09', '2026-09-10T01:00:00Z'), g('2', '2026-09-08', '2026-09-10T04:00:00Z')]
    result = Features(games).at('2026-09-10T03:00:00Z', '10', game_date='2026-09-10')
    assert result['history_games'] == 1 and result['rest_days'] == 0


def test_preserve_pregame_predictions_after_cutoff_and_reject_stale_overwrite():
    old = pd.DataFrame([{'date': '2026-09-10', 'game_id': '1', 'commence_time': '2026-09-10T20:00:00Z',
                         'as_of': '2026-09-10T10:00:00Z', 'p_home': .55}])
    assert preserve_frozen(old.iloc[:0], old, '2026-09-10', '2026-09-10T21:00:00Z').equals(old)
    with pytest.raises(ValueError, match='newer'):
        preserve_frozen(old.iloc[:0], old, '2026-09-10', '2026-09-10T09:00:00Z')


class MemoryS3:
    def __init__(self):
        self.objects, self.writes = {}, []

    def get_object(self, Bucket, Key, **kwargs):
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        body = self.objects[Key]
        return {'Body': io.BytesIO(body), 'ETag': hashlib.sha256(body).hexdigest()}

    def put_object(self, Bucket, Key, Body, **kwargs):
        if 'IfMatch' in kwargs:
            assert kwargs['IfMatch'] == hashlib.sha256(self.objects[Key]).hexdigest()
        if 'IfNoneMatch' in kwargs:
            assert Key not in self.objects
        self.objects[Key] = Body; self.writes.append(Key)
        return {}


def test_date_publication_isolated_idempotent_and_restricted_to_existing_job(tmp_path, monkeypatch):
    s3 = MemoryS3(); other = PREFIX+'date=2026-09-09/predictions.parquet'; s3.objects[other] = b'previous-date'
    from ks1.platt import raw_model_version
    table = pa.Table.from_pylist([{'date': '2026-09-10', 'game_id': '1', 'as_of': '2026-09-10T10:00:00Z',
                                   'commence_time': '2026-09-10T20:00:00Z', 'p_home': .55,
                                   'model_version': raw_model_version()}], schema=SCHEMA)
    body = parquet_bytes(table);(tmp_path/'predictions.parquet').write_bytes(body)
    (tmp_path/'odds_cache.parquet').write_bytes(parquet_bytes(pa.table({'event_id': ['one']})))
    (tmp_path/'crosswalk.json').write_text('{}')
    (tmp_path/'lineup_cache.json').write_text('{"games":{}}')
    report = {'date': '2026-09-10', 'as_of': '2026-09-10T10:00:00Z', 'parquet_sha256': hashlib.sha256(body).hexdigest(), 'source_capture': {}}
    for name in ('GITHUB_ACTIONS', 'GITHUB_REPOSITORY', 'GITHUB_REF', 'GITHUB_EVENT_NAME', 'GITHUB_WORKFLOW_REF'):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match='existing main'):
        publish(s3, 'test', table, report, tmp_path)
    assert not s3.writes
    for k, v in {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'KirtKurt/parlay-platform', 'GITHUB_REF': 'refs/heads/main',
                 'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_WORKFLOW_REF': 'KirtKurt/parlay-platform/.github/workflows/mlb-research-ingestion.yml@refs/heads/main'}.items():
        monkeypatch.setenv(k, v)
    result = publish(s3, 'test', table, report, tmp_path)
    assert all(k.startswith(PREFIX+'date=2026-09-10/') for k in result['write_keys'])
    assert s3.objects[other] == b'previous-date'
    published = pq.ParquetFile(tmp_path/'predictions.parquet').read()
    assert published.to_pylist()[0]['p_raw'] == published.to_pylist()[0]['p_home'] == .55
    assert publish(s3, 'test', published, report, tmp_path)['write_keys'] == []


def test_partition_parquet_is_readable_by_default_pandas_reader(tmp_path):
    p = tmp_path/'date=2026-09-10';p.mkdir()
    table = pa.Table.from_pylist([{'date': '2026-09-10', 'game_id': '1'}], schema=SCHEMA)
    (p/'predictions.parquet').write_bytes(parquet_bytes(table))
    assert pd.read_parquet(p/'predictions.parquet').date.tolist() == ['2026-09-10']


@pytest.mark.parametrize('fault', ['missing', 'p_home', 'p_raw', 'as_of', 'commence_time', 'home_lineup_ids'])
def test_publication_preflight_rejects_frozen_changes_before_any_writes(tmp_path, monkeypatch, fault):
    from ks1.platt import raw_model_version
    for k, v in {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'KirtKurt/parlay-platform',
                 'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'schedule',
                 'GITHUB_WORKFLOW_REF': 'KirtKurt/parlay-platform/.github/workflows/mlb-research-ingestion.yml@refs/heads/main'}.items():
        monkeypatch.setenv(k, v)
    original = {'date': '2026-09-10', 'game_id': '1', 'as_of': '2026-09-10T10:00:00Z',
                'commence_time': '2026-09-10T20:00:00Z', 'p_home': .55, 'p_raw': .55,
                'model_version': raw_model_version(), 'status': 'confirmed_lineups', 'home_lineup_ids': '[1,2]'}
    old = parquet_bytes(pa.Table.from_pylist([original], schema=SCHEMA))
    changed = dict(original)
    if fault in ('p_home', 'p_raw'): changed[fault] = .6
    if fault == 'as_of': changed[fault] = '2026-09-10T11:00:00Z'
    if fault == 'commence_time': changed[fault] = '2026-09-10T21:00:00Z'
    if fault == 'home_lineup_ids': changed[fault] = '[2,1]'
    table = pa.Table.from_pylist([] if fault == 'missing' else [changed], schema=SCHEMA)
    body = parquet_bytes(table); (tmp_path/'predictions.parquet').write_bytes(body)
    for name in ('odds_cache.parquet', 'crosswalk.json', 'lineup_cache.json'):
        (tmp_path/name).write_bytes(b'new sidecar must not be written')
    store = MemoryS3(); key = PREFIX+'date=2026-09-10/predictions.parquet'
    store.objects[key] = old
    report = {'date': '2026-09-10', 'as_of': '2026-09-10T20:01:00Z',
              'parquet_sha256': hashlib.sha256(body).hexdigest(),
              'source_capture': {'previous_etag': hashlib.sha256(old).hexdigest()}}
    with pytest.raises(ValueError, match='changed or missing frozen prediction'):
        publish(store, 'test', table, report, tmp_path)
    assert store.writes == [] and store.objects[key] == old


def test_existing_workflow_has_one_hourly_schedule_and_no_pr_publication():
    path = Path(__file__).resolve().parents[2]/'.github/workflows/mlb-research-ingestion.yml'
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert workflow['on']['schedule'] == [{'cron': '17 * * * *'}]
    assert workflow['on']['push']['branches'] == ['main']
    assert workflow['jobs']['ingest']['if'] == "github.ref == 'refs/heads/main' && github.event_name != 'pull_request'"
    assert workflow['jobs']['verify-ks1']['if'] == "github.event_name == 'pull_request'"
