"""Read-only current pregame source proof; never backfills a locked snapshot."""
import argparse,json,re,sys,time
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'hello_world'))
import mlb_advanced_context as advanced
import mlb_statsapi_team_context as source

HEX64=re.compile(r'^[0-9a-f]{64}$')

def _time(v):
    try:
        d=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:return None

def _id(v):
    if isinstance(v,bool):return None
    if isinstance(v,int):return v if v>0 else None
    if isinstance(v,Decimal) and v.is_finite() and v>0 and v==v.to_integral_value():return int(v)
    return None

def _official(v):
    if (n:=_id(v)) is not None:return n
    if isinstance(v,str) and v.isdigit() and not v.startswith('0'):
        n=int(v);return n if n>0 else None
    return None

def _count(v):
    if isinstance(v,bool):return None
    if isinstance(v,int):return v if v>=0 else None
    if isinstance(v,Decimal) and v.is_finite() and v>=0 and v==v.to_integral_value():return int(v)
    return None

def _number(v):
    if isinstance(v,bool):return None
    if isinstance(v,int):return float(v)
    if isinstance(v,Decimal) and v.is_finite():return float(v)
    return None

def _receipt(prov,game_pk,commence,prefix,errors):
    if not isinstance(prov,dict):prov={}
    if prov.get('provider')!='MLB Stats API' or prov.get('dataset')!=source.VERSION:errors.append(prefix+'_source_provenance_invalid')
    if prov.get('endpoint')!=f'https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live':errors.append(prefix+'_endpoint_identity_mismatch')
    fp=str(prov.get('payloadFingerprint') or '')
    if not HEX64.fullmatch(fp):errors.append(prefix+'_payload_fingerprint_invalid')
    r,e=_time(prov.get('retrievedAtUtc')),_time(prov.get('sourceEffectiveAtUtc'))
    if r is None:errors.append(prefix+'_retrieved_at_invalid')
    if e is None:errors.append(prefix+'_effective_at_invalid')
    cutoff=commence-timedelta(minutes=45) if commence else None
    if r and e and e>r:errors.append(prefix+'_effective_after_retrieval')
    if cutoff and r and r>=cutoff:errors.append(prefix+'_not_pre_t45')
    if cutoff and e and e>=cutoff:errors.append(prefix+'_effective_not_pre_t45')
    return r,e

def _sample(item,side,errors):
    pa=_count(item.get('plateAppearances')) if item.get('plateAppearances') is not None else None
    vals={k:_number(item.get(k)) if item.get(k) is not None else None for k in ('ops','obp','slg')}
    for k,hi in (('ops',5),('obp',1),('slg',4)):
        if item.get(k) is not None and (vals[k] is None or not 0<=vals[k]<=hi):errors.append(f'{side}_passive_batter_{k}_invalid')
    if item.get('plateAppearances') is not None and pa is None:errors.append(side+'_passive_batter_plate_appearances_invalid')
    roc=_count(item.get('rateObservationCount')); expected=sum(v is not None for v in vals.values())
    if roc!=expected:errors.append(side+'_passive_batter_rate_observation_count_invalid')
    expected_status='OBSERVED' if pa is not None and pa>0 else 'NO_PLATE_APPEARANCES' if pa==0 else 'SAMPLE_UNAVAILABLE'
    if item.get('sampleStatus')!=expected_status:errors.append(side+'_passive_batter_sample_status_invalid')
    if pa in (None,0) and any(v is not None for v in vals.values()):errors.append(side+'_passive_batter_rate_without_sample')

def passive_lineup_observation(row):
    ctx=row.get('advanced_context') or row.get('advancedContext') or {}; line=ctx.get('confirmed_lineups') if isinstance(ctx,dict) else None;line=line if isinstance(line,dict) else {}
    present=any(line.get(k) is not None for k in ('home_lineup_season_batting','away_lineup_season_batting','home_batting_order','away_batting_order')) or any(line.get(k) is True for k in ('home_lineup_confirmed','away_lineup_confirmed'))
    out={'present':present,'valid':False,'errors':[],'homeBatterCount':0,'awayBatterCount':0,'retrievedAtUtc':None,'preT45':None,'identitySets':{'home':[],'away':[]}}
    if not present:return out
    err=[];official=_official(row.get('officialGamePk') if row.get('officialGamePk') is not None else row.get('official_game_pk')); commence=_time(row.get('commenceTime') or row.get('commence_time'))
    if official is None:err.append('passive_batting_official_game_pk_invalid')
    if commence is None:err.append('passive_batting_commence_time_invalid')
    if _id(line.get('game_pk')) not in (None,official):err.append('passive_batting_game_identity_mismatch')
    if line.get('lineupSeasonBattingVersion')!=source.BATTING_OBSERVATION_VERSION:err.append('passive_batting_version_mismatch')
    r,_=_receipt(line.get('sourceProvenance'),official,commence,'passive_batting',err) if official else (None,None);out['retrievedAtUtc']=(line.get('sourceProvenance') or {}).get('retrievedAtUtc');out['preT45']=bool(r and commence and r<commence-timedelta(minutes=45))
    for side in ('home','away'):
        order,obs=line.get(side+'_batting_order'),line.get(side+'_lineup_season_batting')
        if not isinstance(order,list) or len(order)!=9:err.append(side+'_batting_order_invalid');continue
        ids=[_id(v) for v in order]
        if any(v is None for v in ids) or len(set(ids))!=9:err.append(side+'_batting_order_identity_invalid');continue
        out['identitySets'][side]=ids
        if not isinstance(obs,list) or len(obs)!=9:err.append(side+'_passive_batter_count_invalid');continue
        seen=[]
        for slot,item in enumerate(obs,1):
            if not isinstance(item,dict):err.append(side+'_passive_batter_row_invalid');seen.append(None);continue
            pid,bs=_id(item.get('playerId')),_id(item.get('battingSlot'))
            if pid is None or bs!=slot:err.append(side+'_passive_batter_identity_or_slot_invalid')
            _sample(item,side,err);seen.append(pid)
        if seen!=ids:err.append(side+'_passive_batter_order_mismatch')
        out[side+'BatterCount']=len(obs)
    if set(out['identitySets']['home']) & set(out['identitySets']['away']):err.append('passive_batting_cross_team_identity_overlap')
    out['errors']=sorted(set(err));out['valid']=not out['errors'];return out

def passive_bullpen_roster_observation(row):
    ctx=row.get('advanced_context') or row.get('advancedContext') or {}; bp=ctx.get('bullpen_fatigue') if isinstance(ctx,dict) else None;bp=bp if isinstance(bp,dict) else {}
    markers=('home_bullpen_roster_player_ids','away_bullpen_roster_player_ids','bullpenRosterObservationStatus','bullpenRosterSourceProvenance','home_available_relievers','away_available_relievers','home_unavailable_relievers','away_unavailable_relievers')
    present=any(k in bp and bp.get(k) is not None for k in markers)
    out={'present':present,'valid':False,'errors':[],'availabilityClaimed':False,'retrievedAtUtc':None,'preT45':None,'identitySets':{'home':[],'away':[]}}
    if not present:return out
    err=[];official=_official(row.get('officialGamePk') if row.get('officialGamePk') is not None else row.get('official_game_pk'));commence=_time(row.get('commenceTime') or row.get('commence_time'))
    if official is None:err.append('bullpen_roster_official_game_pk_invalid')
    if commence is None:err.append('bullpen_roster_commence_time_invalid')
    if bp.get('game_pk') is not None and _id(bp.get('game_pk'))!=official:err.append('bullpen_roster_game_identity_mismatch')
    if bp.get('bullpenRosterObservationStatus')!='OBSERVED_ROSTER_ONLY':err.append('bullpen_roster_status_invalid')
    r,_=_receipt(bp.get('bullpenRosterSourceProvenance'),official,commence,'bullpen_roster',err) if official else (None,None);out['retrievedAtUtc']=(bp.get('bullpenRosterSourceProvenance') or {}).get('retrievedAtUtc');out['preT45']=bool(r and commence and r<commence-timedelta(minutes=45))
    for side in ('home','away'):
        vals=bp.get(side+'_bullpen_roster_player_ids')
        if not isinstance(vals,list) or not vals:err.append(side+'_bullpen_roster_missing');continue
        ids=[_id(v) for v in vals]
        if any(v is None for v in ids) or len(set(ids))!=len(ids):err.append(side+'_bullpen_roster_identity_invalid');continue
        out['identitySets'][side]=ids
    if set(out['identitySets']['home']) & set(out['identitySets']['away']):err.append('bullpen_roster_cross_team_identity_overlap')
    availability=('home_available_relievers','away_available_relievers','home_unavailable_relievers','away_unavailable_relievers')
    if any(k in bp and bp.get(k) is not None for k in availability):out['availabilityClaimed']=True;err.append('passive_roster_must_not_claim_reliever_availability')
    out['errors']=sorted(set(err));out['valid']=not out['errors'];return out

def persisted_observations(table,day):
    from boto3.dynamodb.conditions import Key
    import mlb_fundamentals_snapshot_v2 as snapshots
    rows=[];cursor=None
    while True:
        args={'KeyConditionExpression':Key('PK').eq(f'GAME_WINNERS#mlb#{day}') & Key('SK').begins_with('GAME#'),'ConsistentRead':True}
        if cursor:args['ExclusiveStartKey']=cursor
        page=table.query(**args);rows.extend(page.get('Items') or []);cursor=page.get('LastEvaluatedKey')
        if not cursor:break
    evidence=[]
    for stored in rows:
        row=stored.get('data') or stored;snap=row.get('fundamentalsSnapshotV2') or {};groups=snap.get('groups') or {}
        line,roster=passive_lineup_observation(row),passive_bullpen_roster_observation(row)
        if line['present'] and not line['valid']:raise RuntimeError('invalid persisted passive batter observation: '+','.join(line['errors']))
        if roster['present'] and not roster['valid']:raise RuntimeError('invalid persisted passive bullpen roster observation: '+','.join(roster['errors']))
        home=set(line['identitySets']['home'])|set(roster['identitySets']['home']);away=set(line['identitySets']['away'])|set(roster['identitySets']['away'])
        if home & away:raise RuntimeError('invalid persisted passive cross-team identity overlap')
        team_snapshot=any(groups.get(k,{}).get('dataset')==source.VERSION for k in ('confirmed_lineups','bullpen_availability'))
        if team_snapshot:
            errors=snapshots.validate(snap)
            if errors:raise RuntimeError('invalid persisted team snapshot: '+','.join(errors))
        lv=groups.get('confirmed_lineups',{}).get('values') or {};bv=groups.get('bullpen_availability',{}).get('values') or {}
        evidence.append({'officialGamePk':str(row.get('officialGamePk')),'snapshotFingerprint':snap.get('fingerprint'),'teamSnapshot':team_snapshot,'bothLineups':all(lv.get(s+'Confirmed') is True for s in ('home','away')) if team_snapshot else False,'bothWorkloads':all(bv.get(s+'Usage1d3d5d') is not None for s in ('home','away')) if team_snapshot else False,'passiveLineupObservation':line,'passiveBullpenRosterObservation':roster})
    return {'storedGameRows':len(rows),'teamSnapshotRows':sum(r['teamSnapshot'] for r in evidence),'gamesWithBothLineups':sum(r['bothLineups'] for r in evidence),'gamesWithBothWorkloads':sum(r['bothWorkloads'] for r in evidence),'gamesWithPassiveBatterObservations':sum(r['passiveLineupObservation']['present'] for r in evidence),'gamesWithValidPassiveBatterObservations':sum(r['passiveLineupObservation']['valid'] for r in evidence),'gamesWithPassiveBullpenRosters':sum(r['passiveBullpenRosterObservation']['present'] for r in evidence),'gamesWithValidPassiveBullpenRosters':sum(r['passiveBullpenRosterObservation']['valid'] for r in evidence),'passiveRosterAvailabilityClaimCount':sum(r['passiveBullpenRosterObservation']['availabilityClaimed'] for r in evidence),'readOnly':True,'rows':evidence}

def main():
    p=argparse.ArgumentParser();p.add_argument('--persisted',action='store_true');a=p.parse_args();now=datetime.now(timezone.utc);day=now.astimezone(ZoneInfo('America/New_York')).date().isoformat();schedule=advanced._statsapi_schedule(day);history=advanced._statsapi_schedule_history(day)
    if not schedule.get('ok') or not history.get('ok'):raise RuntimeError('official schedule unavailable')
    started=time.monotonic();rows=[]
    for game in advanced._schedule_games(schedule):
        start=source._time(game.get('gameDate'))
        if game.get('status',{}).get('abstractGameState')!='Preview' or start is None or start-timedelta(minutes=45)<=now:continue
        at=time.monotonic();x,y=source.observe(day,game,history,advanced._http_get_json);rows.append({'officialGamePk':game['gamePk'],'elapsedSeconds':round(time.monotonic()-at,3),'lineup':x,'bullpen':y})
    report={'readOnly':True,'createdAtUtc':datetime.now(timezone.utc).isoformat(),'version':source.VERSION,'elapsedSeconds':round(time.monotonic()-started,3),'eligibleGames':len(rows),'gamesWithBothLineups':sum(all(r['lineup'].get(s+'_lineup_confirmed') is True for s in ('home','away')) for r in rows),'gamesWithBothWorkloads':sum(all(r['bullpen'].get(s+'_reliever_usage_1d_3d_5d') is not None for s in ('home','away')) for r in rows),'rows':rows}
    if a.persisted:
        import boto3;report['persistedCollectorEvidence']=persisted_observations(boto3.resource('dynamodb').Table('parlay_platform_snapshots'),day)
    out=ROOT/'runtime_reports/mlb_team_context_live_proof_latest.json';out.write_text(json.dumps(report,indent=2,default=str)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='rows'},default=str))
    if rows and not report['gamesWithBothWorkloads']:raise RuntimeError('no complete observed relief workload')
    if report['elapsedSeconds']>90:raise RuntimeError('supplemental source proof exceeds 90 second slate budget')
if __name__=='__main__':main()
