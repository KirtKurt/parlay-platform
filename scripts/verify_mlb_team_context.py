"""Read-only current pregame source proof; never backfills a locked snapshot."""
import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'hello_world'))
import mlb_advanced_context as advanced
import mlb_statsapi_team_context as source


def _parse_time(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:return None


def _positive_int(value):
    """Accept only producer-compatible integer identities plus DynamoDB Decimal readback."""
    if isinstance(value,bool):return None
    if isinstance(value,int):return value if value>0 else None
    if isinstance(value,Decimal):
        if not value.is_finite() or value<=0 or value!=value.to_integral_value():return None
        return int(value)
    return None


def _nonnegative_int(value):
    if isinstance(value,bool):return None
    if isinstance(value,int):return value if value>=0 else None
    if isinstance(value,Decimal):
        if not value.is_finite() or value<0 or value!=value.to_integral_value():return None
        return int(value)
    return None


def _finite_number(value):
    if isinstance(value,bool):return None
    if isinstance(value,int):return float(value)
    if isinstance(value,Decimal):return float(value) if value.is_finite() else None
    return None


def _official_game_pk(row):
    return _positive_int(row.get('officialGamePk') if row.get('officialGamePk') is not None else row.get('official_game_pk'))


def _exact_feed_endpoint(provenance, game_pk):
    endpoint=(provenance or {}).get('endpoint')
    return bool(game_pk and endpoint==f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live")


def _validate_batter_sample(item, side, errors):
    pa=_nonnegative_int(item.get('plateAppearances')) if item.get('plateAppearances') is not None else None
    raw_rates={name:_finite_number(item.get(name)) if item.get(name) is not None else None
               for name in ('ops','obp','slg')}
    bounds={'ops':5.0,'obp':1.0,'slg':4.0}
    if item.get('plateAppearances') is not None and pa is None:
        errors.append(side+'_passive_batter_plate_appearances_invalid')
    for name,value in raw_rates.items():
        if item.get(name) is not None and (value is None or value<0 or value>bounds[name]):
            errors.append(side+'_passive_batter_'+name+'_invalid')
    count=_nonnegative_int(item.get('rateObservationCount'))
    expected_count=sum(value is not None for value in raw_rates.values())
    if count is None or count>3 or count!=expected_count:
        errors.append(side+'_passive_batter_rate_observation_count_invalid')
    expected_status=('OBSERVED' if pa is not None and pa>0 else
                     'NO_PLATE_APPEARANCES' if pa==0 else 'SAMPLE_UNAVAILABLE')
    if item.get('sampleStatus')!=expected_status:
        errors.append(side+'_passive_batter_sample_status_invalid')
    if pa in (None,0) and any(value is not None for value in raw_rates.values()):
        errors.append(side+'_passive_batter_rate_without_sample')


def passive_lineup_observation(row):
    """Validate optional persisted batter arrays without granting model authority."""
    context=(row.get('advanced_context') or row.get('advancedContext') or {})
    lineup=context.get('confirmed_lineups') if isinstance(context,dict) else None
    lineup=lineup if isinstance(lineup,dict) else {}
    home_payload=lineup.get('home_lineup_season_batting')
    away_payload=lineup.get('away_lineup_season_batting')
    present=home_payload is not None or away_payload is not None
    result={'present':present,'valid':False,'errors':[],'homeBatterCount':0,'awayBatterCount':0,
            'retrievedAtUtc':None,'preT45':None}
    if not present:return result
    errors=[]
    official=_official_game_pk(row)
    observed_game=_positive_int(lineup.get('game_pk'))
    if official is None:errors.append('passive_batting_official_game_pk_invalid')
    elif observed_game!=official:errors.append('passive_batting_game_identity_mismatch')
    if lineup.get('lineupSeasonBattingVersion') != source.BATTING_OBSERVATION_VERSION:
        errors.append('passive_batting_version_mismatch')
    provenance=lineup.get('sourceProvenance')
    provenance=provenance if isinstance(provenance,dict) else {}
    if provenance.get('provider')!='MLB Stats API' or provenance.get('dataset')!=source.VERSION:
        errors.append('passive_batting_source_provenance_invalid')
    if official is not None and not _exact_feed_endpoint(provenance,official):
        errors.append('passive_batting_endpoint_identity_mismatch')
    retrieved=_parse_time(provenance.get('retrievedAtUtc'))
    commence=_parse_time(row.get('commenceTime') or row.get('commence_time'))
    result['retrievedAtUtc']=provenance.get('retrievedAtUtc')
    if retrieved is None:errors.append('passive_batting_retrieved_at_invalid')
    if commence is None:errors.append('passive_batting_commence_time_invalid')
    if commence is not None and retrieved is not None:
        result['preT45']=retrieved < commence-timedelta(minutes=45)
        if not result['preT45']:errors.append('passive_batting_not_pre_t45')
    side_ids={}
    for side in ('home','away'):
        order=lineup.get(side+'_batting_order')
        observations=lineup.get(side+'_lineup_season_batting')
        if not isinstance(order,list) or len(order)!=9:
            errors.append(side+'_batting_order_invalid');continue
        order_ids=[_positive_int(value) for value in order]
        if any(value is None for value in order_ids) or len(set(order_ids))!=9:
            errors.append(side+'_batting_order_identity_invalid');continue
        side_ids[side]=set(order_ids)
        if not isinstance(observations,list) or len(observations)!=9:
            errors.append(side+'_passive_batter_count_invalid');continue
        observed_ids=[]
        for expected_slot,item in enumerate(observations,1):
            if not isinstance(item,dict):
                errors.append(side+'_passive_batter_row_invalid');observed_ids.append(None);continue
            identity=_positive_int(item.get('playerId'))
            slot=_positive_int(item.get('battingSlot'))
            if identity is None or slot!=expected_slot:
                errors.append(side+'_passive_batter_identity_or_slot_invalid')
            _validate_batter_sample(item,side,errors)
            observed_ids.append(identity)
        if observed_ids!=order_ids:
            errors.append(side+'_passive_batter_order_mismatch')
        result[side+'BatterCount']=len(observations)
    if side_ids.get('home') and side_ids.get('away') and side_ids['home'] & side_ids['away']:
        errors.append('passive_batting_cross_team_identity_overlap')
    result['errors']=sorted(set(errors));result['valid']=not result['errors']
    return result


def passive_bullpen_roster_observation(row):
    """Validate roster identity metadata only; roster membership is not availability."""
    context=(row.get('advanced_context') or row.get('advancedContext') or {})
    bullpen=context.get('bullpen_fatigue') if isinstance(context,dict) else None
    bullpen=bullpen if isinstance(bullpen,dict) else {}
    home_payload=bullpen.get('home_bullpen_roster_player_ids')
    away_payload=bullpen.get('away_bullpen_roster_player_ids')
    present=home_payload is not None or away_payload is not None
    result={'present':present,'valid':False,'errors':[],'availabilityClaimed':False,
            'retrievedAtUtc':None,'preT45':None}
    if not present:return result
    errors=[]
    official=_official_game_pk(row)
    if official is None:errors.append('bullpen_roster_official_game_pk_invalid')
    observed_game=bullpen.get('game_pk')
    if observed_game is not None and _positive_int(observed_game)!=official:
        errors.append('bullpen_roster_game_identity_mismatch')
    if bullpen.get('bullpenRosterObservationStatus')!='OBSERVED_ROSTER_ONLY':
        errors.append('bullpen_roster_status_invalid')
    provenance=bullpen.get('bullpenRosterSourceProvenance')
    provenance=provenance if isinstance(provenance,dict) else {}
    if provenance.get('provider')!='MLB Stats API' or provenance.get('dataset')!=source.VERSION:
        errors.append('bullpen_roster_source_provenance_invalid')
    if official is not None and not _exact_feed_endpoint(provenance,official):
        errors.append('bullpen_roster_endpoint_identity_mismatch')
    retrieved=_parse_time(provenance.get('retrievedAtUtc'))
    commence=_parse_time(row.get('commenceTime') or row.get('commence_time'))
    result['retrievedAtUtc']=provenance.get('retrievedAtUtc')
    if retrieved is None:errors.append('bullpen_roster_retrieved_at_invalid')
    if commence is None:errors.append('bullpen_roster_commence_time_invalid')
    if commence is not None and retrieved is not None:
        result['preT45']=retrieved < commence-timedelta(minutes=45)
        if not result['preT45']:errors.append('bullpen_roster_not_pre_t45')
    side_ids={}
    for side in ('home','away'):
        values=bullpen.get(side+'_bullpen_roster_player_ids')
        if not isinstance(values,list) or not values:
            errors.append(side+'_bullpen_roster_missing');continue
        identities=[_positive_int(value) for value in values]
        if any(value is None for value in identities) or len(set(identities))!=len(identities):
            errors.append(side+'_bullpen_roster_identity_invalid');continue
        side_ids[side]=set(identities)
    if side_ids.get('home') and side_ids.get('away') and side_ids['home'] & side_ids['away']:
        errors.append('bullpen_roster_cross_team_identity_overlap')
    availability_fields=(
        'home_available_relievers','away_available_relievers',
        'home_unavailable_relievers','away_unavailable_relievers',
    )
    if any(bullpen.get(field) is not None for field in availability_fields):
        result['availabilityClaimed']=True
        errors.append('passive_roster_must_not_claim_reliever_availability')
    result['errors']=sorted(set(errors));result['valid']=not result['errors']
    return result


def persisted_observations(table, day):
    from boto3.dynamodb.conditions import Key
    import mlb_fundamentals_snapshot_v2 as snapshots
    rows=[];cursor=None
    while True:
        args={"KeyConditionExpression":Key("PK").eq(f"GAME_WINNERS#mlb#{day}") & Key("SK").begins_with("GAME#"),"ConsistentRead":True}
        if cursor:args["ExclusiveStartKey"]=cursor
        page=table.query(**args);rows.extend(page.get("Items") or [])
        cursor=page.get("LastEvaluatedKey")
        if not cursor:break
    evidence=[]
    for stored in rows:
        row=stored.get("data") or stored;snap=row.get("fundamentalsSnapshotV2") or {}
        groups=snap.get("groups") or {}
        lineup_state=passive_lineup_observation(row)
        roster_state=passive_bullpen_roster_observation(row)
        if lineup_state['present'] and not lineup_state['valid']:
            raise RuntimeError('invalid persisted passive batter observation: '+','.join(lineup_state['errors']))
        if roster_state['present'] and not roster_state['valid']:
            raise RuntimeError('invalid persisted passive bullpen roster observation: '+','.join(roster_state['errors']))
        team_snapshot=any(groups.get(key,{}).get("dataset")==source.VERSION for key in ("confirmed_lineups","bullpen_availability"))
        if team_snapshot:
            errors=snapshots.validate(snap)
            if errors:raise RuntimeError("invalid persisted team snapshot: "+','.join(errors))
        lv=groups.get("confirmed_lineups",{}).get("values") or {};bv=groups.get("bullpen_availability",{}).get("values") or {}
        evidence.append({"officialGamePk":str(row.get("officialGamePk")),"snapshotFingerprint":snap.get("fingerprint"),
            "teamSnapshot":team_snapshot,
            "bothLineups":all(lv.get(s+"Confirmed") is True for s in ("home","away")) if team_snapshot else False,
            "bothWorkloads":all(bv.get(s+"Usage1d3d5d") is not None for s in ("home","away")) if team_snapshot else False,
            "passiveLineupObservation":lineup_state,"passiveBullpenRosterObservation":roster_state,
            "retrievedAtUtc":groups.get("bullpen_availability",{}).get("retrievedAtUtc")})
    return {"storedGameRows":len(rows),"teamSnapshotRows":sum(r['teamSnapshot'] for r in evidence),
            "gamesWithBothLineups":sum(r["bothLineups"] for r in evidence),
            "gamesWithBothWorkloads":sum(r["bothWorkloads"] for r in evidence),
            "gamesWithPassiveBatterObservations":sum(r['passiveLineupObservation']['present'] for r in evidence),
            "gamesWithValidPassiveBatterObservations":sum(r['passiveLineupObservation']['valid'] for r in evidence),
            "gamesWithPassiveBullpenRosters":sum(r['passiveBullpenRosterObservation']['present'] for r in evidence),
            "gamesWithValidPassiveBullpenRosters":sum(r['passiveBullpenRosterObservation']['valid'] for r in evidence),
            "passiveRosterAvailabilityClaimCount":sum(r['passiveBullpenRosterObservation']['availabilityClaimed'] for r in evidence),
            "readOnly":True,"rows":evidence}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--persisted',action='store_true');args=parser.parse_args()
    now=datetime.now(timezone.utc);day=now.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    schedule=advanced._statsapi_schedule(day);history=advanced._statsapi_schedule_history(day)
    if not schedule.get('ok') or not history.get('ok'):raise RuntimeError('official schedule unavailable')
    started=time.monotonic();rows=[]
    for game in advanced._schedule_games(schedule):
        start=source._time(game.get('gameDate'))
        if game.get('status',{}).get('abstractGameState')!='Preview' or start is None or start-timedelta(minutes=45)<=now:
            continue
        at=time.monotonic();a,b=source.observe(day,game,history,advanced._http_get_json)
        rows.append({'officialGamePk':game['gamePk'],'elapsedSeconds':round(time.monotonic()-at,3),'lineup':a,'bullpen':b})
    report={'readOnly':True,'createdAtUtc':datetime.now(timezone.utc).isoformat(),'version':source.VERSION,
            'elapsedSeconds':round(time.monotonic()-started,3),'eligibleGames':len(rows),
            'gamesWithBothLineups':sum(all(r['lineup'].get(s+'_lineup_confirmed') is True for s in ('home','away')) for r in rows),
            'gamesWithBothWorkloads':sum(all(r['bullpen'].get(s+'_reliever_usage_1d_3d_5d') is not None for s in ('home','away')) for r in rows),
            'gamesWithPassiveBatterObservations':sum(all(isinstance(r['lineup'].get(s+'_lineup_season_batting'),list) for s in ('home','away')) for r in rows),
            'gamesWithPassiveBullpenRosters':sum(all(isinstance(r['bullpen'].get(s+'_bullpen_roster_player_ids'),list) for s in ('home','away')) for r in rows),
            'rows':rows}
    if args.persisted:
        import boto3
        report['persistedCollectorEvidence']=persisted_observations(boto3.resource('dynamodb').Table('parlay_platform_snapshots'),day)
    output=ROOT/'runtime_reports/mlb_team_context_live_proof_latest.json';output.write_text(json.dumps(report,indent=2,default=str)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},default=str))
    if rows and not report['gamesWithBothWorkloads']:raise RuntimeError('no complete observed relief workload')
    if report['elapsedSeconds']>90:raise RuntimeError('supplemental source proof exceeds 90 second slate budget')

if __name__=='__main__':main()
