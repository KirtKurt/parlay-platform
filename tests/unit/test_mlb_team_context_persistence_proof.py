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
    l['home_lineup_confirmed']=False;l['away_lineup_confirmed']=False
    s=SUBJECT.passive_lineup_observation(r['data']);assert s['present'] is False and s['errors']==[]
@pytest.mark.parametrize('bad',['101',101.0,Decimal('101.5')])
def test_player_identity_remains_strict(bad):
    r=_row();l=r['data']['advanced_context']['confirmed_lineups'];l['home_batting_order'][0]=bad;l['home_lineup_season_batting'][0]['playerId']=bad
    with pytest.raises(RuntimeError,match='identity'):SUBJECT.persisted_observations(Table([r]),'2026-09-11')
