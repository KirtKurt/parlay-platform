#!/usr/bin/env python3
"""Full-cohort wrapper for the MLB reversal-size audit.

Unions every game seen in every canonical pull so early completed games are not
lost when the provider removes them from later pulls. Read-only.
"""
from __future__ import annotations
import argparse, json, statistics, urllib.parse, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import audit_mlb_reversal_sizes as base
import boto3


def ids(game):
    values=set()
    for key in ('provider_event_id','providerEventId','official_game_pk','officialGamePk','game_id','gameId','id'):
        value=game.get(key)
        if value not in (None,''):
            values.add(str(value).replace('provider:','',1))
    return values


def stable_id(game):
    for key in ('provider_event_id','providerEventId','official_game_pk','officialGamePk','game_id','gameId','id','game_key','gameKey'):
        value=game.get(key)
        if value not in (None,''):
            return str(value).replace('provider:','',1)
    return base.ident(game)


def same_game(left,right):
    if ids(left) & ids(right):
        return True
    lh,la=base.teams(left); rh,ra=base.teams(right)
    return base.norm(lh)==base.norm(rh) and base.norm(la)==base.norm(ra) and base.start(left)==base.start(right)


def targets_from_all_pulls(pulls):
    targets={}
    aliases=[]
    for pull in pulls:
        for game in pull.get('games') or []:
            if not isinstance(game,dict):
                continue
            match=None
            current_ids=ids(game)
            for known_key,known_ids in aliases:
                if current_ids and current_ids & known_ids:
                    match=known_key
                    known_ids.update(current_ids)
                    break
            key=match or stable_id(game)
            if match is None:
                aliases.append((key,set(current_ids)))
            targets[key]=game
    return list(targets.values())


def series(pulls,target,cutoff):
    consensus=[]; by_book=defaultdict(list)
    for pull in pulls:
        at=base.dt(pull.get('pulled_at'))
        if at is None or at>cutoff:
            continue
        matched=next((game for game in (pull.get('games') or []) if isinstance(game,dict) and same_game(game,target)),None)
        if not matched:
            continue
        book_probs=base.probs(matched)
        if not book_probs:
            continue
        consensus.append((at,sum(row['home'] for row in book_probs.values())/len(book_probs)))
        for book,row in book_probs.items():
            by_book[book].append((at,row['home']))
    def unique(rows):
        seen={}
        for at,value in rows:
            seen.setdefault(at,value)
        return [(at,seen[at]) for at in sorted(seen)]
    return unique(consensus),{book:unique(rows) for book,rows in by_book.items()}


def official_finals(slate):
    url='https://statsapi.mlb.com/api/v1/schedule?'+urllib.parse.urlencode({'sportId':1,'date':slate,'hydrate':'team,linescore'})
    request=urllib.request.Request(url,headers={'accept':'application/json','user-agent':'inqsi-reversal-audit-v2/1.0'})
    with urllib.request.urlopen(request,timeout=45) as response:
        payload=json.loads(response.read().decode())
    rows=[]; by_pk={}
    for date_row in payload.get('dates') or []:
        for game in date_row.get('games') or []:
            teams=game.get('teams') or {}; home=teams.get('home') or {}; away=teams.get('away') or {}
            state=str((game.get('status') or {}).get('abstractGameState') or '').lower(); winner=None
            try:
                if state=='final' and int(home.get('score'))!=int(away.get('score')):
                    winner=((home if int(home['score'])>int(away['score']) else away).get('team') or {}).get('name')
            except Exception:
                pass
            row={'gamePk':str(game.get('gamePk') or ''),'home':(home.get('team') or {}).get('name'),'away':(away.get('team') or {}).get('name'),'start':game.get('gameDate'),'winner':winner}
            rows.append(row)
            if row['gamePk']:
                by_pk[row['gamePk']]=row
    return {'rows':rows,'byPk':by_pk}


def final_for(target,finals):
    pk=str(target.get('official_game_pk') or target.get('officialGamePk') or '')
    if pk and pk in finals['byPk']:
        return finals['byPk'][pk]
    home,away=base.teams(target); start=base.start(target); candidates=[]
    for row in finals['rows']:
        if base.norm(row.get('home'))==base.norm(home) and base.norm(row.get('away'))==base.norm(away):
            row_start=base.dt(row.get('start'))
            distance=abs((row_start-start).total_seconds()) if row_start and start else 10**12
            candidates.append((distance,row))
    return min(candidates,key=lambda item:item[0])[1] if candidates else {}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--start-date',default='2026-06-28')
    parser.add_argument('--end-date',default='2026-07-22')
    parser.add_argument('--table',default='parlay_platform_snapshots')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    table=boto3.resource('dynamodb').Table(args.table)
    day=date.fromisoformat(args.start_date); end=date.fromisoformat(args.end_date)
    consensus_events=[]; book_events=[]; game_rows=[]; diagnostics=[]

    while day<=end:
        slate=day.isoformat(); pulls=base.query(table,slate); targets=targets_from_all_pulls(pulls)
        try:
            final_rows=official_finals(slate); final_error=None
        except Exception as exc:
            final_rows={'rows':[],'byPk':{}}; final_error=f'{type(exc).__name__}:{exc}'
        processed=0
        for target in targets:
            game_start=base.start(target)
            if game_start is None:
                continue
            consensus,per_book=series(pulls,target,game_start-timedelta(minutes=45))
            if len(consensus)<2:
                continue
            processed+=1
            home,away=base.teams(target); final=final_for(target,final_rows); winner=final.get('winner')
            win_direction=base.winner_dir(home,away,winner); game_key=str(target.get('official_game_pk') or target.get('officialGamePk') or stable_id(target)); matchup=f'{away} @ {home}'
            game_book_events=[]
            for book,points in per_book.items():
                legs=base.legs(points)
                for index,leg in enumerate(legs[1:],1):
                    previous=legs[index-1]
                    event={'level':'book','book':book,'slateDateEt':slate,'weekday':day.strftime('%A'),'gameKey':game_key,'matchup':matchup,'winner':winner,'startAtUtc':leg['start'].isoformat(),'endAtUtc':leg['end'].isoformat(),'direction':'home' if leg['dir']>0 else 'away','directionSign':leg['dir'],'amplitudePp':round(leg['amp'],4),'priorLegPp':round(previous['amp'],4),'recoveryRatio':round(leg['amp']/previous['amp'],4) if previous['amp'] else None,'minutesBeforeGame':round((game_start-leg['start']).total_seconds()/60,2),'marketFlip':min(leg['v0'],leg['v1'])<.5<max(leg['v0'],leg['v1']),'towardWinner':leg['dir']==win_direction if win_direction else None}
                    game_book_events.append(event); book_events.append(event)
            consensus_legs=base.legs(consensus)
            for index,leg in enumerate(consensus_legs[1:],1):
                previous=consensus_legs[index-1]
                event={'level':'consensus','slateDateEt':slate,'weekday':day.strftime('%A'),'gameKey':game_key,'matchup':matchup,'winner':winner,'startAtUtc':leg['start'].isoformat(),'endAtUtc':leg['end'].isoformat(),'direction':'home' if leg['dir']>0 else 'away','directionSign':leg['dir'],'amplitudePp':round(leg['amp'],4),'priorLegPp':round(previous['amp'],4),'recoveryRatio':round(leg['amp']/previous['amp'],4) if previous['amp'] else None,'minutesBeforeGame':round((game_start-leg['start']).total_seconds()/60,2),'marketFlip':min(leg['v0'],leg['v1'])<.5<max(leg['v0'],leg['v1']),'towardWinner':leg['dir']==win_direction if win_direction else None}
                candidates=[row for row in game_book_events if row['directionSign']==event['directionSign'] and abs((base.dt(row['startAtUtc'])-leg['start']).total_seconds())<=base.SYNC_MIN*60]
                closest={}
                for row in candidates:
                    distance=abs((base.dt(row['startAtUtc'])-leg['start']).total_seconds()); book=row['book']
                    if book not in closest or distance<closest[book][0]:
                        closest[book]=(distance,row)
                matched=[row for _,row in closest.values()]; amplitudes=[row['amplitudePp'] for row in matched]
                event.update({'eligibleBooks':len(per_book),'matchedBooks':len(matched),'breadthPct':base.pct(len(matched),len(per_book)),'books':sorted(closest),'bookMedianPp':round(statistics.median(amplitudes),3) if amplitudes else None,'bookRangePp':round(max(amplitudes)-min(amplitudes),3) if len(amplitudes)>1 else 0 if amplitudes else None,'tightSameSize':bool(len(amplitudes)>=4 and max(amplitudes)-min(amplitudes)<=.5),'matchedBookEvents':matched})
                consensus_events.append(event)
            game_rows.append({'slateDateEt':slate,'weekday':day.strftime('%A'),'gameKey':game_key,'matchup':matchup,'winner':winner,'pullsBeforeT45':len(consensus),'books':len(per_book),'consensusReversals':max(0,len(consensus_legs)-1),'bookReversals':len(game_book_events)})
        diagnostics.append({'slateDateEt':slate,'pulls':len(pulls),'unionGameCount':len(targets),'processedGames':processed,'officialGames':len(final_rows['rows']),'error':final_error})
        day+=timedelta(days=1)

    meaningful_consensus=[row for row in consensus_events if row['amplitudePp']>=base.MEANINGFUL_PP]
    meaningful_books=[row for row in book_events if row['amplitudePp']>=base.MEANINGFUL_PP]
    repeated=defaultdict(list)
    for row in meaningful_consensus:
        repeated[round(row['amplitudePp']*4)/4].append(row)
    repeated_sizes=[]
    for size,rows in repeated.items():
        if len(rows)<3 or len({row['gameKey'] for row in rows})<2:
            continue
        repeated_sizes.append({'roundedSizePp':size,'events':len(rows),'games':len({row['gameKey'] for row in rows}),'slates':len({row['slateDateEt'] for row in rows}),'towardWinnerPct':base.cond(rows)['towardWinnerPct'],'examples':[{'date':row['slateDateEt'],'matchup':row['matchup'],'amplitudePp':row['amplitudePp'],'direction':row['direction']} for row in rows[:8]]})
    repeated_sizes.sort(key=lambda row:(-row['events'],row['roundedSizePp']))
    synchronized=[row for row in meaningful_consensus if row['matchedBooks']>=3]
    tight=[row for row in synchronized if row['tightSameSize']]
    output={'ok':True,'version':'MLB-REVERSAL-SIZE-AUDIT-v2-full-union','readOnly':True,'createdAtUtc':datetime.now(timezone.utc).isoformat(),'scope':{'startDate':args.start_date,'endDate':args.end_date,'cutoffMinutes':45,'stepThresholdPp':base.EPS_PP,'meaningfulPp':base.MEANINGFUL_PP,'sizeBinPp':.25,'syncWindowMinutes':base.SYNC_MIN,'gameRoster':'union_of_every_game_seen_in_every_canonical_pull'},'summary':{'games':len(game_rows),'gamesAnyConsensusReversal':len({row['gameKey'] for row in consensus_events}),'gamesMeaningfulConsensusReversal':len({row['gameKey'] for row in meaningful_consensus}),'consensusEvents':len(consensus_events),'meaningfulConsensusEvents':len(meaningful_consensus),'bookEvents':len(book_events),'meaningfulBookEvents':len(meaningful_books),'synchronizedAtLeast3Books':len(synchronized),'tightSameSizeAtLeast4Books':len(tight),'mondayGames':len({row['gameKey'] for row in game_rows if row['weekday']=='Monday'}),'mondayMeaningfulEvents':len([row for row in meaningful_consensus if row['weekday']=='Monday'])},'consensusBands':base.summarize(meaningful_consensus),'bookBands':base.summarize(meaningful_books),'repeatedConsensusQuarterPointSizes':repeated_sizes[:75],'patterns':{'all':base.cond(meaningful_consensus),'breadth50':base.cond([row for row in meaningful_consensus if (row['breadthPct'] or 0)>=50]),'breadth75':base.cond([row for row in meaningful_consensus if (row['breadthPct'] or 0)>=75]),'tightSameSize':base.cond(tight),'recoveryAtLeast1':base.cond([row for row in meaningful_consensus if (row['recoveryRatio'] or 0)>=1]),'recoveryBelow1':base.cond([row for row in meaningful_consensus if row['recoveryRatio'] is not None and row['recoveryRatio']<1]),'late60':base.cond([row for row in meaningful_consensus if 0<=row['minutesBeforeGame']<=60]),'marketFlip':base.cond([row for row in meaningful_consensus if row['marketFlip']]),'mondays':base.cond([row for row in meaningful_consensus if row['weekday']=='Monday'])},'topSynchronizedEvents':sorted(synchronized,key=lambda row:(-row['matchedBooks'],row['bookRangePp'] if row['bookRangePp'] is not None else 999,-row['amplitudePp']))[:100],'tightSameSizeEvents':sorted(tight,key=lambda row:(-row['matchedBooks'],row['bookRangePp']))[:100],'games':game_rows,'consensusEvents':consensus_events,'bookEvents':book_events,'dateDiagnostics':diagnostics}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True);Path(args.output).write_text(json.dumps(output,indent=2,sort_keys=True)+'\n')
    print(json.dumps(output['summary'],indent=2));return 0


if __name__=='__main__':
    raise SystemExit(main())
