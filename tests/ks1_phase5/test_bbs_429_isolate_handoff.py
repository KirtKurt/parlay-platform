"""Degraded BBS 429 catalogue must not kill the official slate.

Empty data is a missing catalogue. Official games become missing_bbs_identity
exclusions. Truncation, schema change, and duplicate BBS-to-one-game stay fatal.
"""
from ks1.bbs_degrade import degraded_bbs_capture
from ks1.daily import Crosswalk, bbs_assignments


def _game(pk, home, away):
    return {
        'gamePk': pk,
        'gameDate': '2026-10-07T23:10:00Z',
        'doubleHeader': 'N',
        'status': {'startTimeTBD': False, 'detailedState': 'Scheduled'},
        'teams': {
            'home': {'team': {'id': home, 'name': 'Home '+str(home)}},
            'away': {'team': {'id': away, 'name': 'Away '+str(away)}},
        },
    }


def test_degraded_429_catalogue_isolates_official_games():
    degraded = degraded_bbs_capture({'provider': 'bbs', 'status': 429, 'endpoint': 'https://api.bigballsdata.com/v1/matches'})
    schedule = [_game(1, 147, 139)]
    crosswalk = Crosswalk([], schedule)
    assigned = bbs_assignments(degraded['payload'], schedule, crosswalk, '2026-10-07', isolate_unmatched=True)
    assert assigned == {}
    assert crosswalk.bbs_identity_exclusions == []
    assert degraded['receipt']['degraded'] == 'bbs_unavailable'


def test_duplicate_bbs_to_one_game_stays_fatal():
    payload = {'data': [
        {'id': 'a', 'kickoff_utc': '2026-10-07T23:10:00Z', 'sport': 'baseball', 'league': 'mlb', 'status': 'scheduled',
         'home': {'id': 'h', 'name': 'Home 147'}, 'away': {'id': 'w', 'name': 'Away 139'}},
        {'id': 'b', 'kickoff_utc': '2026-10-07T23:10:30Z', 'sport': 'baseball', 'league': 'mlb', 'status': 'scheduled',
         'home': {'id': 'h2', 'name': 'Home 147'}, 'away': {'id': 'w2', 'name': 'Away 139'}},
    ]}
    schedule = [_game(1, 147, 139)]
    crosswalk = Crosswalk([], schedule)
    try:
        bbs_assignments(payload, schedule, crosswalk, '2026-10-07', isolate_unmatched=True)
    except ValueError as exc:
        assert 'multiple BBS IDs map to one official game' in str(exc)
    else:
        raise AssertionError('duplicate BBS-to-one-game must stay fatal')
