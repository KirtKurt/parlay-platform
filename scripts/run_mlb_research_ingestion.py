"""Autonomous source preparation and original-snapshot settlement; no trainer invocation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import date as calendar_date, timedelta
import json
from pathlib import Path
import sys
import time
from publish_mlb_research_dataset import store_for_stack,publish_historical
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'mlb_research'))
from ks1.features import Features, number
from ks1.statcast_history import (official_pitch_counts, physical_pitch_complete_dates,
                                  pitch_complete_dates, pitches_complete)
from mlb_research_store_v1 import now,utc,digest
from mlb_research_dataset_v1 import publish_dataset
import mlb_research_sources_v1 as source


def cached_statcast(store,value,expected,deadline):
    """Return a date only when its cached game set matches today's final set."""
    if not expected:
        return [],None
    primary=f'sources/statcast-v2/{value}.json'
    cached=store.get(primary)
    actual={source.count(r['game_pk']) for r in cached.get('rows',[])} if cached else set()
    if cached and actual!=expected:
        # A suspended/late-final game can expand the expected set after the
        # immutable daily object was written. Preserve it for audit and use a
        # game-set-addressed revision instead of pretending it is complete.
        key=f'sources/statcast-v2-revisions/{value}/{digest(sorted(expected))}.json'
        cached=store.get(key)
    else:
        key=primary
    if not cached:
        if time.monotonic()>deadline:
            raise TimeoutError('ingestion time budget reached')
        fetched=source.statcast(value)
        # Savant returns every game played on a date, including spring games
        # outside this research contract.  Retain only the independently
        # scheduled eligible IDs, then still fail closed if any are missing.
        fetched={**fetched,'rows':[r for r in fetched['rows']
                                   if source.count(r['game_pk']) in expected]}
        actual={source.count(r['game_pk']) for r in fetched['rows']}
        if actual!=expected:
            return [],{'date':value,'source':'statcast','error':'COVERAGE_MISMATCH',
                       'expectedGameCount':len(expected),'observedGameCount':len(actual),
                       'missingGameIds':sorted(expected-actual),'extraGameIds':sorted(actual-expected)}
        cached=store.once(key,fetched)
    actual={source.count(r['game_pk']) for r in cached.get('rows',[])}
    if actual!=expected:
        return [],{'date':value,'source':'statcast','error':'COVERAGE_MISMATCH',
                   'expectedGameCount':len(expected),'observedGameCount':len(actual),
                   'missingGameIds':sorted(expected-actual),'extraGameIds':sorted(actual-expected)}
    return cached['rows'],None


def discovery_summary(store):
    pointer=store.get('feature-discovery.json')
    if not pointer or not pointer.get('artifact'):
        return {'status':'NOT_OBSERVED','candidateCount':0,'productionAuthorityChanged':False}
    try:
        value=store.load(pointer['artifact'])
    except Exception as exc:
        return {'status':'EVIDENCE_READ_FAILED','error':type(exc).__name__,'candidateCount':0,
                'productionAuthorityChanged':False}
    return {'status':value.get('status'),'candidateCount':len(value.get('candidates',[])),
            'candidateFeatures':[r.get('feature') for r in value.get('candidates',[])[:10]],
            'evaluatedFeatures':value.get('evaluatedFeatures'),'developmentRows':value.get('developmentRows'),
            'holdoutRows':value.get('holdoutRows'),'holdoutLabelsInspectedByScreen':value.get('holdoutLabelsInspectedByScreen'),
            'datasetRowsHash':value.get('datasetRowsHash'),'implementationSha256':value.get('implementationSha256'),
            'productionAuthorityChanged':False}


def _snapshot_problem(value,game):
    """Return bounded non-secret evidence explaining a rejected immutable T10 row."""
    from mlb_research_runtime_v1 import validate_snapshot
    try:
        validate_snapshot(value,game)
        return None
    except Exception as exc:
        return {'gamePk':str(game['gamePk']),'error':type(exc).__name__,'code':str(exc)[:120],
                'snapshotCommenceTime':value.get('commenceTime'),'currentOfficialStart':game.get('gameDate'),
                'snapshotCutoff':value.get('featureCutoffUtc'),'capturedAtUtc':value.get('capturedAtUtc'),
                'snapshotFingerprint':digest(value)}


def prior_year_profiles(sources, rows, prior_year):
    """Build reusable prior-year pitcher physics independently of current-year lag."""
    prior_sources=[g for g in sources
                   if utc(g['startAtUtc']).astimezone(source.ET).date().year==prior_year]
    engine=Features(prior_sources, rows, statcast_complete=True)
    expected_counts,invalid=official_pitch_counts(prior_sources)
    verified_games={pk for pk in expected_counts
                    if pitches_complete(engine.statcast_by_game.get(pk, []),
                                        {pk}, expected_counts, invalid)}
    appearances={}
    for row in engine.rows:
        for player in row['players']:
            if number(player['stats'].get('gamesStarted'))==1:
                appearances.setdefault(player['id'],[]).append((row['game_id'],player['stats']))
    profiles={}
    for pitcher,pairs in appearances.items():
        # Do not aggregate a convenient subset: every appearance in this
        # pitcher's prior year must have complete physical AND PA evidence.
        if not {game_id for game_id,_ in pairs}.issubset(verified_games):
            continue
        expected=sum(number(stats.get('numberOfPitches')) for _,stats in pairs) if all(
            number(stats.get('numberOfPitches')) is not None for _,stats in pairs) else None
        profile=engine.statcast(pitcher,{game_id for game_id,_ in pairs},expected)
        if profile['complete']==1:
            profiles[pitcher]=profile
    return profiles


def ingest(store,seconds=2400):
    started=now();deadline=time.monotonic()+seconds
    owner=store.acquire('ingestion',seconds+120)
    if not owner: return {'ok':True,'status':'LEASE_BUSY'}
    errors=[]
    try:
        if not store.get('historical-input.json'):
            try:
                publish_historical(store)
            except Exception as exc:
                errors.append({'source':'historical','error':type(exc).__name__})
        day=started.astimezone(source.ET).date()
        current_dates={day-timedelta(days=age) for age in range(1,31)}
        current_year_dates={calendar_date(day.year,1,1)+timedelta(days=age)
                            for age in range((day-calendar_date(day.year,1,1)).days)}
        prior_year=day.year-1
        prior_dates={calendar_date(prior_year,1,1)+timedelta(days=age)
                     for age in range((calendar_date(prior_year+1,1,1)-calendar_date(prior_year,1,1)).days)}
        recent_games,recent_receipt=source.schedule(f'{day.year}-01-01',day.isoformat())
        prior_games,prior_receipt=source.schedule(f'{prior_year}-01-01',f'{prior_year}-12-31')
        games=list({g['gamePk']:g for g in [*prior_games,*recent_games]}.values())
        receipt={'requests':[prior_receipt,recent_receipt],
                 'retrievedAtUtc':max(prior_receipt['retrievedAtUtc'],
                                      recent_receipt['retrievedAtUtc']),
                 'uniqueGames':len(games),
                 'scopes':{'priorYear':prior_year,'currentDays':30}}
        completed=[g for g in games if source.final(g)]
        sources=[]
        def prepare(game):
            # v1 omitted fields now required by pitcher-result profiles. Never
            # reinterpret an immutable legacy projection as the expanded one.
            key=f"sources/games-v2/{game['gamePk']}.json"
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
        prior_game_ids={g['gamePk'] for g in prior_games if source.final(g)}
        recent_game_ids={g['gamePk'] for g in completed
                         if utc(g['gameDate']).astimezone(source.ET).date() in current_dates}
        current_year_game_ids={g['gamePk'] for g in recent_games if source.final(g)}
        observed_game_ids={g['officialGamePk'] for g in sources}
        prior={'games':sources,'schedule':games,'receipt':receipt,
               'coverageComplete':len(sources)==len(completed),
               'current30CoverageComplete':recent_game_ids.issubset(observed_game_ids),
               'currentYearCoverageComplete':current_year_game_ids.issubset(observed_game_ids),
               'priorYearCoverageComplete':prior_game_ids.issubset(observed_game_ids),
               'priorYear':prior_year,'updatedAtUtc':now().isoformat()}
        store.latest('prior-games.json',{'artifact':store.artifact('prior-games',prior),'updatedAtUtc':prior['updatedAtUtc']})
        expected_by_date={value.isoformat():set() for value in current_year_dates|prior_dates}
        for game in completed:
            game_day=utc(game['gameDate']).astimezone(source.ET).date().isoformat()
            if game_day in expected_by_date:
                expected_by_date[game_day].add(source.count(game['gamePk']))
        def prepare_statcast(value):
            expected=expected_by_date[value]
            try:
                rows,error=cached_statcast(store,value,expected,deadline)
                return value,rows,error
            except Exception as exc:
                return value,[],{'date':value,'source':'statcast','error':type(exc).__name__}
        statcast_by_date={};complete_dates=set()
        with ThreadPoolExecutor(max_workers=4) as pool:
            for value,rows,error in pool.map(prepare_statcast,sorted(expected_by_date)):
                if error: errors.append(error)
                else: statcast_by_date[value]=rows;complete_dates.add(value)
        current_rows=[row for value in sorted(d.isoformat() for d in current_dates)
                      for row in statcast_by_date.get(value,())]
        current_complete={d.isoformat() for d in current_dates}.issubset(complete_dates)
        current_year_complete={d.isoformat() for d in current_year_dates}.issubset(complete_dates)
        prior_complete={d.isoformat() for d in prior_dates}.issubset(complete_dates)
        prior_profiles={};last_start_rows=[]
        if prior_complete and prior['priorYearCoverageComplete']:
            prior_rows=[row for value in sorted(d.isoformat() for d in prior_dates)
                        for row in statcast_by_date.get(value,())]
            prior_profiles=prior_year_profiles(sources,prior_rows,prior_year)
        if (prior_complete and current_year_complete and prior['priorYearCoverageComplete']
                and prior['currentYearCoverageComplete']):
            all_rows=[row for value in sorted(d.isoformat() for d in prior_dates|current_year_dates)
                      for row in statcast_by_date.get(value,())]
            all_engine=Features(sources,all_rows,statcast_complete=True)
            last_pairs=set()
            appearances={}
            for row in all_engine.rows:
                for player in row['players']:
                    if number(player['stats'].get('gamesStarted'))==1:
                        appearances.setdefault(player['id'],[]).append((row['start'],row['game_id']))
            for pitcher,pairs in appearances.items():
                last_pairs.update((pitcher,game_id) for _,game_id in sorted(pairs,reverse=True)[:3])
            # Retain complete games for last starts, not partial pitcher-only
            # dates. Game proof must never imply whole-calendar-date coverage.
            last_games={game_id for _,game_id in last_pairs}
            last_start_rows=[row for row in all_rows if str(row.get('game_pk')) in last_games]
        retained_rows={}
        for row in [*current_rows,*last_start_rows]:
            identity=tuple(str(row.get(key)) for key in ('game_pk','at_bat_number','pitch_number'))
            retained_rows[identity]=row
        pitch_verified_dates = set(pitch_complete_dates(sources, statcast_by_date, expected_by_date))
        physical_verified_dates = set(physical_pitch_complete_dates(
            sources, statcast_by_date, expected_by_date))
        # Outcome evidence is not safe for player attribution unless the same
        # rows also pass the independent physical/batter identity proof.
        pitch_verified_dates &= physical_verified_dates
        retained_verified_games=sorted({str(row.get('game_pk')) for row in retained_rows.values()
                                       if row.get('game_date') in pitch_verified_dates})
        retained_physical_games=sorted({str(row.get('game_pk')) for row in retained_rows.values()
                                       if row.get('game_date') in physical_verified_dates})
        sc={'rows':current_rows,'coverageComplete':current_complete and current_year_complete and prior_complete,
            'retainedCompleteDates':sorted({d.isoformat() for d in current_dates}&pitch_verified_dates),
            'retainedCompleteGames':retained_verified_games,
            'retainedPhysicalDates':sorted(
                {d.isoformat() for d in current_dates}&physical_verified_dates),
            'retainedPhysicalGames':retained_physical_games,
            'retainedPitchCoverageMethod':'official_box_physical_v1_plus_pa_outcomes_v3',
            'current30CoverageComplete':current_complete,'priorYearCoverageComplete':prior_complete,
            'currentYearCoverageComplete':current_year_complete,
            'priorYear':prior_year,'priorYearProfiles':prior_profiles,
            'updatedAtUtc':now().isoformat()}
        sc['rows']=list(retained_rows.values())
        store.latest('statcast.json',{'artifact':store.artifact('statcast',sc),'updatedAtUtc':sc['updatedAtUtc']})
        index=store.get('original-index.json') or {'slates':{}}
        available=sorted({key.split('/')[1] for key in store.keys('snapshots/') if key.endswith('/T10.json')})
        # Whole-slate settlement remains fail closed. Diagnostic evidence identifies
        # missing/invalid rows but never admits a partial slate into development.
        for date in available:
            if date>=day.isoformat() or date in index['slates']: continue
            try:
                slate,_=source.schedule(date)
                slate=[g for g in slate if source.playable(g)]
                if not slate or not all(source.final(g) for g in slate): continue
                snapshots={str(g['gamePk']):store.get(f"snapshots/{date}/{g['gamePk']}/T10.json") for g in slate}
                missing=sorted(pk for pk,value in snapshots.items() if not value)
                if missing:
                    errors.append({'date':date,'source':'original_settlement','error':'MISSING_T10_SNAPSHOTS',
                                   'scheduledGameCount':len(slate),'missingGameIds':missing})
                    continue
                invalid=[problem for game in slate
                         if (problem:=_snapshot_problem(snapshots[str(game['gamePk'])],game))]
                if invalid:
                    errors.append({'date':date,'source':'original_settlement','error':'INVALID_T10_SNAPSHOTS',
                                   'scheduledGameCount':len(slate),'invalidSnapshots':invalid})
                    continue
                rows=[]
                for game in slate:
                    value=snapshots[str(game['gamePk'])]
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
                'statcastDays':len({d.isoformat() for d in current_dates}&complete_dates),'expectedStatcastDays':30,
                'priorYearStatcastDays':len({d.isoformat() for d in prior_dates}&complete_dates),
                'expectedPriorYearStatcastDays':len(prior_dates),
                'currentYearStatcastDays':len({d.isoformat() for d in current_year_dates}&complete_dates),
                'expectedCurrentYearStatcastDays':len(current_year_dates),
                'retainedPhysicalStatcastDays':len(
                    {d.isoformat() for d in current_dates}&physical_verified_dates),
                'retainedOutcomeStatcastDays':len(
                    {d.isoformat() for d in current_dates}&pitch_verified_dates),
                'retainedStatcastPitches':len(sc['rows']),'priorYearProfiles':len(prior_profiles),
                'datasetRows':len(data['rows']),'originalRows':data['originalRows'],
                'featureDiscovery':discovery_summary(store),'errors':errors,
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
    output=ROOT/'runtime_reports/mlb_research_ingestion_latest.json'
    try:
        result=ingest(store_for_stack(),args.seconds)
    except Exception as exc:
        output.write_text(json.dumps({'ok':False,'status':'FAILED','error':type(exc).__name__,
                                    'updatedAtUtc':now().isoformat()},indent=2)+'\n')
        raise
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
