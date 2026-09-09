"""Prepare isolated historical data and audit live admission; no trainer writes."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'hello_world'))
import mlb_historical_development_data as historical
from mlb_data_admission import audit_rows, daily_audit
import mlb_r7_historical_walkforward_bridge as bridge
import mlb_advanced_context as advanced


def query(table, pk, prefix):
    from boto3.dynamodb.conditions import Key
    rows, cursor = [], None
    while True:
        args = {'KeyConditionExpression': Key('PK').eq(pk) & Key('SK').begins_with(prefix), 'ConsistentRead': True}
        if cursor: args['ExclusiveStartKey'] = cursor
        page = table.query(**args); rows.extend(page.get('Items') or [])
        cursor = page.get('LastEvaluatedKey')
        if not cursor: return rows


def normalize_schedule(payload):
    games = [g for d in payload.get('dates', []) for g in d.get('games', [])]
    if payload.get('totalGames') != len(games):
        raise ValueError('incomplete official schedule')
    grouped = {}
    for game in games:
        grouped.setdefault(game['gamePk'], []).append(game)
    result = []
    for occurrences in grouped.values():
        identities = {(g.get('gameType'), g['teams']['home']['team']['id'], g['teams']['away']['team']['id']) for g in occurrences}
        if len(identities) != 1:
            raise ValueError('conflicting official game identity')
        played = [g for g in occurrences if g['status'].get('detailedState') not in ('Postponed','Cancelled')]
        candidates = played or occurrences
        chosen = dict(min(candidates, key=lambda g:g['gameDate']))
        chosen['scheduleOccurrences'] = len(occurrences)
        chosen['resumptionTimingAmbiguous'] = any(any(g.get(k) for k in ('resumeDate','resumeGameDate','resumedFrom','resumedFromDate')) for g in occurrences)
        result.append(chosen)
    return result


def schedule(first, last):
    endpoint = 'https://statsapi.mlb.com/api/v1/schedule?' + urlencode({'sportId': 1, 'startDate': first, 'endDate': last})
    payload = advanced._http_get_json(endpoint, timeout=30)
    games = normalize_schedule(payload)
    return games, {'endpoint': endpoint, 'retrievedAtUtc': datetime.now(timezone.utc).isoformat(),
                   'sha256': historical.digest(payload),'listedOccurrences':payload['totalGames'],'uniqueGames':len(games)}


def source_game(game, s3, bucket, persist):
    pk = game['gamePk']; key = historical.PREFIX + f'source-games/{pk}.json'
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') not in ('NoSuchKey', '404'):
            raise
    else:
        body = response['Body'].read()
        if hashlib.sha256(body).hexdigest() != response.get('Metadata', {}).get('sha256'):
            raise ValueError('cached prior-game checksum mismatch')
        result = json.loads(body)
        if result['officialGamePk'] != pk or historical.digest({k:v for k,v in result.items() if k!='fingerprint'}) != result['fingerprint']:
            raise ValueError('cached prior-game identity or fingerprint mismatch')
        return result
    endpoint = f'https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live'
    payload = advanced._http_get_json(endpoint, timeout=30)
    receipt = {'endpoint': endpoint, 'retrievedAtUtc': datetime.now(timezone.utc).isoformat(), 'fullPayloadSha256': historical.digest(payload)}
    result = historical.compact_game(payload, receipt)
    if result['officialGamePk'] != pk:
        raise ValueError('prior-game identity mismatch')
    if persist:
        body = historical.encoded(result)
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json',
                          Metadata={'sha256': hashlib.sha256(body).hexdigest()}, IfNoneMatch='*')
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') not in ('PreconditionFailed', '412'):
                raise
            # Another bounded preparation run saved this game first. Read and
            # verify that immutable receipt instead of replacing it.
            return source_game(game, s3, bucket, False)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', action='store_true')
    parser.add_argument('--persist', action='store_true')
    parser.add_argument('--max-rows', type=int, default=2000)
    args = parser.parse_args()
    if not 1 <= args.max_rows <= 5000: raise ValueError('max rows must be 1..5000')
    import boto3
    from botocore.config import Config
    config = Config(connect_timeout=10, read_timeout=120, retries={'max_attempts': 2})
    cf, lam, s3 = (boto3.client(name, region_name='us-east-1', config=config) for name in ('cloudformation', 'lambda', 's3'))
    def physical(logical):
        return cf.describe_stack_resource(StackName='parlay-platform-dev', LogicalResourceId=logical)['StackResourceDetail']['PhysicalResourceId']
    function = physical('MLBMLTrainingFunction')
    env = lam.get_function_configuration(FunctionName=function)['Environment']['Variables']
    bucket = env['MLB_ML_ARTIFACTS_BUCKET']
    if args.persist and s3.get_bucket_versioning(Bucket=bucket).get('Status') != 'Enabled':
        raise ValueError('versioned development storage required before source writes')
    os.environ['SNAPSHOTS_TABLE'] = physical('SnapshotsTable')
    os.environ['OUTCOMES_TABLE'] = physical('OutcomesTable')
    ddb = boto3.resource('dynamodb', region_name='us-east-1')
    table = ddb.Table(os.environ['SNAPSHOTS_TABLE'])
    response = lam.invoke(FunctionName=function, Payload=b'{"sport":"mlb","mode":"status"}')
    if response.get('FunctionError'): raise ValueError('trainer status failed')
    status = json.loads(response['Payload'].read())
    candidate = status['latestCandidate']
    pointer = candidate['artifacts']['dataset']
    if pointer['bucket'] != bucket or not pointer.get('versionId'): raise ValueError('candidate dataset pointer mismatch')
    response = s3.get_object(Bucket=bucket, Key=pointer['key'], VersionId=pointer['versionId'])
    body = response['Body'].read()
    if hashlib.sha256(body).hexdigest() != pointer['sha256']: raise ValueError('candidate source checksum mismatch')
    dataset = json.loads(body)
    rows = [r for group in dataset['partitions'].values() for r in group]
    audit = audit_rows(rows)
    state = bridge._plain(table.get_item(Key={'PK':bridge.STATE_PK, 'SK':bridge.STATE_SK}, ConsistentRead=True).get('Item') or {}).get('data') or {}
    ledgers = sorted(state.get('completedSlates') or [], key=lambda r:r['slateDateEt'])
    if not ledgers: raise ValueError('no historical archives available')
    today = datetime.now(timezone.utc).astimezone(historical.ET).date()
    import mlb_canonical_final_labels_v1 as canonical
    import mlb_prospective_trainer_read_repair as repair
    canonical.history.PULLS = table
    canonical.outcomes_tbl = ddb.Table(os.environ['OUTCOMES_TABLE'])
    repair.install(canonical)
    days, canonical_rows = [], []
    release_day = historical.utc(env['MLB_ML_RELEASE_CUTOFF_UTC']).astimezone(historical.ET).date()
    days_back = min(14, max(0, (today-release_day).days))
    for n in range(days_back, -1, -1):
        day = (today-timedelta(days=n)).isoformat()
        print(json.dumps({'phase':'daily_admission','slateDateEt':day}),flush=True)
        games, receipt = schedule(day, day)
        locks, rejected = canonical._validated_canonical_locks(day)
        labels = canonical._labels_for_slate(day)
        item = daily_audit(day, query(table, f'GAME_WINNERS#mlb#{day}', 'GAME#'), locks, rejected, labels, games, canonical)
        item['scheduleReceipt'] = receipt
        days.append(item)
        by_pk = {str(r.get('official_game_pk')):r for r in labels}
        final_ids = {str(g['gamePk']) for g in games if historical.is_final(g)}
        for lock in locks:
            pk = str(lock.get('officialGamePk'))
            if pk in by_pk and pk in final_ids:
                canonical_rows.append(canonical._joined_training_row(day, by_pk[pk], lock, slate_finalized=True))
    unique = {(str(r.get('slateDateEt')), str(r.get('officialGamePk'))):r for r in rows}
    unique.update({(str(r.get('slateDateEt')),str(r.get('officialGamePk'))):r for r in canonical_rows})
    audit_all = audit_rows(list(unique.values()))
    now = datetime.now(timezone.utc).isoformat()
    report = {'createdAtUtc': now, 'version': historical.VERSION,
              'historicalArchiveSlates':len(ledgers), 'historicalArchiveGames':sum(int(r.get('eligibleGameCount',0)) for r in ledgers),
              'frozenCandidateAdmission': {k:v for k,v in audit.items() if k!='rows'},
              'combinedOriginalAdmission':{k:v for k,v in audit_all.items() if k!='rows'},
              'daily':days, 'runtimeStateMutated':False, 'productionAuthorityChanged':False,
              'budgetSetupRequested':False}
    report['preparationStatus'] = 'RUNNING' if not args.inventory else 'INVENTORY_ONLY'
    report['ok'] = bool(args.inventory)
    audit_all['createdAtUtc'] = now
    (ROOT/'runtime_reports/mlb_data_admission_rows_latest.json').write_text(json.dumps(audit_all,indent=2)+'\n')
    (ROOT/'runtime_reports/mlb_data_admission_latest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'phase':'admission_complete','historicalArchiveGames':report['historicalArchiveGames'],
                      'combinedOriginalAdmission':report['combinedOriginalAdmission']},indent=2),flush=True)
    if not args.inventory:
        selected, archives, rejected_archives = [], [], []
        def read_archive(ledger):
            try:
                archived = bridge._get_artifact(s3, ledger['artifact']); bridge._verify_dataset(archived,ledger)
            except Exception as exc:
                return ledger,None,str(exc)
            return ledger,archived,None
        candidates = [l for l in ledgers if l['slateDateEt'] < today.isoformat()]
        # Read immutable archived slates concurrently; production DynamoDB
        # readers above remain sequential and no trainer is invoked.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for ledger,archived,error in pool.map(read_archive,candidates):
                if error:
                    rejected_archives.append({'slateDateEt':ledger['slateDateEt'],'reason':error});continue
                if selected and len(selected)+len(archived['records']) > args.max_rows:continue
                archives.append({'dataset':archived,'artifact':ledger['artifact']})
                selected.extend(archived['records'])
        if not selected: raise ValueError('no verified historical rows')
        # The frozen R8 candidate excludes diagnostic historical rows. Replay
        # the deployed trainer's whole-slate historical window as well, so the
        # audit accounts for those rows without changing their source evidence.
        historical_window = []
        limit = max(400, min(2000, int(env.get('MLB_R7_HISTORICAL_MAX_ROWS', '500'))))
        release = historical.utc(env['MLB_ML_RELEASE_CUTOFF_UTC']).date().isoformat()
        for archive in archives:
            data = archive['dataset']
            if data['slateDateEt'] >= release: continue
            if historical_window and len(historical_window)+len(data['records']) > limit: break
            historical_window.extend(bridge.materialize_record(r, dataset=data, artifact=archive['artifact'],
                feature_version=env['MLB_ML_FEATURE_VECTOR_VERSION']) for r in data['records'])
            if len(historical_window) >= limit: break
        unique.update({(str(r['slateDateEt']),str(r['officialGamePk'])):r for r in historical_window})
        audit_all = audit_rows(list(unique.values()));audit_all['createdAtUtc']=now
        report['combinedOriginalAdmission']={k:v for k,v in audit_all.items() if k not in ('rows','createdAtUtc')}
        report['historicalTrainerWindowRows']=len(historical_window)
        report['sourceScope']='deployed historical window, frozen candidate and recent canonical locks'
        (ROOT/'runtime_reports/mlb_data_admission_rows_latest.json').write_text(json.dumps(audit_all,indent=2)+'\n')
        (ROOT/'runtime_reports/mlb_data_admission_latest.json').write_text(json.dumps(report,indent=2)+'\n')
        first = (datetime.fromisoformat(min(r['slateDateEt'] for r in selected)).date()-timedelta(days=14)).isoformat()
        last = max(r['slateDateEt'] for r in selected)
        official, schedule_receipt = schedule(first,last)
        official_map = {g['gamePk']:g for g in official}
        completed = [g for g in official if g.get('gameType')=='R' and historical.is_final(g)]
        if len(completed)>7000: raise ValueError('historical source range exceeds bounded request budget')
        source_errors, sources = {}, []
        print(json.dumps({'phase':'fetch_prior_games','requestedSources':len(completed),'selectedArchiveRows':len(selected)}),flush=True)
        def read(game):
            try: return game['gamePk'],source_game(game,s3,bucket,args.persist),None
            except Exception as exc: return game['gamePk'],None,type(exc).__name__+': '+str(exc)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for pk, source, error in pool.map(read,completed):
                if error: source_errors[pk]=error
                else: sources.append(source)
                if (len(sources)+len(source_errors))%100==0:
                    print(json.dumps({'phase':'prior_games','completed':len(sources),'failed':len(source_errors)}),flush=True)
        source_map = {s['officialGamePk']:s for s in sources}
        prepared, rejections = [], []
        seen = set()
        for archive in archives:
            for record in archive['dataset']['records']:
                try:
                    pk=int(record['officialGamePk']);target=official_map[pk];day=record['slateDateEt']
                    if pk in seen: raise ValueError('duplicate historical game')
                    seen.add(pk)
                    ids={t['team']['id'] for t in target['teams'].values()}
                    relevant=[]
                    for old in official:
                        age=(datetime.fromisoformat(day).date()-historical.utc(old['gameDate']).astimezone(historical.ET).date()).days
                        if old.get('gameType')!='R' or not 1<=age<=14 or not any(t['team']['id'] in ids for t in old['teams'].values()):continue
                        # Postponements contribute no statistics. Suspensions,
                        # missing sources and late completions remain rejected.
                        if old['status'].get('detailedState') in ('Postponed','Cancelled'):continue
                        if old.get('resumptionTimingAmbiguous'):raise ValueError('prior-game resumption timing ambiguous:'+str(old['gamePk']))
                        if old['gamePk'] not in source_map:raise ValueError('prior-game source unavailable:'+str(old['gamePk']))
                        relevant.append(source_map[old['gamePk']])
                    prepared.append(historical.materialize(record,archive['dataset'],archive['artifact'],target,relevant,now))
                except Exception as exc:
                    rejections.append({'officialGamePk':str(record['officialGamePk']),'slateDateEt':record['slateDateEt'],'reason':str(exc)})
        payload={'version':historical.VERSION,'createdAtUtc':now,'rows':prepared,'priorGameSources':sources,
                 'scheduleReceipt':schedule_receipt,'rejections':rejections,'rejectedArchives':rejected_archives,
                 'sourceErrors':source_errors,'developmentOnly':True,'prospectiveQualificationEvidence':False}
        report['historicalDevelopment']={'verifiedArchiveRows':len(selected),'preparedGames':len(prepared),
            'rejectedGames':len(rejections),'rejectionReasons':dict(Counter(r['reason'] for r in rejections)),
            'priorGameSources':len(sources),'sourceFailures':len(source_errors),
            'firstSlate':min(r['slateDateEt'] for r in selected),'lastSlate':last,
            'missingHistoricalFields':['current-game pregame starter identity','pregame batting order'],
            'prospectiveQualificationEvidence':False}
        if not prepared: raise ValueError('no historical development games prepared')
        if args.persist:report['historicalDevelopment']['artifact']=historical.write_verified(s3,bucket,payload)
        output=ROOT/'runtime_reports/mlb_historical_development_dataset_latest.json'
        output.write_text(json.dumps(payload,sort_keys=True)+'\n')
        report['preparationStatus']='COMPLETE'
        report['ok']=True
    audit_all['createdAtUtc'] = now
    (ROOT/'runtime_reports/mlb_data_admission_rows_latest.json').write_text(json.dumps(audit_all,indent=2)+'\n')
    (ROOT/'runtime_reports/mlb_data_admission_latest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='daily'},indent=2))
    print(json.dumps([{k:v for k,v in d.items() if k not in ('admission','rejectedLocks','waitingOrMissingLabels','scheduleReceipt')} for d in days],indent=2))


if __name__=='__main__':main()
