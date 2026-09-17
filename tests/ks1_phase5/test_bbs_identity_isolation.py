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
