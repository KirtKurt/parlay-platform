"""Publish checked historical inputs, keeping original evidence separate."""
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mlb_research'))
from mlb_research_store_v1 import Store,digest,now,utc
import mlb_research_sources_v1 as source
from mlb_research_dataset_v1 import publish_dataset


def store_for_stack():
    import boto3
    cf,lam=(boto3.client(n,region_name='us-east-1') for n in ('cloudformation','lambda'))
    fn=cf.describe_stack_resource(StackName='parlay-platform-dev',LogicalResourceId='MLBMLTrainingFunction')['StackResourceDetail']['PhysicalResourceId']
    env=lam.get_function_configuration(FunctionName=fn)['Environment']['Variables']
    return Store(env['MLB_ML_ARTIFACTS_BUCKET'])


def publish_historical(store,payload=None):
    if payload is None:
        report=json.loads((ROOT/'runtime_reports/mlb_data_admission_latest.json').read_text())
        pointer=report['historicalDevelopment']['artifact']
        if pointer['bucket']!=store.bucket or not pointer['key'].startswith('mlb/development-data/reconstructed-v1/'):
            raise ValueError('historical source scope mismatch')
        response=store.s3.get_object(Bucket=store.bucket,Key=pointer['key'],VersionId=pointer['versionId'])
        body=response['Body'].read()
        if hashlib.sha256(body).hexdigest()!=pointer['sha256']:
            raise ValueError('historical source checksum mismatch')
        payload=json.loads(body)
    original=payload['rows']
    games,_=source.schedule(min(r['slateDateEt'] for r in original),max(r['slateDateEt'] for r in original))
    official={}
    for game in games:
        if source.playable(game):
            day=utc(game['gameDate']).astimezone(source.ET).date().isoformat()
            official.setdefault(day,[]).append(game)
    grouped={}
    for r in original: grouped.setdefault(r['slateDateEt'],[]).append(r)
    rows=[]; excluded=[]
    for day,items in sorted(grouped.items()):
        slate=official.get(day,[])
        if (not slate or not all(source.final(g) for g in slate)
                or len({str(r['officialGamePk']) for r in items})!=len(items)
                or {str(g['gamePk']) for g in slate}!={str(r['officialGamePk']) for r in items}):
            excluded.append(day);continue
        by_id={str(g['gamePk']):g for g in slate}
        for item in items:
            if item.get('originalObservation') is not False or digest(item['features'])!=item['featureFingerprint']:
                raise ValueError('reconstructed input evidence invalid')
            f={k:float(v) for k,v in item['features'].items() if source.number(v) is not None}
            game=by_id[str(item['officialGamePk'])]
            row={'officialGamePk':str(item['officialGamePk']),'slateDateEt':day,'features':f,
                 'featureFingerprint':digest(f),'homeWon':int(item['label']['homeWon']),
                 'originalObservation':False,'slateComplete':True,'evidenceKind':'RECONSTRUCTED_HISTORICAL_DEVELOPMENT',
                 'homeRuns':source.count(game['teams']['home']['score']),'awayRuns':source.count(game['teams']['away']['score'])}
            if row['homeWon']!=int(game['teams']['home']['isWinner']):
                raise ValueError('historical official label mismatch')
            rows.append(row)
    artifact=store.artifact('historical-input',{'rows':rows,'excludedIncompleteSlates':excluded})
    store.latest('historical-input.json',{'artifact':artifact,'updatedAtUtc':now().isoformat()})
    return {'rows':len(rows),'excludedIncompleteSlates':len(excluded)}


if __name__=='__main__':
    store=store_for_stack()
    local=ROOT/'runtime_reports/mlb_historical_development_dataset_latest.json'
    payload=json.loads(local.read_text()) if local.exists() else None
    print(json.dumps(publish_historical(store,payload)))
    result=publish_dataset(store)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))
