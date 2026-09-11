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


def test_unmatched_bbs_event_is_skipped_not_fatal():
    games = schedule(); cw = Crosswalk([], games)
    matched = {'id': 'bbs-1', 'kickoff_utc': games[0]['gameDate'], 'sport': 'baseball', 'league': 'MLB',
               'home': {'id': 'home-bbs', 'name': 'Home'}, 'away': {'id': 'away-bbs', 'name': 'Away'}}
    extra = {'id': '9ba968c3-513f-4e43-81eb-34e1244b07a0', 'kickoff_utc': games[0]['gameDate'],
             'sport': 'baseball', 'league': 'MLB',
             'home': {'id': 'x-home', 'name': 'Unknown Home'}, 'away': {'id': 'x-away', 'name': 'Unknown Away'}}
    assigned = bbs_assignments({'data': [matched, extra]}, games, cw, '2026-09-10')
    assert list(assigned) == ['1']
    assert extra['id'] not in {event['id'] for event in assigned.values()}
