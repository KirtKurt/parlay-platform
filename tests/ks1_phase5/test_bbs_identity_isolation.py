"""BBS isolation must not change trusted matches, inference or stored rows."""
from copy import deepcopy
import json

import pytest

from ks1 import daily
from ks1.inventory import encode
from tests.ks1_phase4.test_daily import adjusted_start_fixture
from tests.ks1_phase5.test_refresh import AT, DATE, advance, capture, envelope, seal


def replace_bbs(folder, events, at=AT):
    (folder/'bbs.json').write_bytes(encode(envelope({'data': events}, at)))
    seal(folder, at)


def fixture():
    game, event = adjusted_start_fixture()
    event['kickoff_utc'] = game['gameDate']
    return game, event


def assign(events, games):
    cw = daily.Crosswalk([], games)
    result = daily.bbs_assignments({'data': events}, games, cw, '2026-09-11', isolate_unmatched=True)
    return result, cw


def test_extra_unmatched_event_leaves_matched_predictions_byte_identical(capture):
    folder, output, calls, _ = capture
    first, _, out = daily.predict(folder, output)
    body = (out/'predictions.parquet').read_bytes()
    events = json.loads((folder/'bbs.json').read_bytes())['payload']['data']
    extra = deepcopy(events[0])
    extra.update(id='unmatched-extra')
    extra['home'] = {'id': 'unknown-home', 'name': 'Unknown Team'}
    replace_bbs(folder, events+[extra])
    second, report, out = daily.predict(folder, output)
    assert first.equals(second)
    assert body == (out/'predictions.parquet').read_bytes()
    assert calls == [2, 2]
    assert report['bbs_matched_games'] == 2 and report['exclusions'] == []
    assert report['bbs_identity_exclusions'] == [{
        'bbs_game_id': 'unmatched-extra', 'bbs_start': events[0]['kickoff_utc'],
        'reason': 'unmatched_bbs_identity', 'official_candidate_game_ids': []}]
    crosswalk = json.loads((out/'crosswalk.json').read_bytes())
    assert crosswalk['bbs_identity_exclusions'] == report['bbs_identity_exclusions']
    assert not any(row['bbs_id'] == 'unknown-home' for row in crosswalk['teams'])
