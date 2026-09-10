"""Isolated research worker; invoked only by the existing MLB training owner."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import mlb_research_sources_v1 as source
import mlb_player_windows_v1 as players
import mlb_research_signals_v1 as signals
import mlb_research_models_v1 as models
from mlb_research_store_v1 import Store, digest, now, utc

VERSION='MLB-RESEARCH-RUNTIME-v1-independent-future-tests'


def bundle(store,name,cutoff):
    pointer=store.get(name+'.json')
    if not pointer:
        return {},'NOT_OBSERVED'
    age=(cutoff-utc(pointer['updatedAtUtc'])).total_seconds()
    if not 0 <= age <= 10800:
        return {},'STALE' if age>0 else 'INVALID_FUTURE_TIME'
    value=store.load(pointer['artifact'])
    return value,'COMPLETE' if value.get('coverageComplete') else 'PARTIAL'


def active_frozen(store):
    pointer=store.get('active-frozen.json')
    return store.load(pointer['artifact']) if pointer else None


def snapshot(store,game,checkpoint,market,history,prior,statcast):
    minutes={'early':54,'T45':45,'T30':30,'T10':10}[checkpoint]
    cutoff=utc(game['gameDate'])-timedelta(minutes=minutes)
    if now()>cutoff:
        raise ValueError('snapshot deadline passed')
    payload,receipt=source.feed(game)
    observed=now()
    completed={g['officialGamePk']:g for g in prior.get('games',[]) if utc(g['completedAtUtc'])<observed}
    observation=players.observe(game,payload,completed,observed)
    conditions=signals.conditions(game,payload,list(completed.values()),observed)
    features={**players.features(observation),
              **signals.prior_features(game,list(completed.values()),history,observed),
              **signals.statcast_features(observation,statcast,observed), **conditions['features']}
    day=utc(game['gameDate']).astimezone(source.ET).date().isoformat()
    previous=None
    for cp in ('early','T45','T30'):
        if cp==checkpoint:break
        value=store.get(f"snapshots/{day}/{game['gamePk']}/{cp}.json")
        if value and utc(value['capturedAtUtc'])<now():previous=value
    for side in ('home','away'):
        current=observation['teams'][side]['battingOrder']
        prior_order=previous['playerWindows']['teams'][side]['battingOrder'] if previous else None
        features[side+'LineupChangesSincePreviousCheckpoint']=(sum(a!=b for a,b in zip(current,prior_order))
            if current and prior_order and len(current)==len(prior_order)==9 else None)
    captured=now()
    if captured>cutoff:
        raise ValueError('source collection crossed snapshot deadline')
    features.update(signals.market_path(market,captured))
    if source.number(features.get('marketHomeProbability')) is None:
        raise ValueError('same-time market unavailable')
    features=signals.interactions(features)
    return {'version':VERSION,'officialGamePk':str(game['gamePk']),
            'deploymentGitSha':os.environ.get('INQSI_DEPLOY_GIT_SHA'),
            'slateDateEt':utc(game['gameDate']).astimezone(source.ET).date().isoformat(),
            'commenceTime':game['gameDate'],'checkpoint':checkpoint,'featureCutoffUtc':cutoff.isoformat(),
            'capturedAtUtc':captured.isoformat(),'features':features,'featureFingerprint':digest(features),
            'playerWindows':observation,'conditions':conditions,'feedReceipt':receipt,
            'originalObservation':True,'outcomeKnownAtCapture':False,'productionAuthority':False}


def validate_snapshot(value,game):
    cutoff=utc(game['gameDate'])-timedelta(minutes=10)
    if (value.get('checkpoint')!='T10' or value.get('originalObservation') is not True
            or value.get('outcomeKnownAtCapture') is not False or value.get('productionAuthority') is not False
            or str(value.get('officialGamePk'))!=str(game['gamePk'])
            or utc(value['commenceTime'])!=utc(game['gameDate'])
            or value['slateDateEt']!=utc(game['gameDate']).astimezone(source.ET).date().isoformat()
            or utc(value['featureCutoffUtc'])!=cutoff or utc(value['capturedAtUtc'])>cutoff
            or digest(value['features'])!=value['featureFingerprint']):
        raise ValueError('invalid original T10 observation')
    p=source.number(value['features'].get('marketHomeProbability'))
    if p is None or not 0<p<1:
        raise ValueError('invalid snapshot market baseline')
    return value


def save_prediction(store,value,frozen,clock=now):
    if not frozen or value['checkpoint']!='T10' or value['slateDateEt']<frozen['firstProspectiveSlateDate']:
        return 'NO_FROZEN_MODEL_DUE'
    key=f"predictions/{frozen['id']}/{value['slateDateEt']}/{value['officialGamePk']}.json"
    if store.get(key):
        return 'EXISTING'
    cutoff=utc(value['featureCutoffUtc'])
    at=clock()
    if not utc(frozen['frozenAtUtc'])<utc(value['capturedAtUtc'])<=at<=cutoff:
        raise ValueError('future prediction timing invalid')
    names=frozen['model']['features']
    if names and sum(source.number(value['features'].get(k)) is not None for k in names)/len(names)<.8:
        raise ValueError('frozen model input coverage insufficient')
    probability=float(models.predict([value],frozen['model'])[0])
    predicted=clock()
    if predicted>cutoff or source.number(probability) is None or not 0<=probability<=1:
        raise ValueError('prediction calculation crossed deadline or invalid probability')
    store.once(key,{'frozenId':frozen['id'],'snapshot':value,'homeProbability':probability,
                    'predictedAtUtc':predicted.isoformat(),'outcomeKnownAtCapture':False})
    return 'NEW'


def capture(store):
    at=now(); day=at.astimezone(source.ET).date().isoformat()
    games,receipt=source.schedule(day)
    preview=[g for g in games if g['status']['abstractGameState']=='Preview' and utc(g['gameDate'])>at]
    prior,prior_status=bundle(store,'prior-games',at)
    statcast,statcast_status=bundle(store,'statcast',at)
    history=prior.get('schedule')
    frozen=active_frozen(store)
    report={'ok':True,'version':VERSION,'status':'NO_PREGAME_GAMES' if not preview else 'CAPTURE_COMPLETE',
            'updatedAtUtc':at.isoformat(),'games':len(games),'pregameGames':len(preview),'snapshotWrites':0,
            'predictionWrites':0,'failures':[],'missedT10':[], 'priorSources':prior_status,'statcastSources':statcast_status,
            'scheduleReceipt':receipt,'productionAuthorityChanged':False}
    odds=source.markets(preview) if preview else {}
    due=[]
    slot=int(at.timestamp())//120
    for game in preview:
        pk=str(game['gamePk']); minutes=(utc(game['gameDate'])-at).total_seconds()/60
        if pk in odds:
            store.once(f'markets/{day}/{pk}/{slot}.json',{**odds[pk],'capturedAtUtc':now().isoformat()})
        cp=next((name for n,name in ((10,'T10'),(30,'T30'),(45,'T45')) if n<minutes<=n+9),None)
        if cp is None and 54<minutes<=720: cp='early'
        if minutes<=10 and not store.get(f'snapshots/{day}/{pk}/T10.json'):
            report['missedT10'].append(pk)
        if not cp: continue
        name=f'snapshots/{day}/{pk}/{cp}.json'
        existing=store.get(name)
        if existing:
            try:
                report['predictionWrites']+=save_prediction(store,existing,frozen)=='NEW'
            except Exception as exc:
                report['failures'].append({'gamePk':pk,'phase':'prediction_retry','error':type(exc).__name__})
        else:
            due.append((minutes,game,cp,name))
    # Earliest deadlines first. Deferred early captures retry next two-minute tick.
    due.sort(key=lambda x:x[0])
    def collect(item):
        _,game,cp,name=item
        try:
            market=[store.get(key) for key in store.keys(f"markets/{day}/{game['gamePk']}/")]
            value=snapshot(store,game,cp,market,history,prior,statcast)
            saved=store.once(name,value)
            prediction=save_prediction(store,saved,frozen)
            return {'snapshot':1,'prediction':int(prediction=='NEW')}
        except Exception as exc:
            return {'error':type(exc).__name__,'gamePk':str(game['gamePk']),'checkpoint':cp}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(collect,due[:8]):
            if 'error' in result: report['failures'].append(result)
            else:
                report['snapshotWrites']+=result['snapshot'];report['predictionWrites']+=result['prediction']
    report['deferredGames']=len(due[8:])
    report['updatedAtUtc']=now().isoformat()
    if report['failures'] or report['missedT10'] or prior_status!='COMPLETE' or statcast_status!='COMPLETE':
        report['status']='PARTIAL' if preview else 'NO_PREGAME_GAMES'
    store.latest('capture.json',report)
    return report


def evaluate_fresh(store,frozen):
    name=f"sealed/{frozen['id']}.json"
    sealed=store.get(name)
    if sealed:
        return sealed
    last=(now().astimezone(source.ET).date()-timedelta(days=1)).isoformat()
    if last<frozen['firstProspectiveSlateDate']:
        return {'status':'WAITING_FOR_FRESH_SLATES','count':0,'sealed':False}
    games,_=source.schedule(frozen['firstProspectiveSlateDate'],last)
    days=sorted({utc(g['gameDate']).astimezone(source.ET).date().isoformat() for g in games if source.playable(g)})
    rows,probabilities=[],[]
    for day in days:
        slate=[g for g in games if source.playable(g) and utc(g['gameDate']).astimezone(source.ET).date().isoformat()==day]
        if not all(source.final(g) for g in slate):
            return {'status':'WAITING_FOR_COMPLETE_FRESH_SLATES','count':len(rows),'sealed':False,'blockedSlate':day}
        missing=[]
        for game in slate:
            entry=store.get(f"predictions/{frozen['id']}/{day}/{game['gamePk']}.json")
            if not entry:
                missing.append(str(game['gamePk']));continue
            value=validate_snapshot(entry['snapshot'],game)
            predicted=utc(entry['predictedAtUtc'])
            p=source.number(entry['homeProbability'])
            if (entry['frozenId']!=frozen['id'] or entry.get('outcomeKnownAtCapture') is not False
                    or not utc(frozen['frozenAtUtc'])<utc(value['capturedAtUtc'])<=predicted<=utc(value['featureCutoffUtc'])
                    or p is None or abs(p-float(models.predict([value],frozen['model'])[0]))>1e-10):
                raise ValueError('future prediction replay failed')
            rows.append({**value,'homeWon':int(game['teams']['home']['isWinner'])});probabilities.append(p)
        if missing:
            return store.once(name,{'status':'FRESH_TEST_SEALED_INCOMPLETE','sealed':True,'passed':False,
                'frozenId':frozen['id'],'sealedAtUtc':now().isoformat(),'missingGameIds':missing,'blockedSlate':day,
                'testCanBeReopened':False,'count':len(rows),'firstActivationRequiresManualReview':False})
        if len(rows)>=models.PROTOCOL['freshTestMinimum']:
            comp=models.comparison(rows,probabilities);blocked=models.blockers(comp)
            return store.once(name,{'status':'FRESH_TEST_PASSED_AWAITING_REVIEW' if not blocked else 'FRESH_TEST_SEALED_FAILED',
                'sealed':True,'passed':not blocked,'frozenId':frozen['id'],'sealedAtUtc':now().isoformat(),
                'comparison':comp,'blockers':blocked,'count':len(rows),'rows':rows,'probabilities':probabilities,
                'testCanBeReopened':False,'firstActivationRequiresManualReview':True})
    return {'status':'ACCUMULATING_FRESH_TEST','count':len(rows),'sealed':False}


def train(store):
    pointer=store.get('dataset.json');frozen=active_frozen(store)
    report={'ok':True,'version':VERSION,'updatedAtUtc':now().isoformat(),'productionAuthorityChanged':False,
            'datasetRowsHash':(pointer or {}).get('rowsHash')}
    if frozen:
        test=evaluate_fresh(store,frozen)
        report.update(test={k:v for k,v in test.items() if k not in ('rows','probabilities')},frozenId=frozen['id'])
        if not test.get('sealed') or test.get('passed'):
            report['status']=test['status'];store.latest('training.json',report);return report
    if not pointer:
        report['status']='WAITING_FOR_DEVELOPMENT_DATA';store.latest('training.json',report);return report
    data=store.load(pointer['artifact']);rows=data['rows']
    report.update(developmentRows=len(rows),originalRows=data['originalRows'],dataset=pointer)
    new_rows=len(rows)-(frozen['developmentRows'] if frozen else 0)
    if frozen and frozen.get('dataset'):
        old_rows=store.load(frozen['dataset']['artifact'])['rows']
        new_rows=len({str(r['officialGamePk']) for r in rows}-{str(r['officialGamePk']) for r in old_rows})
    report['newGamesSincePreviousFreeze']=new_rows
    if frozen and new_rows<models.PROTOCOL['newRowsAfterFailedTest']:
        report['status']='SEALED_RESEARCH_TEST_FAILED_WAITING_FOR_NEW_DATA'
    else:
        identity=digest({'rowsHash':digest(rows),'protocol':models.PROTOCOL})
        result=store.get(f'experiments/{identity}.json')
        if not result:
            result=store.once(f'experiments/{identity}.json',models.research(rows))
        report.update(status=result['status'],experimentId=identity,development=result)
        if result['status']=='READY_FOR_NEW_FUTURE_TEST':
            value={'model':result['model'],'experimentId':identity,'protocol':models.PROTOCOL,'developmentRows':len(rows),
                   'dataset':pointer,'frozenAtUtc':now().isoformat(),
                   'firstProspectiveSlateDate':(now().astimezone(source.ET).date()+timedelta(days=1)).isoformat(),
                   'predecessorFrozenId':frozen['id'] if frozen else None}
            value['id']=digest(value)
            artifact=store.artifact('frozen',value)
            store.latest('active-frozen.json',{'artifact':artifact,'updatedAtUtc':now().isoformat()})
            report.update(status='FROZEN_WAITING_FOR_NEW_FUTURE_TEST',frozenId=value['id'])
    store.latest('training.json',report)
    return report


def health(store):
    result={'version':VERSION,'readOnly':True,'productionAuthorityChanged':False}
    for name,age in (('capture',900),('training',8*3600),('ingestion',3*3600)):
        value=store.get(name+'.json')
        state='NOT_OBSERVED'
        if value:
            elapsed=(now()-utc(value['updatedAtUtc'])).total_seconds()
            state='INVALID_FUTURE_TIME' if elapsed<0 else 'STALE' if elapsed>age else 'FAILED' if value.get('ok') is False else 'PARTIAL' if value.get('status')=='PARTIAL' else 'HEALTHY'
        result[name]={'health':state,'evidence':value}
    return result


def lambda_handler(event,context):
    store=Store();mode=event.get('mode')
    if mode=='status': return health(store)
    if mode not in ('capture','train'): raise ValueError('unsupported research mode')
    owner=store.acquire(mode)
    if not owner: return {'ok':True,'status':'LEASE_BUSY'}
    try:
        if mode=='train': return train(store)
        result=capture(store)
        pointer=store.get('dataset.json'); previous=store.get('training.json')
        # New source data or a new deployment can arrive between six-hour runs.
        # The same canonical capture owner refreshes training evidence once.
        # No second schedule or external trainer is introduced.
        if (pointer and (not previous or previous.get('datasetRowsHash')!=pointer['rowsHash']
                or previous.get('deploymentGitSha')!=os.environ.get('INQSI_DEPLOY_GIT_SHA'))
                and (not previous or (now()-utc(previous['updatedAtUtc'])).total_seconds()>900)
                and context.get_remaining_time_in_millis()>180000):
            training_owner=store.acquire('train')
            if training_owner:
                try: result['newDataTraining']=train(store)['status']
                except Exception as exc:
                    store.latest('training.json',{'ok':False,'status':'FAILED','updatedAtUtc':now().isoformat(),'error':type(exc).__name__})
                    result['newDataTraining']='FAILED'
                finally: store.release('train',training_owner)
        return result
    except Exception as exc:
        store.latest(('capture' if mode=='capture' else 'training')+'.json',{'ok':False,'status':'FAILED',
                     'error':type(exc).__name__,'updatedAtUtc':now().isoformat(),'version':VERSION})
        raise
    finally:
        store.release(mode,owner)
