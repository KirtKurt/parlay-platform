"""Game proof cannot be promoted into whole-date or missing-outcome proof."""
from copy import deepcopy
import hashlib

import pytest

from ks1.features import Features
from ks1.inventory import RESEARCH, encode
from ks1.statcast_history import load_training_statcast
from tests.ks1.test_statcast_history import RetainedS3, fixture


def two_games():
    bundle, payload, key = fixture()
    other = deepcopy(bundle['full'][0])
    other['officialGamePk'] = 2
    bundle['full'].append(other)
    bundle['schedule'].append({**bundle['schedule'][0], 'gamePk': 2})
    payload['rows'].extend({**row, 'game_pk': '2'} for row in list(payload['rows']))
    return bundle, payload, key


def test_bad_game_does_not_erase_independently_verified_game_or_certify_date():
    bundle, payload, key = two_games()
    payload['rows'].pop()  # Game 2 fails physical and PA inventory.
    original = deepcopy(payload)
    bundle['statcast'] = deepcopy(payload['rows'])
    bundle['statcast_verified_games'] = ['1', '2']
    bundle['statcast_physical_games'] = ['1', '2']
    bundle['statcast_retained_dates'] = [payload['date']]
    bundle['statcast_physical_dates'] = [payload['date']]
    bundle['statcast_coverage_complete'] = False
    report = load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'b')
    assert payload == original
    assert bundle['statcast_verified_games'] == ['1']
    assert bundle['statcast_physical_games'] == ['1']
    assert payload['date'] not in bundle['statcast_retained_dates']
    assert payload['date'] not in bundle['statcast_physical_dates']
    assert bundle['statcast_coverage_complete'] is False
    assert report['verified_physical_games'] == report['verified_outcome_games'] == 1
    assert report['verified_pitch_objects'] == report['verified_physical_pitch_objects'] == 0
    assert len(report['errors']) == 1
    assert bundle['source_receipts'][0]['versionId'] == 'v1'
    engine = Features(bundle['full'], bundle['statcast'],
                      statcast_retained_dates=bundle['statcast_retained_dates'],
                      statcast_physical_dates=bundle['statcast_physical_dates'],
                      statcast_verified_games=bundle['statcast_verified_games'],
                      statcast_physical_games=bundle['statcast_physical_games'])
    assert engine.statcast('151', ['1'], 18)['xwoba'] == .5
    assert engine.statcast('151', ['2'], 18)['xwoba'] is None
    _, lineup = engine.lineup_batters_at('2026-09-02T17:50:00Z', range(101, 110), '251', 'R')
    assert lineup['lineup_pitch_type_matchup_xwoba_30d'] is None
    assert lineup['lineup_pitch_type_matchup_whiff_pct_30d'] is None


def test_missing_outcome_retains_physical_game_but_not_its_outcome_proof():
    bundle, payload, key = two_games()
    payload['rows'][-1]['woba_denom'] = ''
    report = load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'b')
    assert bundle['statcast_physical_games'] == ['1', '2']
    assert bundle['statcast_verified_games'] == ['1']
    assert payload['date'] in bundle['statcast_physical_dates']
    assert payload['date'] not in bundle['statcast_retained_dates']
    assert report['verified_outcome_games'] == 1
    assert payload['rows'][-1]['woba_denom'] == ''


@pytest.mark.parametrize('defect', ['wrong_date', 'foreign_game', 'missing_game',
                                   'unversioned', 'checksum', 'derived_envelope'])
def test_invalid_envelope_cannot_supply_partial_game_proof(defect):
    bundle, payload, key = two_games()
    payload['rows'].pop()
    version, checksum = 'v1', None
    if defect == 'wrong_date': payload['rows'][-1]['game_date'] = '2026-09-02'
    elif defect == 'foreign_game': payload['rows'][-1]['game_pk'] = '999'
    elif defect == 'missing_game': payload['rows'] = payload['rows'][:36]
    elif defect == 'unversioned': version = None
    elif defect == 'checksum': checksum = '0' * 64
    elif defect == 'derived_envelope': payload['raw_statcast'] = deepcopy(payload)
    load_training_statcast(bundle, RetainedS3({key: (payload, version, checksum)}), 'b')
    assert bundle['statcast_verified_games'] == []
    assert bundle['statcast_physical_games'] == []
    assert bundle['source_receipts'] == []


def test_revision_upgrades_whole_game_without_splicing_versions():
    bundle, payload, key = two_games()
    first = deepcopy(payload)
    first['rows'].pop()  # Only game 1 is valid here.
    second = deepcopy(payload)
    second['rows'][0]['pitcher'] = '999'  # Only game 2 is valid here.
    revision = RESEARCH + 'sources/statcast-v2-revisions/2026-09-01/' + hashlib.sha256(
        encode([1, 2])).hexdigest() + '.json'
    load_training_statcast(bundle, RetainedS3({key: (first, 'v1', None),
                                            revision: (second, 'v2', None)}), 'b')
    assert bundle['statcast_verified_games'] == ['1', '2']
    assert payload['date'] not in bundle['statcast_retained_dates']
    assert {r['versionId'] for r in bundle['source_receipts']} == {'v1', 'v2'}
    assert all(r['pitcher'] != '999' for r in bundle['statcast'])
    assert len(bundle['statcast']) == 72


def test_later_rejection_revokes_previous_individual_game_proof():
    bundle, payload, key = two_games()
    payload['rows'].pop()
    load_training_statcast(bundle, RetainedS3({key: (payload, 'v1', None)}), 'b')
    assert bundle['statcast_verified_games'] == ['1']
    payload['rows'][0]['pitcher'] = '999'
    load_training_statcast(bundle, RetainedS3({key: (payload, 'v2', None)}), 'b')
    assert bundle['statcast_verified_games'] == bundle['statcast_physical_games'] == []


def test_physical_date_cannot_borrow_other_versions_outcome_proof():
    bundle, payload, key = two_games()
    first = deepcopy(payload)
    first['rows'][-1]['woba_denom'] = ''  # Whole physical date, no game 2 outcome proof.
    second = deepcopy(payload)
    second['rows'][0]['pitcher'] = '999'  # Game 2 outcomes pass in another version.
    revision = RESEARCH + 'sources/statcast-v2-revisions/2026-09-01/' + hashlib.sha256(
        encode([1, 2])).hexdigest() + '.json'
    load_training_statcast(bundle, RetainedS3({key: (first, 'v1', None),
                                            revision: (second, 'v2', None)}), 'b')
    assert bundle['statcast'] == first['rows']
    assert bundle['statcast_verified_games'] == ['1']
    assert bundle['statcast_physical_games'] == ['1', '2']
    assert {r['versionId'] for r in bundle['source_receipts']} == {'v1'}
