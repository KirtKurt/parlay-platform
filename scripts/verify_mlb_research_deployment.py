"""Read-only proof of deployed bytes, natural execution, and source evidence."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mlb_research'))
from mlb_research_store_v1 import Store,digest,now,utc


def verify(expected_sha,wait_seconds):
    import boto3
    cf,lam=(boto3.client(n,region_name='us-east-1') for n in ('cloudformation','lambda'))
    function=cf.describe_stack_resource(StackName='parlay-platform-dev',LogicalResourceId='MLBResearchFunction')['StackResourceDetail']['PhysicalResourceId']
    deployed=lam.get_function(FunctionName=function)
    cfg=deployed['Configuration'];env=cfg['Environment']['Variables']
    if env.get('INQSI_DEPLOY_GIT_SHA')!=expected_sha or cfg.get('State')!='Active' or cfg.get('LastUpdateStatus')!='Successful':
        raise ValueError('research deployment identity or state mismatch')
    with urlopen(deployed['Code']['Location'],timeout=60) as response: body=response.read()
    if base64.b64encode(hashlib.sha256(body).digest()).decode()!=cfg['CodeSha256']:
        raise ValueError('deployed archive checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        verified={}
        for path in sorted((ROOT/'mlb_research').glob('*.py')):
            if archive.read(path.name)!=path.read_bytes():
                raise ValueError('deployed research module differs: '+path.name)
            verified[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    report={'ok':False,'function':function,'expectedGitSha':expected_sha,'deployedModules':verified,
            'codeSha256':cfg['CodeSha256'],'readOnly':True,'updatedAtUtc':now().isoformat()}
    output=ROOT/'runtime_reports/mlb_research_deployment_proof_latest.json'
    store=Store(env['MLB_ML_ARTIFACTS_BUCKET'])
    deadline=time.monotonic()+wait_seconds
    while True:
        response=lam.invoke(FunctionName=function,Payload=b'{"mode":"status"}')
        payload=json.loads(response['Payload'].read())
        report['status']=payload
        if response.get('FunctionError'): raise ValueError('deployed research status failed')
        capture=payload.get('capture',{});training=payload.get('training',{});ingestion=payload.get('ingestion',{})
        report['executionVerified']=(capture.get('health') in ('HEALTHY','PARTIAL')
            and capture.get('evidence',{}).get('deploymentGitSha')==expected_sha
            and training.get('health')=='HEALTHY'
            and training.get('evidence',{}).get('deploymentGitSha')==expected_sha
            and ingestion.get('health') in ('HEALTHY','PARTIAL'))
        output.write_text(json.dumps(report,indent=2)+'\n')
        if report['executionVerified'] or time.monotonic()>=deadline: break
        print(json.dumps({'waitingForNaturalExecution':{k:payload.get(k,{}).get('health') for k in ('capture','training','ingestion')}}),flush=True)
        time.sleep(30)
    day=now().astimezone(__import__('zoneinfo').ZoneInfo('America/New_York')).date().isoformat()
    snapshot_proofs=[]
    for key in store.keys('snapshots/'+day+'/'):
        snap=store.get(key)
        if digest(snap['features'])!=snap['featureFingerprint'] or utc(snap['capturedAtUtc'])>utc(snap['featureCutoffUtc']):
            raise ValueError('persisted original snapshot integrity failure')
        teams=snap['playerWindows']['teams']
        snapshot_proofs.append({'key':key,'fingerprint':digest(snap),'checkpoint':snap['checkpoint'],
            'pitcherEntries':sum(p['pitcher'] for t in teams.values() for p in t['players']),
            'lineupBatters':sum(p['lineupSlot'] is not None for t in teams.values() for p in t['players']),
            'incompletePitcherWindows':sum(p['pitching']['30d']['status']=='INCOMPLETE' for t in teams.values() for p in t['players'] if p['pitcher'])})
    report['snapshotProofs']=snapshot_proofs
    report['sourceCoverageComplete']=all(report['status'].get(k,{}).get('health')=='HEALTHY' for k in ('capture','training','ingestion'))
    report['ok']=report['executionVerified'];report['updatedAtUtc']=now().isoformat()
    import mlb_research_sources_v1 as source
    games,_=source.schedule(day)
    expected={str(g['gamePk']) for g in games if source.playable(g)}
    recorded={p['key'].split('/')[2] for p in snapshot_proofs if p['checkpoint']=='T10'}
    report['scheduledGames']=len(expected)
    report['gamesWithT10Snapshot']=len(recorded)
    report['allGamesVerified']=bool(expected) and expected==recorded and report['sourceCoverageComplete']
    output.write_text(json.dumps(report,indent=2)+'\n')
    if not report['ok']: raise ValueError('natural capture, training, or ingestion not verified before deadline')
    print(json.dumps({k:v for k,v in report.items() if k not in ('status','deployedModules','snapshotProofs')},indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--expected-sha',required=True);parser.add_argument('--wait-seconds',type=int,default=1800)
    args=parser.parse_args()
    try:verify(args.expected_sha,args.wait_seconds)
    except Exception as exc:
        path=ROOT/'runtime_reports/mlb_research_deployment_proof_latest.json'
        report=json.loads(path.read_text()) if path.exists() else {}
        report.update(ok=False,error=str(exc) if isinstance(exc,ValueError) else type(exc).__name__,updatedAtUtc=now().isoformat())
        path.write_text(json.dumps(report,indent=2)+'\n')
        raise
