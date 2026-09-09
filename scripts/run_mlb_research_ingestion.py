"""Autonomous source preparation and original-snapshot settlement; no trainer invocation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import sys
import time
from publish_mlb_research_dataset import store_for_stack,publish_historical
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mlb_research'))
from mlb_research_store_v1 import now,utc,digest
from mlb_research_dataset_v1 import publish_dataset
import mlb_research_sources_v1 as source


def ingest(store,seconds=2400):
    started=now();deadline=time.monotonic()+seconds
    owner=store.acquire('ingestion',seconds+120)
    if not owner: return {'ok':True,'status':'LEASE_BUSY'}
    errors=[]
    try:
        if not store.get('historical-input.json'):
            publish_historical(store)
        day=started.astimezone(source.ET).date()
        first=(day-timedelta(days=30)).isoformat()
        games,receipt=source.schedule(first,day.isoformat())
        completed=[g for g in games if source.final(g)]
        sources=[]
        def prepare(game):
            key=f"sources/games/{game['gamePk']}.json"
            try:
                cached=store.get(key)
                if cached: return cached,None
                if time.monotonic()>deadline: raise TimeoutError('ingestion time budget reached')
                return store.once(key,source.final_source(game)),None
            except Exception as exc: return None,{'gamePk':game['gamePk'],'source':'official','error':type(exc).__name__}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for value,error in pool.map(prepare,completed):
                if error: errors.append(error)
                else: sources.append(value)
        prior={'games':sources,'schedule':games,'receipt':receipt,
               'coverageComplete':len(sources)==len(completed),'updatedAtUtc':now().isoformat()}
        store.latest('prior-games.json',{'artifact':store.artifact('prior-games',prior),'updatedAtUtc':prior['updatedAtUtc']})
        statcasts=[];complete_days=0
        for age in range(1,31):
            date=(day-timedelta(days=age)).isoformat()
            key=f'sources/statcast/{date}.json'
            try:
                cached=store.get(key)
                if not cached:
                    if time.monotonic()>deadline: raise TimeoutError('ingestion time budget reached')
                    expected={g['gamePk'] for g in completed if utc(g['gameDate']).astimezone(source.ET).date().isoformat()==date}
                    value=source.statcast(date) if expected else {'date':date,'rows':[],'receipt':receipt,'noGamesConfirmedByOfficialSchedule':True}
                    actual={source.count(r['game_pk']) for r in value['rows']}
                    if actual!=expected: raise ValueError('incomplete Statcast game coverage')
                    cached=store.once(key,value)
                statcasts.extend(cached['rows']);complete_days+=1
            except Exception as exc: errors.append({'date':date,'source':'statcast','error':type(exc).__name__})
        sc={'rows':statcasts,'coverageComplete':complete_days==30,'updatedAtUtc':now().isoformat()}
        store.latest('statcast.json',{'artifact':store.artifact('statcast',sc),'updatedAtUtc':sc['updatedAtUtc']})
        index=store.get('original-index.json') or {'slates':{}}
        available=sorted({key.split('/')[1] for key in store.keys('snapshots/') if key.endswith('/T10.json')})
        source_map={str(g['officialGamePk']):g for g in sources}
        # Visit all days independently. Missing historical evidence cannot stop
        # collection or settlement of a later complete original slate.
        from mlb_research_runtime_v1 import validate_snapshot
        for date in available:
            if date>=day.isoformat() or date in index['slates']: continue
            try:
                slate,_=source.schedule(date)
                slate=[g for g in slate if source.playable(g)]
                if not slate or not all(source.final(g) for g in slate): continue
                rows=[]
                for game in slate:
                    value=store.get(f"snapshots/{date}/{game['gamePk']}/T10.json")
                    if not value: raise ValueError('incomplete original slate')
                    validate_snapshot(value,game)
                    rows.append({'officialGamePk':value['officialGamePk'],'slateDateEt':date,'features':value['features'],
                        'featureFingerprint':value['featureFingerprint'],'homeWon':int(game['teams']['home']['isWinner']),
                        'homeRuns':source.count(game['teams']['home']['score']),'awayRuns':source.count(game['teams']['away']['score']),
                        'originalObservation':True,'slateComplete':True,'snapshotFingerprint':digest(value)})
                index['slates'][date]=store.artifact('original-slates',{'rows':rows})
            except Exception as exc: errors.append({'date':date,'source':'original_settlement','error':type(exc).__name__})
        index['updatedAtUtc']=now().isoformat();store.latest('original-index.json',index)
        data=publish_dataset(store)
        report={'ok':True,'status':'PARTIAL' if errors else 'COMPLETE','updatedAtUtc':now().isoformat(),
                'startedAtUtc':started.isoformat(),'priorGames':len(sources),'expectedPriorGames':len(completed),
                'statcastDays':complete_days,'expectedStatcastDays':30,'statcastPitches':len(statcasts),
                'datasetRows':len(data['rows']),'originalRows':data['originalRows'],'errors':errors,
                'productionAuthorityChanged':False}
        store.latest('ingestion.json',report)
        return report
    except Exception as exc:
        store.latest('ingestion.json',{'ok':False,'status':'FAILED','updatedAtUtc':now().isoformat(),'error':type(exc).__name__})
        raise
    finally: store.release('ingestion',owner)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=2400);args=parser.parse_args()
    if not 60<=args.seconds<=2400: raise ValueError('source budget must be 60..2400 seconds')
    result=ingest(store_for_stack(),args.seconds)
    (ROOT/'runtime_reports/mlb_research_ingestion_latest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
