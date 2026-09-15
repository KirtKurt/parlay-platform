from copy import deepcopy

from ks1.statcast_diagnostics import diagnose_date
from tests.ks1.test_statcast_history import fixture
from tests.ks1_recent.test_recovery import MemoryS3


def test_diagnostic_identifies_extra_pitch_without_changing_admission_or_sources():
    bundle, raw, key = fixture()
    extra = {**raw['rows'][0], 'pitch_number': '3', 'pitch_type': '',
             'release_speed': '', 'description': 'intent_ball', 'events': ''}
    raw['rows'].append(extra)
    s3 = MemoryS3()
    s3.seed(key, raw)
    before, writes = deepcopy(bundle), list(s3.writes)
    report = diagnose_date(bundle, s3, 'b', raw['date'])
    counts = report['sources'][0]['counts']
    assert counts['reason'] == 'physical_pitch_or_batter_attribution_mismatch'
    assert counts['pitcher_count_differences'] == [
        {'game_pk': '1', 'player_id': '151', 'expected': 18, 'observed': 19}]
    assert counts['batter_pa_differences'] == []
    assert any(p['description'] == 'intent_ball' and p['counted_as_thrown']
               for p in counts['untracked_row_profiles'])
    assert report['sources'][0]['payload'] == raw
    assert report['source_writes'] == report['provider_requests'] == 0
    assert bundle == before and s3.writes == writes


def test_diagnostic_exposes_duplicate_identity_and_batter_transfer():
    bundle, raw, key = fixture()
    raw['rows'][0]['batter'] = '999'
    raw['rows'].append(deepcopy(raw['rows'][-1]))
    s3 = MemoryS3()
    s3.seed(key, raw)
    counts = diagnose_date(bundle, s3, 'b', raw['date'])['sources'][0]['counts']
    assert counts['duplicate_identities']
    assert counts['ambiguous_at_bats']
    assert counts['batter_pa_differences']


def test_diagnostic_reports_unknown_players_on_uncounted_events():
    bundle, raw, key = fixture()
    raw['rows'][0].update(pitcher='999', batter='998', events='caught_stealing_2b',
                          description='automatic_ball', pitch_type='', release_speed='')
    s3 = MemoryS3(); s3.seed(key, raw)
    counts = diagnose_date(bundle, s3, 'b', raw['date'])['sources'][0]['counts']
    assert counts['invalid_player_rows'][0]['row_index'] == 0
    assert counts['invalid_player_rows'][0]['pitcher'] == '999'
    assert counts['invalid_player_rows'][0]['batter'] == '998'


def test_diagnostic_preserves_guard_reason():
    bundle, raw, key = fixture()
    s3 = MemoryS3(); s3.seed(key, raw)
    original_get = s3.get_object
    s3.get_object = lambda **kwargs: {**original_get(**kwargs), 'VersionId': 'null'}
    report = diagnose_date(bundle, s3, 'b', raw['date'])
    assert report['errors'][0]['reason'] == 'unversioned diagnostic source'
