"""Read-only replay of a failed research capture with bounded diagnostics."""
import json
import os
from pathlib import Path
import sys
import traceback
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mlb_research'))
import mlb_research_runtime_v1 as runtime
import mlb_research_sources_v1 as source
from mlb_research_store_v1 import Store,now,utc,encoded


def diagnose():
    import boto3
    cf,lam=(boto3.client(n,region_name='us-east-1') for n in ('cloudformation','lambda'))
    function=cf.describe_stack_resource(StackName='parlay-platform-dev',LogicalResourceId='MLBResearchFunction')['StackResourceDetail']['PhysicalResourceId']
    cfg=lam.get_function_configuration(FunctionName=function)
    env=cfg['Environment']['Variables']
    os.environ['ODDS_API_KEY']=env['ODDS_API_KEY']
    store=Store(env['MLB_ML_ARTIFACTS_BUCKET'])
    status=runtime.health(store)
    report={'readOnly':True,'productionWrites':False,'deployedGitSha':env['INQSI_DEPLOY_GIT_SHA'],
            'updatedAtUtc':now().isoformat(),'capture':status['capture'],'diagnostics':[]}
    at=now();day=at.astimezone(source.ET).date().isoformat()
    games,_=source.schedule(day)
    prior,_=runtime.bundle(store,'prior-games',at)
    statcast,_=runtime.bundle(store,'statcast',at)
    failures=(status['capture'].get('evidence') or {}).get('failures',[])
    for failure in failures[:2]:
        game=next((g for g in games if str(g['gamePk'])==str(failure['gamePk'])),None)
        checkpoint=failure.get('checkpoint')
        detail={'gamePk':failure['gamePk'],'checkpoint':checkpoint}
        report['diagnostics'].append(detail)
        if not game or checkpoint not in ('early','T45','T30','T10'):
            detail['status']='NO_CURRENT_SNAPSHOT_REPLAY';continue
        market=[store.get(k) for k in store.keys(f"markets/{day}/{game['gamePk']}/")]
        detail['storedMarketObservations']=len(market)
        home,away=(game['teams'][s]['team']['name'] for s in ('home','away'))
        detail['officialStart']=game['gameDate']
        fetch=source.fetch
        def observe_fetch(url,**kwargs):
            payload,receipt=fetch(url,**kwargs)
            if url.startswith('https://api.the-odds-api.com/'):
                detail['sameTeamProviderEvents']=[{'id':e.get('id'),'commenceTime':e.get('commence_time'),
                    'bookmakers':len(e.get('bookmakers',[]))} for e in payload
                    if e.get('home_team')==home and e.get('away_team')==away]
            return payload,receipt
        try:
            source.fetch=observe_fetch
            detail['liveMatchedMarketAvailable']=str(game['gamePk']) in source.markets([game])
        except Exception as exc:
            detail['liveMarketError']=type(exc).__name__
        finally:source.fetch=fetch
        try:
            value=runtime.snapshot(store,game,checkpoint,market,prior.get('schedule'),prior,statcast)
            decoded=json.loads(encoded(value))
            detail.update(status='READ_ONLY_SNAPSHOT_SUCCEEDED',featureCount=len(value['features']),
                jsonRoundTripEqual=decoded==value,
                roundTripDifferingFields=[k for k in value if decoded.get(k)!=value[k]])
        except Exception as exc:
            safe={'same-time market unavailable':'MARKET_UNAVAILABLE','snapshot deadline passed':'DEADLINE_PASSED',
                'source collection crossed snapshot deadline':'SOURCE_COLLECTION_CROSSED_DEADLINE',
                'official feed identity changed':'FEED_IDENTITY_CHANGED','active roster unavailable':'ROSTER_UNAVAILABLE',
                'ambiguous player response':'AMBIGUOUS_PLAYER_RESPONSE','invalid active roster identities':'ROSTER_IDENTITY_INVALID',
                'player observation requires pregame feed':'FEED_NOT_PREGAME'}
            detail.update(status='READ_ONLY_SNAPSHOT_FAILED',error=type(exc).__name__,
                code=safe.get(str(exc),'UNCLASSIFIED'),
                frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno}
                        for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename).name.startswith('mlb_')])
    return report


if __name__=='__main__':
    report=diagnose()
    (ROOT/'runtime_reports/mlb_research_capture_diagnostic_latest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
