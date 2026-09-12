from __future__ import annotations
import copy,importlib.util
from decimal import Decimal
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('proof',ROOT/'scripts/verify_mlb_team_context.py');SUBJECT=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(SUBJECT)
PK='824631'; FEED=f'https://statsapi.mlb.com/api/v1.1/game/{PK}/feed/live'; FP='a'*64
class Table:
    def __init__(self,items):self.items=copy.deepcopy(items)
    def query(self,**kwargs):assert kwargs['ConsistentRead'] is True;return {'Items':copy.deepcopy(self.items)}
def _row():
    prov={'provider':'MLB Stats API','dataset':SUBJECT.source.VERSION,'endpoint':FEED,'retrievedAtUtc':'2026-09-11T18:00:00+00:00','sourceEffectiveAtUtc':'2026-09-11T17:59:59+00:00','payloadFingerprint':FP}
    line={'source_status':'CONNECTED','game_pk':Decimal(int(PK)),'lineupSeasonBattingVersion':SUBJECT.source.BATTING_OBSERVATION_VERSION,'sourceProvenance':copy.deepcopy(prov),'home_lineup_confirmed':True,'away_lineup_confirmed':True}
    for side,base in (('home',100),('away',200)):
        line[side+'_batting_order']=[Decimal(base+i) for i in range(1,10)]
        line[side+'_lineup_season_batting']=[{'playerId':Decimal(base+i),'battingSlot':Decimal(i),'plateAppearances':Decimal(100),'ops':Decimal('.8'),'obp':Decimal('.33'),'slg':Decimal('.47'),'rateObservationCount':Decimal(3),'sampleStatus':'OBSERVED'} for i in range(1,10)]
    bp={'source_status':'PARTIAL','game_pk':Decimal(int(PK)),'bullpenRosterObservationStatus':'OBSERVED_ROSTER_ONLY','bullpenRosterSourceProvenance':copy.deepcopy(prov),'home_bullpen_roster_player_ids':[Decimal(301),Decimal(302)],'away_bullpen_roster_player_ids':[Decimal(401),Decimal(402)]}
    return {'PK':'GAME_WINNERS#mlb#2026-09-11','SK':'GAME#fixture','data':{'officialGamePk':PK,'commenceTime':'2026-09-11T20:00:00+00:00','advanced_context':{'confirmed_lineups':line,'bullpen_fatigue':bp}}}
def test_valid_production_string_game_pk_and_full_receipts_are_accepted():
    r=SUBJECT.persisted_observations(Table([_row()]),'2026-09-11');assert r['gamesWithValidPassiveBatterObservations']==1;assert r['gamesWithValidPassiveBullpenRosters']==1;assert r['readOnly'] is True
def test_posted_lineup_missing_passive_arrays_fails_closed():
    r=_row();l=r['data']['advanced_context']['confirmed_lineups'];l['home_lineup_season_batting']=None;l['away_lineup_season_batting']=None
    with pytest.raises(RuntimeError,match='passive_batter_count_invalid'):SUBJECT.persisted_observations(Table([r]),'2026-09-11')
def test_partial_bullpen_write_with_availability_claim_cannot_hide_as_absence():
    r=_row();b=r['data']['advanced_context']['bullpen_fatigue'];b['home_bullpen_roster_player_ids']=None;b['away_bullpen_roster_player_ids']=None;b['home_available_relievers']=[]
    with pytest.raises(RuntimeError,match='passive_roster_must_not_claim_reliever_availability'):SUBJECT.persisted_observations(Table([r]),'2026-09-11')
@pytest.mark.parametrize('field,value,error',[('payloadFingerprint','bad','fingerprint_invalid'),('sourceEffectiveAtUtc','bad','effective_at_invalid'),('sourceEffectiveAtUtc','2026-09-11T18:01:00+00:00','effective_after_retrieval'),('retrievedAtUtc','2026-09-11T19:15:00+00:00','not_pre_t45')])
def test_full_source_receipt_is_required(field,value,error):
    r=_row();r['data']['advanced_context']['confirmed_lineups']['sourceProvenance'][field]=value
    with pytest.raises(RuntimeError,match=error):SUBJECT.persisted_observations(Table([r]),'2026-09-11')
def test_exact_official_endpoint_required():
    r=_row();r['data']['advanced_context']['confirmed_lineups']['sourceProvenance']['endpoint']=FEED+'?x=1'
    with pytest.raises(RuntimeError,match='endpoint_identity_mismatch'):SUBJECT.persisted_observations(Table([r]),'2026-09-11')
def test_cross_role_opponent_identity_overlap_fails_closed():
    r=_row();r['data']['advanced_context']['bullpen_fatigue']['away_bullpen_roster_player_ids']=[Decimal(101),Decimal(402)]
    with pytest.raises(RuntimeError,match='cross-team identity overlap'):SUBJECT.persisted_observations(Table([r]),'2026-09-11')
def test_unposted_lineup_remains_normal_absence():
    r=_row();l=r['data']['advanced_context']['confirmed_lineups']
    for k in ('home_lineup_season_batting','away_lineup_season_batting','home_batting_order','away_batting_order'):l[k]=None
    l['home_lineup_confirmed']=False;l['away_lineup_confirmed']=False;l['source_status']='PARTIAL'
    s=SUBJECT.passive_lineup_observation(r['data']);assert s['present'] is False and s['errors']==[]
@pytest.mark.parametrize('bad',['101',101.0,Decimal('101.5')])
def test_player_identity_remains_strict(bad):
    r=_row();l=r['data']['advanced_context']['confirmed_lineups'];l['home_batting_order'][0]=bad;l['home_lineup_season_batting'][0]['playerId']=bad
    with pytest.raises(RuntimeError,match='identity'):SUBJECT.persisted_observations(Table([r]),'2026-09-11')


# Retained negative coverage from the original persistence proof (#819).
GAME_PK = int(PK)

def test_persisted_passive_observations_accept_decimal_ddb_identities_without_authority_change():
    row = _row(); row["data"]["officialGamePk"] = Decimal(GAME_PK)
    report = SUBJECT.persisted_observations(Table([row]), "2026-09-11")
    assert report["readOnly"] is True
    assert report["gamesWithValidPassiveBatterObservations"] == 1
    assert report["gamesWithValidPassiveBullpenRosters"] == 1
    assert report["passiveRosterAvailabilityClaimCount"] == 0
    assert report["rows"][0]["passiveLineupObservation"]["preT45"] is True
    assert report["rows"][0]["passiveBullpenRosterObservation"]["preT45"] is True


def test_passive_batter_order_mismatch_fails_closed():
    row = _row()
    row["data"]["advanced_context"]["confirmed_lineups"]["home_lineup_season_batting"][0]["playerId"] = Decimal(999)
    with pytest.raises(RuntimeError, match="passive_batter_order_mismatch"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def test_passive_batter_observation_at_or_after_t45_fails_closed():
    row = _row()
    row["data"]["advanced_context"]["confirmed_lineups"]["sourceProvenance"]["retrievedAtUtc"] = "2026-09-11T19:15:00+00:00"
    with pytest.raises(RuntimeError, match="passive_batting_not_pre_t45"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


@pytest.mark.parametrize("field,value", [
    ("home_available_relievers", []),
    ("away_available_relievers", [401]),
    ("home_unavailable_relievers", []),
    ("away_unavailable_relievers", [402]),
])
def test_passive_bullpen_roster_never_makes_available_or_unavailable_claim(field, value):
    row = _row()
    row["data"]["advanced_context"]["bullpen_fatigue"][field] = value
    with pytest.raises(RuntimeError, match="passive_roster_must_not_claim_reliever_availability"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def test_absent_passive_observations_are_reported_as_absent_not_fabricated():
    row = _row(); row["data"]["advanced_context"] = {}
    report = SUBJECT.persisted_observations(Table([row]), "2026-09-11")
    assert report["gamesWithPassiveBatterObservations"] == 0
    assert report["gamesWithPassiveBullpenRosters"] == 0


def test_version_marker_with_unposted_lineups_is_normal_absence():
    row = _row(); lineup = row["data"]["advanced_context"]["confirmed_lineups"]
    lineup["home_lineup_season_batting"] = None; lineup["away_lineup_season_batting"] = None
    lineup["home_batting_order"] = None; lineup["away_batting_order"] = None
    lineup["source_status"] = "PARTIAL"
    lineup["home_lineup_confirmed"] = None; lineup["away_lineup_confirmed"] = None
    state = SUBJECT.passive_lineup_observation(row["data"])
    assert state["present"] is False and state["errors"] == []


@pytest.mark.parametrize("value", [None, "", "not-a-time"])
def test_present_passive_batter_observation_requires_parseable_game_start(value):
    row = _row(); row["data"]["commenceTime"] = value
    with pytest.raises(RuntimeError, match="passive_batting_commence_time_invalid"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def test_passive_lineup_must_belong_to_same_exact_official_game_and_feed():
    row = _row(); lineup = row["data"]["advanced_context"]["confirmed_lineups"]
    lineup["game_pk"] = Decimal(GAME_PK + 1)
    with pytest.raises(RuntimeError, match="passive_batting_game_identity_mismatch"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def test_feed_endpoint_requires_exact_scheme_host_and_path():
    for endpoint in (
        f"http://statsapi.mlb.com/api/v1.1/game/{GAME_PK}/feed/live",
        f"https://example.invalid/api/v1.1/game/{GAME_PK}/feed/live",
        FEED + "/corrupt",
        FEED + "?extra=1",
    ):
        row = _row(); row["data"]["advanced_context"]["confirmed_lineups"]["sourceProvenance"]["endpoint"] = endpoint
        with pytest.raises(RuntimeError, match="passive_batting_endpoint_identity_mismatch"):
            SUBJECT.persisted_observations(Table([row]), "2026-09-11")


@pytest.mark.parametrize("invalid", ["101", 101.0, Decimal("101.5"), Decimal("9007199254740992.5")])
def test_persisted_player_ids_do_not_accept_string_float_or_fractional_decimal(invalid):
    row = _row(); lineup = row["data"]["advanced_context"]["confirmed_lineups"]
    lineup["home_batting_order"][0] = invalid; lineup["home_lineup_season_batting"][0]["playerId"] = invalid
    with pytest.raises(RuntimeError, match="batting_order_identity_invalid|passive_batter_identity_or_slot_invalid"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


@pytest.mark.parametrize("field,value,error", [
    ("plateAppearances", None, "sample_status_invalid"),
    ("ops", Decimal("9.0"), "ops_invalid"),
    ("rateObservationCount", Decimal(2), "rate_observation_count_invalid"),
    ("sampleStatus", "SAMPLE_UNAVAILABLE", "sample_status_invalid"),
])
def test_passive_batter_season_observation_fields_are_validated(field, value, error):
    row = _row(); item = row["data"]["advanced_context"]["confirmed_lineups"]["home_lineup_season_batting"][0]
    item[field] = value
    with pytest.raises(RuntimeError, match=error):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def test_players_cannot_be_duplicated_across_opposing_lineups():
    row = _row(); lineup = row["data"]["advanced_context"]["confirmed_lineups"]
    lineup["away_batting_order"] = copy.deepcopy(lineup["home_batting_order"])
    lineup["away_lineup_season_batting"] = copy.deepcopy(lineup["home_lineup_season_batting"])
    with pytest.raises(RuntimeError, match="cross_team_identity_overlap"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def test_players_cannot_be_duplicated_across_opposing_bullpens():
    row = _row(); bullpen = row["data"]["advanced_context"]["bullpen_fatigue"]
    bullpen["away_bullpen_roster_player_ids"] = copy.deepcopy(bullpen["home_bullpen_roster_player_ids"])
    with pytest.raises(RuntimeError, match="cross_team_identity_overlap"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def _unpost(row, side):
    line = row['data']['advanced_context']['confirmed_lineups']
    line['source_status'] = 'PARTIAL'
    for suffix in ('batting_order', 'lineup_season_batting', 'lineup_confirmed'):
        line[side+'_'+suffix] = None


def _live(row):
    context = row['data']['advanced_context']
    return {'officialGamePk': int(PK), 'gameDate': row['data']['commenceTime'],
            'lineup': copy.deepcopy(context.get('confirmed_lineups') or {}),
            'bullpen': copy.deepcopy(context.get('bullpen_fatigue') or {})}


@pytest.mark.parametrize('side', ['home', 'away'])
def test_partial_lineup_accepts_each_independently_observed_side(side):
    row = _row(); _unpost(row, side)
    before = copy.deepcopy(row)
    report = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    state = report['rows'][0]['passiveLineupObservation']
    assert state['valid'] and state[side+'BatterCount'] == 0
    assert state[('away' if side == 'home' else 'home')+'BatterCount'] == 9
    assert row == before
    assert SUBJECT.correlate_persistence([_live(row)], report)['status'] == 'PROVEN'


@pytest.mark.parametrize('side', ['home', 'away'])
def test_connected_lineup_cannot_omit_a_side(side):
    row = _row(); _unpost(row, side)
    row['data']['advanced_context']['confirmed_lineups']['source_status'] = 'CONNECTED'
    with pytest.raises(RuntimeError, match='batting_order_invalid'):
        SUBJECT.persisted_observations(Table([row]), '2026-09-11')


@pytest.mark.parametrize('block,receipt', [('confirmed_lineups', 'sourceProvenance'),
                                         ('bullpen_fatigue', 'bullpenRosterSourceProvenance')])
@pytest.mark.parametrize('field', ['retrievedAtUtc', 'sourceEffectiveAtUtc'])
@pytest.mark.parametrize('value', ['2026-09-11T18:00:00', '2026-09-11'])
def test_source_receipts_require_explicit_timezones(block, receipt, field, value):
    row = _row()
    row['data']['advanced_context'][block][receipt][field] = value
    with pytest.raises(RuntimeError, match='at_invalid'):
        SUBJECT.persisted_observations(Table([row]), '2026-09-11')


@pytest.mark.parametrize('value', [None, '824631', Decimal('824631.5'), True])
def test_lineup_game_identity_cannot_bypass_validation(value):
    row = _row(); row['data']['advanced_context']['confirmed_lineups']['game_pk'] = value
    with pytest.raises(RuntimeError, match='game_identity_mismatch'):
        SUBJECT.persisted_observations(Table([row]), '2026-09-11')


def test_exact_game_correlation_retains_row_keys_without_mutation():
    row = _row(); before = copy.deepcopy(row)
    report = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    result = SUBJECT.correlate_persistence([_live(row)], report)
    assert result['status'] == 'PROVEN' and result['matchedBlocks'] == 2
    assert all(m['storedPK'] == row['PK'] and m['storedSK'] == row['SK'] for m in result['matches'])
    assert row == before


@pytest.mark.parametrize('kind', ['no_rows', 'no_blocks', 'other_game', 'duplicate_game', 'wrong_side'])
def test_missing_or_ambiguous_persistence_is_inconclusive(kind):
    live = _live(_row()); row = _row()
    if kind == 'no_blocks': row['data']['advanced_context'] = {}
    if kind == 'wrong_side': _unpost(row, 'away')
    report = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    if kind == 'no_rows': report['rows'] = []
    if kind == 'other_game': live['officialGamePk'] += 1
    if kind == 'duplicate_game': report['rows'] *= 2
    assert SUBJECT.correlate_persistence([live], report)['status'] == 'INCONCLUSIVE'


def test_no_live_observations_do_not_prove_persistence():
    report = SUBJECT.persisted_observations(Table([_row()]), '2026-09-11')
    for rows in ([], [{'officialGamePk': int(PK), 'lineup': {}, 'bullpen': {}}]):
        result = SUBJECT.correlate_persistence(rows, report)
        assert result['status'] == 'INCONCLUSIVE'
        assert result['observedBlocks'] == 0


def test_paginated_reads_are_consistent_and_preserve_every_game():
    row = _row()
    class Pages:
        calls = 0
        def query(self, **kwargs):
            assert kwargs['ConsistentRead'] is True
            self.calls += 1
            if self.calls == 1:
                assert 'ExclusiveStartKey' not in kwargs
                return {'Items': [], 'LastEvaluatedKey': {'PK': row['PK'], 'SK': 'GAME#before'}}
            assert kwargs['ExclusiveStartKey']['SK'] == 'GAME#before'
            return {'Items': [copy.deepcopy(row)]}
    table = Pages()
    result = SUBJECT.persisted_observations(table, '2026-09-11')
    assert table.calls == 2 and result['storedGameRows'] == 1


@pytest.mark.parametrize('mode', ['missing', 'malformed_lineup', 'malformed_bullpen', 'proven'])
def test_live_command_retains_fresh_inconclusive_diagnostic_and_fails(monkeypatch, tmp_path, mode):
    import boto3
    import json
    from datetime import datetime, timezone
    class Clock(datetime):
        @staticmethod
        def now(tz): return datetime(2026, 9, 11, 18, tzinfo=timezone.utc)
    live = _live(_row()); stored = _row()
    if mode == 'missing': stored['data']['advanced_context'] = {}
    elif mode == 'malformed_lineup': stored['data']['advanced_context']['confirmed_lineups']['sourceProvenance'] = 'corrupt'
    elif mode == 'malformed_bullpen': stored['data']['advanced_context']['bullpen_fatigue']['bullpenRosterSourceProvenance'] = ['corrupt']
    for side in ('home', 'away'): live['bullpen'][side+'_reliever_usage_1d_3d_5d'] = {}
    class Resource:
        def Table(self, name):
            assert name == 'parlay_platform_snapshots'
            return Table([stored])
    monkeypatch.setattr(SUBJECT, 'datetime', Clock)
    monkeypatch.setattr(SUBJECT, 'ROOT', tmp_path)
    monkeypatch.setattr(SUBJECT.sys, 'argv', ['proof', '--persisted'])
    monkeypatch.setattr(SUBJECT.advanced, '_statsapi_schedule', lambda _: {'ok': True})
    monkeypatch.setattr(SUBJECT.advanced, '_statsapi_schedule_history', lambda _: {'ok': True})
    monkeypatch.setattr(SUBJECT.advanced, '_schedule_games', lambda _: [
        {'gamePk': int(PK), 'gameDate': '2026-09-11T20:00:00Z', 'status': {'abstractGameState': 'Preview'}}])
    monkeypatch.setattr(SUBJECT.source, 'observe', lambda *args: (live['lineup'], live['bullpen']))
    monkeypatch.setattr(boto3, 'resource', lambda _: Resource())
    monkeypatch.setenv('PROOF_SOURCE_SHA', 'f'*40)
    (tmp_path/'runtime_reports').mkdir()
    if mode == 'proven':
        SUBJECT.main()
    else:
        with pytest.raises(RuntimeError, match='proof inconclusive'):
            SUBJECT.main()
    report = json.loads((tmp_path/'runtime_reports/mlb_team_context_live_proof_latest.json').read_text())
    assert report['sourceSha'] == 'f'*40
    assert report['persistenceCorrelation']['status'] == ('PROVEN' if mode == 'proven' else 'INCONCLUSIVE')
    if mode == 'missing':
        assert report['persistedCollectorEvidence']['gamesWithPassiveBatterObservations'] == 0
    elif mode != 'proven':
        assert report['persistedCollectorEvidence']['status'] == 'INVALID'
        assert 'source_provenance_invalid' in report['persistedCollectorEvidence']['errors'][0]


def test_live_workflow_pins_main_event_and_removes_stale_evidence():
    workflow = (ROOT/'.github/workflows/mlb-passive-context-persistence-proof.yml').read_text()
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "with: {ref: '${{ github.sha }}'}" in workflow
    assert 'PROOF_SOURCE_SHA: ${{ github.sha }}' in workflow
    assert workflow.index('rm -f runtime_reports/') < workflow.index('python scripts/verify_mlb_team_context.py --persisted')
    live = workflow.split('  live-read:', 1)[1]
    assert live.index('rm -f runtime_reports/') < live.index('actions/setup-python@v5')
    assert "steps.clean.outcome == 'success' && steps.proof.outcome != 'skipped'" in live


@pytest.mark.parametrize('side', ['home', 'away'])
@pytest.mark.parametrize('marker', [None, False, 'true', 1])
def test_observed_lineup_requires_explicit_confirmation(side, marker):
    row = _row()
    row['data']['advanced_context']['confirmed_lineups'][side+'_lineup_confirmed'] = marker
    with pytest.raises(RuntimeError, match='lineup_confirmation_invalid'):
        SUBJECT.persisted_observations(Table([row]), '2026-09-11')


@pytest.mark.parametrize('block', ['lineup', 'bullpen'])
def test_correlation_respects_lineup_order_but_not_roster_order(block):
    row = _row(); live = _live(row)
    field = 'home_batting_order' if block == 'lineup' else 'home_bullpen_roster_player_ids'
    live[block][field].reverse()
    stored = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    result = SUBJECT.correlate_persistence([live], stored)
    assert result['status'] == ('INCONCLUSIVE' if block == 'lineup' else 'PROVEN')


def test_roster_membership_change_remains_inconclusive():
    row = _row(); live = _live(row)
    live['bullpen']['home_bullpen_roster_player_ids'][0] = 999
    stored = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    assert SUBJECT.correlate_persistence([live], stored)['status'] == 'INCONCLUSIVE'


def test_other_provider_connected_lineup_is_not_statsapi_passive_evidence():
    import mlb_bbd_pro_context as bbd
    row = _row()
    row['data']['advanced_context'] = bbd.merge_into_advanced_context({}, {
        'sourceStatus': 'CONNECTED', 'categories': {
            'confirmed_lineups': [{'signals': {'values': [{'confirmed': True}]}}]}})
    assert row['data']['advanced_context']['confirmed_lineups']['source_status'] == 'CONNECTED'
    result = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    assert result['gamesWithPassiveBatterObservations'] == 0
    assert result['gamesWithPassiveBullpenRosters'] == 0


def test_statsapi_connected_status_cannot_hide_total_array_loss():
    row = _row(); line = row['data']['advanced_context']['confirmed_lineups']
    for side in ('home', 'away'):
        for suffix in ('batting_order', 'lineup_season_batting', 'lineup_confirmed'):
            line.pop(side+'_'+suffix)
    with pytest.raises(RuntimeError, match='batting_order_invalid'):
        SUBJECT.persisted_observations(Table([row]), '2026-09-11')


@pytest.mark.parametrize('field,value', [('plateAppearances', Decimal(101)), ('ops', Decimal('.9')),
                                       ('obp', Decimal('.34')), ('slg', Decimal('.5'))])
def test_individually_valid_but_changed_batter_sample_is_not_proven(field, value):
    row = _row(); live = _live(row)
    row['data']['advanced_context']['confirmed_lineups']['home_lineup_season_batting'][0][field] = value
    stored = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    assert stored['gamesWithValidPassiveBatterObservations'] == 1
    result = SUBJECT.correlate_persistence([live], stored)
    assert result['status'] == 'INCONCLUSIVE'
    assert any('batter_samples_not_persisted' in error for error in result['errors'])


def test_consistently_changed_sample_metadata_is_not_proven():
    row = _row(); live = _live(row)
    row['data']['advanced_context']['confirmed_lineups']['home_lineup_season_batting'][0].update(
        plateAppearances=Decimal(0), ops=None, obp=None, slg=None,
        rateObservationCount=Decimal(0), sampleStatus='NO_PLATE_APPEARANCES')
    stored = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    assert SUBJECT.correlate_persistence([live], stored)['status'] == 'INCONCLUSIVE'


def test_live_float_samples_equal_dynamodb_decimal_readback():
    row = _row(); live = _live(row)
    for side in ('home', 'away'):
        for sample in live['lineup'][side+'_lineup_season_batting']:
            for field in ('ops', 'obp', 'slg'):
                sample[field] = float(sample[field])
    stored = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    assert SUBJECT.correlate_persistence([live], stored)['status'] == 'PROVEN'


@pytest.mark.parametrize('field', ['ops', 'obp', 'slg', 'plateAppearances', 'sampleStatus', 'rateObservationCount'])
def test_missing_sample_fields_fail_closed(field):
    row = _row()
    del row['data']['advanced_context']['confirmed_lineups']['home_lineup_season_batting'][0][field]
    with pytest.raises(RuntimeError, match='fields_missing'):
        SUBJECT.persisted_observations(Table([row]), '2026-09-11')


def test_corrupted_persisted_start_cannot_make_late_receipts_valid():
    row = _row(); live = _live(row)
    row['data']['commenceTime'] = '2026-09-11T22:00:00Z'
    context = row['data']['advanced_context']
    for prov in (context['confirmed_lineups']['sourceProvenance'], context['bullpen_fatigue']['bullpenRosterSourceProvenance']):
        prov['retrievedAtUtc'] = prov['sourceEffectiveAtUtc'] = '2026-09-11T19:30:00Z'
    stored = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    result = SUBJECT.correlate_persistence([live], stored)
    assert result['status'] == 'INCONCLUSIVE'
    assert any('commence_time_mismatch' in error for error in result['errors'])


def test_equivalent_official_start_timezone_is_accepted():
    row = _row(); live = _live(row); live['gameDate'] = '2026-09-11T16:00:00-04:00'
    stored = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    assert SUBJECT.correlate_persistence([live], stored)['status'] == 'PROVEN'


@pytest.mark.parametrize('value', ['corrupt', ['corrupt'], 1])
@pytest.mark.parametrize('block,field,validator', [
    ('confirmed_lineups', 'sourceProvenance', 'passive_lineup_observation'),
    ('bullpen_fatigue', 'bullpenRosterSourceProvenance', 'passive_bullpen_roster_observation')])
def test_malformed_provenance_returns_validation_diagnostics(block, field, validator, value):
    row = _row(); row['data']['advanced_context'][block][field] = value
    result = getattr(SUBJECT, validator)(row['data'])
    assert result['present'] and not result['valid']
    assert any('source_provenance_invalid' in error for error in result['errors'])


def test_new_snapshot_retains_observations_through_game_writer(monkeypatch):
    import mlb_fundamentals_snapshot_v2 as snapshots
    import mlb_fundamentals_scoring_bridge_v1 as bridge
    import mlb_game_winner_engine as engine
    import mlb_three_api_prediction_overlay as overlay

    row = _row()['data']
    context = row.pop('advanced_context')
    row.update(slate_date='2026-09-11', gameId=PK,
               predictionSourcePullAt='2026-09-11T18:00:00+00:00',
               predictedWinner='Home', winProbability=.61)
    calls = []
    def observe(_row):
        calls.append(True)
        return copy.deepcopy(context)
    monkeypatch.setattr(snapshots, '_context_for_row', observe)
    bridge.install_snapshot_determinism(snapshots)
    expected_snapshot = snapshots.build(copy.deepcopy(row))
    calls.clear()
    original_row = copy.deepcopy(row)
    original_overlay_context = overlay._context_from_row(row)
    snapshots.enhance_row(row)
    assert len(calls) == 1
    assert row['fundamentalsSnapshotV2'] == expected_snapshot
    assert row['passiveTeamContext'] == context
    assert overlay._context_from_row(row) == original_overlay_context
    for key, value in original_row.items():
        assert row[key] == value
    assert 'advanced_context' not in row
    context['confirmed_lineups']['home_batting_order'][0] = 999
    assert row['passiveTeamContext']['confirmed_lineups']['home_batting_order'][0] != 999

    class Writer(Table):
        def put_item(self, *, Item):
            self.items.append(copy.deepcopy(Item))
    table = Writer([])
    monkeypatch.setattr(engine.history, 'PULLS', table)
    # Isolate serialization; existing public-authority tests cover these gates.
    monkeypatch.setattr(engine, '_public_prelock_markers', lambda row: {})
    monkeypatch.setattr(engine, '_pregame_snapshot_item', lambda row, **kwargs: {})
    monkeypatch.setattr(engine, '_put_pregame_snapshot', lambda item: {})
    assert engine._store_prediction(row)['ok'] is True
    report = SUBJECT.persisted_observations(table, '2026-09-11')
    assert report['gamesWithValidPassiveBatterObservations'] == 1
    assert report['gamesWithValidPassiveBullpenRosters'] == 1
    assert report['passiveRosterAvailabilityClaimCount'] == 0


def test_existing_snapshot_never_fetches_or_backfills_passive_context(monkeypatch):
    import mlb_fundamentals_snapshot_v2 as snapshots
    row = _row()['data']
    row['fundamentalsSnapshotV2'] = snapshots.build(row)
    original_snapshot = copy.deepcopy(row['fundamentalsSnapshotV2'])
    row.pop('advanced_context')
    def forbidden(*args, **kwargs):
        raise AssertionError('must not fetch or rebuild existing evidence')
    monkeypatch.setattr(snapshots, '_context_for_row', forbidden)
    snapshots.enhance_row(row)
    assert 'passiveTeamContext' not in row
    assert row['fundamentalsSnapshotV2'] == original_snapshot
    row['passiveTeamContext'] = {'existing': 'immutable observation'}
    snapshots.enhance_row(row)
    assert row['passiveTeamContext'] == {'existing': 'immutable observation'}


@pytest.mark.parametrize('value', [None, [], 'malformed'])
def test_malformed_passive_envelope_cannot_fall_back_to_valid_legacy_context(value):
    row = _row()
    row['data']['passiveTeamContext'] = value
    with pytest.raises(RuntimeError, match='invalid persisted passive team context'):
        SUBJECT.persisted_observations(Table([row]), '2026-09-11')


def test_empty_passive_envelope_does_not_claim_legacy_observations():
    import mlb_fundamentals_snapshot_v2 as snapshots
    row = _row()
    row['data']['fundamentalsSnapshotV2'] = snapshots.build(row['data'])
    row['data']['passiveTeamContext'] = {}
    result = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    assert result['gamesWithValidPassiveBatterObservations'] == 0
    assert result['gamesWithValidPassiveBullpenRosters'] == 0


@pytest.mark.parametrize('block', ['confirmed_lineups', 'bullpen_fatigue'])
@pytest.mark.parametrize('value', [None, [], 'malformed'])
def test_named_passive_block_must_be_a_dictionary(block, value):
    row = _row()
    row['data']['passiveTeamContext'] = copy.deepcopy(row['data']['advanced_context'])
    row['data']['passiveTeamContext'][block] = value
    with pytest.raises(RuntimeError, match='invalid persisted passive team block'):
        SUBJECT.persisted_observations(Table([row]), '2026-09-11')


@pytest.mark.parametrize('snapshot', [None, {}, [], 'malformed'])
@pytest.mark.parametrize('roster_only', [False, True])
def test_passive_envelope_requires_valid_companion_snapshot(snapshot, roster_only):
    row = _row()
    context = row['data'].pop('advanced_context')
    if roster_only:
        context.pop('confirmed_lineups')
    row['data']['passiveTeamContext'] = context
    row['data']['fundamentalsSnapshotV2'] = snapshot
    with pytest.raises(RuntimeError, match='invalid persisted companion snapshot'):
        SUBJECT.persisted_observations(Table([row]), '2026-09-11')


def test_roster_only_requires_snapshot_fingerprint_even_without_team_dataset():
    import mlb_fundamentals_snapshot_v2 as snapshots
    row = _row()
    context = row['data'].pop('advanced_context')
    row['data']['passiveTeamContext'] = {'bullpen_fatigue': context['bullpen_fatigue']}
    row['data']['fundamentalsSnapshotV2'] = snapshots.build(row['data'], context={})
    result = SUBJECT.persisted_observations(Table([row]), '2026-09-11')
    assert result['teamSnapshotRows'] == 0
    assert result['gamesWithValidPassiveBullpenRosters'] == 1
    row['data']['fundamentalsSnapshotV2']['fingerprint'] = 'corrupt'
    with pytest.raises(RuntimeError, match='invalid persisted companion snapshot'):
        SUBJECT.persisted_observations(Table([row]), '2026-09-11')
