"""Read-only current pregame source proof; never backfills a locked snapshot."""
import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'hello_world'))
import mlb_advanced_context as advanced
import mlb_statsapi_team_context as source


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
        if not any(groups.get(key,{}).get("dataset")==source.VERSION for key in ("confirmed_lineups","bullpen_availability")):continue
        errors=snapshots.validate(snap)
        if errors:raise RuntimeError("invalid persisted team snapshot: "+','.join(errors))
        lv=groups.get("confirmed_lineups",{}).get("values") or {};bv=groups.get("bullpen_availability",{}).get("values") or {}
        evidence.append({"officialGamePk":str(row.get("officialGamePk")),"snapshotFingerprint":snap["fingerprint"],
            "bothLineups":all(lv.get(s+"Confirmed") is True for s in ("home","away")),
            "bothWorkloads":all(bv.get(s+"Usage1d3d5d") is not None for s in ("home","away")),
            "retrievedAtUtc":groups.get("bullpen_availability",{}).get("retrievedAtUtc")})
    return {"storedGameRows":len(rows),"teamSnapshotRows":len(evidence),"gamesWithBothLineups":sum(r["bothLineups"] for r in evidence),
            "gamesWithBothWorkloads":sum(r["bothWorkloads"] for r in evidence),"readOnly":True,"rows":evidence}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--persisted',action='store_true');args=parser.parse_args()
    now=datetime.now(timezone.utc);day=now.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    schedule=advanced._statsapi_schedule(day);history=advanced._statsapi_schedule_history(day)
    if not schedule.get('ok') or not history.get('ok'):raise RuntimeError('official schedule unavailable')
    started=time.monotonic();rows=[]
    for game in advanced._schedule_games(schedule):
        if game.get('status',{}).get('abstractGameState')!='Preview' or source._time(game.get('gameDate'))-timedelta(minutes=45)<=now:
            continue
        at=time.monotonic();a,b=source.observe(day,game,history,advanced._http_get_json)
        rows.append({'officialGamePk':game['gamePk'],'elapsedSeconds':round(time.monotonic()-at,3),'lineup':a,'bullpen':b})
    report={'readOnly':True,'createdAtUtc':datetime.now(timezone.utc).isoformat(),'version':source.VERSION,
            'elapsedSeconds':round(time.monotonic()-started,3),'eligibleGames':len(rows),
            'gamesWithBothLineups':sum(all(r['lineup'].get(s+'_lineup_confirmed') is True for s in ('home','away')) for r in rows),
            'gamesWithBothWorkloads':sum(all(r['bullpen'].get(s+'_reliever_usage_1d_3d_5d') is not None for s in ('home','away')) for r in rows),
            'rows':rows}
    if args.persisted:
        import boto3
        report['persistedCollectorEvidence']=persisted_observations(boto3.resource('dynamodb').Table('parlay_platform_snapshots'),day)
    output=ROOT/'runtime_reports/mlb_team_context_live_proof_latest.json';output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}))
    if rows and not report['gamesWithBothWorkloads']:raise RuntimeError('no complete observed relief workload')
    if report['elapsedSeconds']>90:raise RuntimeError('supplemental source proof exceeds 90 second slate budget')

if __name__=='__main__':main()
