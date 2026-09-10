"""Named pregame signals; missing sources never become healthy zero values."""
import math
from collections import Counter
from datetime import timedelta
from urllib.parse import urlencode
import mlb_research_sources_v1 as source
import mlb_player_windows_v1 as players
from mlb_research_store_v1 import now, utc


def market_path(observations, cutoff):
    values = sorted((o for o in observations if utc(o['capturedAtUtc']) <= cutoff), key=lambda o:o['capturedAtUtc'])
    if not values:
        return {}
    latest = values[-1]
    # A quote that was fresh when fetched can expire while cached or while
    # collecting other sources. Keep old movement history, but require every
    # book in the current baseline to remain fresh at the snapshot time.
    try:
        ages = [(cutoff-utc(latest['capturedAtUtc'])).total_seconds()]
        books = latest['books']
        ages.extend((cutoff-utc(book['sourceAtUtc'])).total_seconds() for book in books)
        if not books or not all(0 <= age <= source.MAX_MARKET_AGE_SECONDS for age in ages):
            return {}
    except (KeyError, TypeError, ValueError):
        return {}
    p = [o['marketHomeProbability'] for o in values]
    changes = [b-a for a,b in zip(p,p[1:])]
    signs = [1 if x > 0 else -1 for x in changes if abs(x) > 1e-8]
    elapsed = (utc(values[-1]['capturedAtUtc'])-utc(values[0]['capturedAtUtc'])).total_seconds()/3600
    return {'marketHomeProbability':p[-1], 'marketMovement':p[-1]-p[0] if len(p)>1 else None,
            'marketRange':max(p)-min(p) if len(p)>1 else None,
            'marketReversals':sum(a!=b for a,b in zip(signs,signs[1:])) if len(p)>1 else None,
            'marketVelocityPpHr':100*(p[-1]-p[0])/elapsed if elapsed>0 else None,
            'marketObservationCount':len(p)}


def prior_features(game, sources, schedule, cutoff):
    result = {}
    day = cutoff.astimezone(source.ET).date()
    for side in ('home','away'):
        tid = game['teams'][side]['team']['id']
        expected = {g['gamePk'] for g in (schedule or []) if source.playable(g) and g['gamePk'] != game['gamePk']
                    and 1 <= (day-utc(g['gameDate']).astimezone(source.ET).date()).days <= 14
                    and any(t['team']['id'] == tid for t in g['teams'].values())}
        chosen = [g for g in sources if g['officialGamePk'] in expected and utc(g['completedAtUtc']) < cutoff]
        complete = schedule is not None and len(chosen) == len(expected) and {g['officialGamePk'] for g in chosen} == expected
        result[side+'PriorHistoryComplete'] = float(complete)
        batting = Counter(); pitching = Counter(); relief = {n:0 for n in (1,3,5)}
        if complete:
            try:
                for g in chosen:
                    team = next(t for t in g['teams'].values() if t['team']['id']==tid)
                    batting.update({k:source.count(team['teamStats']['batting'][k]) for k in players.BATTING})
                    age = (day-utc(g['startAtUtc']).astimezone(source.ET).date()).days
                    for pid in team['pitchers']:
                        stat = team['players']['ID'+str(pid)]['stats']['pitching']
                        if source.count(stat['gamesStarted']) == 1:
                            pitching.update({k:source.count(stat[k]) for k in players.PITCHING})
                        else:
                            for n in relief:
                                if age<=n: relief[n]+=source.count(stat['numberOfPitches'])
            except (ValueError,KeyError,StopIteration):
                complete = False
                result[side+'PriorHistoryComplete'] = 0.
        pr = players.rates(pitching,'pitching') if complete else {}
        br = players.rates(batting,'hitting') if complete else {}
        result[side+'TeamBattingOps14d'] = br.get('ops')
        result[side+'PriorStartingPitchersEra14d'] = pr.get('era')
        result[side+'PriorStartingPitchersKMinusBbPct14d'] = pr.get('kMinusBbPct')
        for n in relief: result[f'{side}PriorBullpenPitches{n}d'] = relief[n] if complete else None
    for name in ('TeamBattingOps14d','PriorStartingPitchersEra14d','PriorStartingPitchersKMinusBbPct14d','PriorBullpenPitches3d'):
        h,a = result['home'+name],result['away'+name]
        result[name[0].lower()+name[1:]+'GapHome'] = h-a if h is not None and a is not None else None
    # Match archived market/development names exactly.
    result['bullpenPitches3dGapHome'] = result.pop('priorBullpenPitches3dGapHome')
    return result


def statcast_features(observation, bundle, cutoff):
    result = {}
    rows = bundle.get('rows', [])
    for side, team in observation['teams'].items():
        for group, members, role in (('Starter',[p for p in team['players'] if p['probableStarter']], 'pitcher'),
                ('Lineup',[p for p in team['players'] if p['lineupSlot']], 'batter')):
            for n in (7,15,30):
                summaries, complete = [], bool(members)
                for player in members:
                    window = player['pitching' if role=='pitcher' else 'hitting'][str(n)+'d']
                    ids = set(window.get('gameIds', []))
                    selected = [r for r in rows if source.count(r['game_pk']) in ids and str(r[role])==str(player['id'])]
                    expected = (window.get('stats') or {}).get('numberOfPitches' if role=='pitcher' else 'plateAppearances')
                    actual = len(selected) if role=='pitcher' else sum(bool(r.get('events')) for r in selected)
                    if expected is None or actual != expected:
                        complete = False
                    summaries.append(source.statcast_player(selected,player['id'],role))
                result[f'{side}{group}StatcastComplete{n}d'] = float(complete)
                for metric in ('hardHitRate','xwobaOnContact','meanVelocity'):
                    vals = [s[metric] for s in summaries]
                    result[f'{side}{group}{metric[0].upper()+metric[1:]}{n}d'] = sum(vals)/len(vals) if complete and vals and all(v is not None for v in vals) else None
                if role == 'pitcher':
                    mixes = {k for s in summaries for k in s['pitchMix']}
                    for pitch in mixes:
                        result[f'{side}StarterPitchMix{pitch}{n}d'] = sum(s['pitchMix'].get(pitch,0) for s in summaries)/len(summaries) if complete else None
    return result


def conditions(game, feed, prior_sources, cutoff):
    result, receipts, errors = {}, [], []
    roof = feed['gameData'].get('venue',{}).get('fieldInfo',{}).get('roofType')
    weather = feed['gameData'].get('weather',{})
    result['roofOpenObserved'] = 1. if roof == 'Open' or weather.get('condition') == 'Roof Open' else None
    result['roofClosedObserved'] = 1. if roof == 'Dome' or weather.get('condition') == 'Roof Closed' else None
    location = feed['gameData'].get('venue',{}).get('location',{}).get('defaultCoordinates',{})
    completed=[g for g in prior_sources if utc(g['completedAtUtc'])<cutoff]
    venue_id=feed['gameData'].get('venue',{}).get('id')
    totals=[];park=[]
    for prior in completed:
        runs=[source.number(t.get('teamStats',{}).get('batting',{}).get('runs')) for t in prior['teams'].values()]
        if len(runs)==2 and all(v is not None for v in runs):
            totals.append(sum(runs))
            if prior.get('venue',{}).get('id')==venue_id:park.append(sum(runs))
    result['observedVenueRunSample']=len(park)
    result['recentVenueScoringRatio']=(sum(park)/len(park))/(sum(totals)/len(totals)) if len(park)>=5 and sum(totals)>0 else None
    if location:
        try:
            point,r = source.fetch(f"https://api.weather.gov/points/{location['latitude']},{location['longitude']}")
            forecast,r2 = source.fetch(point['properties']['forecastHourly']); receipts.extend([r,r2])
            periods = [p for p in forecast['properties']['periods'] if utc(p['startTime']) <= utc(game['gameDate']) < utc(p['endTime'])]
            if periods and result['roofOpenObserved'] == 1.:
                p=periods[0]
                result['forecastTemperatureF'] = source.number(p['temperature']) if p['temperatureUnit']=='F' else None
                result['forecastPrecipitationPct'] = source.number(p.get('probabilityOfPrecipitation',{}).get('value'))
        except Exception as exc:
            errors.append('weather:'+type(exc).__name__)
    for side in ('home','away'):
        tid=game['teams'][side]['team']['id']
        completed=[g for g in prior_sources if utc(g['completedAtUtc'])<cutoff and any(t['team']['id']==tid for t in g['teams'].values())]
        if completed:
            last=max(completed,key=lambda g:g['completedAtUtc'])
            result[side+'RestHours']=(cutoff-utc(last['completedAtUtc'])).total_seconds()/3600
            previous=last.get('venue',{}).get('location',{}).get('defaultCoordinates',{})
            if previous and location:
                lat1,lat2=map(math.radians,[previous['latitude'],location['latitude']])
                dlon=math.radians(location['longitude']-previous['longitude'])
                arc=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
                result[side+'TravelKm']=6371*2*math.asin(min(1,math.sqrt(arc)))
            margins=[]
            for prior in completed:
                age=(cutoff.astimezone(source.ET).date()-utc(prior['startAtUtc']).astimezone(source.ET).date()).days
                if not 0<=age<14:continue
                teams=list(prior['teams'].values())
                own=next(t for t in teams if t['team']['id']==tid)
                opponent=next(t for t in teams if t['team']['id']!=tid)
                own_runs=source.number(own.get('teamStats',{}).get('batting',{}).get('runs'))
                opp_runs=source.number(opponent.get('teamStats',{}).get('batting',{}).get('runs'))
                if own_runs is not None and opp_runs is not None:margins.append(own_runs-opp_runs)
            result[side+'ObservedRunMargin14d']=sum(margins)/len(margins) if margins else None
        try:
            day=cutoff.astimezone(source.ET).date()
            data,r=source.fetch(source.API+'/v1/transactions?'+urlencode({'teamId':tid,'startDate':str(day-timedelta(days=7)),'endDate':str(day)}))
            receipts.append(r)
            # Counts are transaction activity, not invented injury clearance.
            result[side+'Transactions7d']=len(data['transactions'])
        except Exception as exc:
            errors.append(side+'Transactions:'+type(exc).__name__)
    return {'features':result,'receipts':receipts,'errors':errors}


def interactions(features):
    value=dict(features)
    for side in ('home','away'):
        for a,b in (('StarterEra7d','LineupOps7d'),('BullpenPitches3d','BullpenEra30d'),('StarterEra7vs30d','LineupIso7vs30d')):
            x,y=source.number(value.get(side+a)),source.number(value.get(side+b))
            value[side+a+'Times'+b]=x*y if x is not None and y is not None else None
    return value
