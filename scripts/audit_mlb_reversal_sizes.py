#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, os, re, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'hello_world'))
import inqsi_pull_history as history

BOOKS=history.BOOKS

def dt(v):
    try:
        x=datetime.fromisoformat(str(v).replace('Z','+00:00')); return (x if x.tzinfo else x.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:return None

def drange(a,b):
    x=date.fromisoformat(a); z=date.fromisoformat(b)
    while x<=z: yield x.isoformat(); x+=timedelta(days=1)

def norm(v): return re.sub(r'[^a-z0-9]+',' ',str(v or '').lower()).strip()
def gid(g): return str(g.get('official_game_pk') or g.get('officialGamePk') or g.get('game_id') or g.get('gameId') or g.get('game_key') or g.get('id') or f"{norm(g.get('away_team'))}@{norm(g.get('home_team'))}:{g.get('commence_time')}")

def american_prob(x):
    try:x=float(x)
    except:return None
    if x==0:return None
    return (-x)/((-x)+100.0) if x<0 else 100.0/(x+100.0)

def book_probs(g):
    out={}
    for b,p in (g.get('books') or {}).items():
        if not isinstance(p,dict):continue
        ml=p.get('ml') or p.get('moneyline') or {}
        h=ml.get('home'); a=ml.get('away')
        hp=american_prob(h); ap=american_prob(a)
        if hp is None or ap is None:continue
        s=hp+ap
        if s>0: out[str(b).lower()]={'home':hp/s,'away':ap/s}
    return out

def finals(a,b):
    q=urllib.parse.urlencode({'sportId':1,'startDate':a,'endDate':b,'hydrate':'team,linescore'})
    req=urllib.request.Request('https://statsapi.mlb.com/api/v1/schedule?'+q,headers={'user-agent':'inqsi-reversal-audit/1.0'})
    with urllib.request.urlopen(req,timeout=60) as r:p=json.load(r)
    out={}
    for d in p.get('dates') or []:
        for g in d.get('games') or []:
            if str((g.get('status') or {}).get('abstractGameState')).lower()!='final':continue
            h=(g.get('teams') or {}).get('home') or {}; a1=(g.get('teams') or {}).get('away') or {}
            try: hs=int(h.get('score')); aw=int(a1.get('score'))
            except:continue
            out[str(g.get('gamePk'))]={'winnerSide':'home' if hs>aw else 'away','homeTeam':((h.get('team') or {}).get('name')),'awayTeam':((a1.get('team') or {}).get('name'))}
    return out

def legs(values, threshold=.0005):
    moves=[]
    for i in range(1,len(values)):
        d=values[i][1]-values[i-1][1]
        s=1 if d>threshold else -1 if d<-threshold else 0
        if s:moves.append((i,s,d))
    if not moves:return []
    seg=[]; start_i=moves[0][0]-1; sign=moves[0][1]
    for pos,s,_ in moves[1:]:
        if s!=sign:
            end=pos-1; seg.append((start_i,end,sign,values[end][1]-values[start_i][1])); start_i=end; sign=s
    end=moves[-1][0]; seg.append((start_i,end,sign,values[end][1]-values[start_i][1]))
    rev=[]
    for j in range(1,len(seg)):
        prev=seg[j-1]; cur=seg[j]
        rev.append({'turnIndex':cur[0],'previousLegPp':round(abs(prev[3])*100,4),'reversalLegPp':round(abs(cur[3])*100,4),'direction':cur[2]})
    return rev

def bucket(x):
    bins=[.1,.25,.5,.75,1,1.5,2,3,5,999]
    lo=0
    for hi in bins:
        if x<hi:return f'{lo:.2f}-{hi:.2f}pp'
        lo=hi

def run(a,b):
    outcomes=finals(a,b); games={}; pulls_seen=0
    for slate in drange(a,b):
        pulls=history.query_pulls('mlb',slate,500); pulls_seen+=len(pulls)
        for p in pulls:
            t=dt(p.get('pulled_at'))
            if not t:continue
            for g in p.get('games') or []:
                k=gid(g); rec=games.setdefault(k,{'gameId':k,'slate':slate,'homeTeam':g.get('home_team'),'awayTeam':g.get('away_team'),'commenceTime':g.get('commence_time'),'points':[]})
                cp=history.book_probs(g); bp=book_probs(g)
                if cp: rec['points'].append({'t':t,'consensus':float(cp.get('home')),'books':bp})
    events=[]
    for k,g in games.items():
        pts=sorted(g['points'],key=lambda x:x['t']); start=dt(g.get('commenceTime'))
        if start: pts=[x for x in pts if x['t']<=start-timedelta(minutes=45)]
        vals=[(x['t'],x['consensus']) for x in pts if x.get('consensus') is not None]
        crev=legs(vals)
        for r in crev:
            idx=r['turnIndex']; t=vals[idx][0]; before=pts[max(0,idx-1)].get('books',{}); after=pts[idx].get('books',{})
            dirs=[]; amps=[]
            for bk in set(before)&set(after):
                d=after[bk]['home']-before[bk]['home']
                if abs(d)>.0005: dirs.append(1 if d>0 else -1); amps.append(abs(d)*100)
            breadth=(sum(1 for d in dirs if d==r['direction'])/len(dirs)) if dirs else 0
            r.update({'gameId':k,'slate':g['slate'],'homeTeam':g['homeTeam'],'awayTeam':g['awayTeam'],'time':t.isoformat(),'minutesToStart':round((start-t).total_seconds()/60,1) if start else None,'bookMoverCount':len(dirs),'sameDirectionBookCount':sum(1 for d in dirs if d==r['direction']),'bookBreadthPct':round(breadth*100,2),'medianBookMovePp':round(sorted(amps)[len(amps)//2],4) if amps else None,'winnerSide':(outcomes.get(str(k)) or {}).get('winnerSide')})
            r['towardWinner']= (r['direction']==1 and r['winnerSide']=='home') or (r['direction']==-1 and r['winnerSide']=='away') if r['winnerSide'] else None
            events.append(r)
    sizes=[e['reversalLegPp'] for e in events]
    exact=Counter(round(x,2) for x in sizes)
    bands=defaultdict(list)
    for e in events:bands[bucket(e['reversalLegPp'])].append(e)
    summary=[]
    for name,rows in sorted(bands.items(),key=lambda kv: float(kv[0].split('-')[0])):
        graded=[r for r in rows if r['towardWinner'] is not None]
        summary.append({'band':name,'count':len(rows),'games':len(set(r['gameId'] for r in rows)),'broad60PctCount':sum((r['bookBreadthPct'] or 0)>=60 for r in rows),'towardWinnerPct':round(100*sum(bool(r['towardWinner']) for r in graded)/len(graded),2) if graded else None,'medianMinutesToStart':sorted([r['minutesToStart'] for r in rows if r['minutesToStart'] is not None])[len([r for r in rows if r['minutesToStart'] is not None])//2] if any(r['minutesToStart'] is not None for r in rows) else None})
    broad=[e for e in events if e['bookMoverCount']>=3 and e['bookBreadthPct']>=60]
    out={'version':'MLB-REVERSAL-SIZE-AUDIT-v1','readOnly':True,'dateRange':[a,b],'canonicalPullCount':pulls_seen,'recordedGameCount':len(games),'gamesWithReversals':len(set(e['gameId'] for e in events)),'reversalEventCount':len(events),'sizeStatsPp':{'median':round(sorted(sizes)[len(sizes)//2],4) if sizes else None,'p75':round(sorted(sizes)[int(.75*(len(sizes)-1))],4) if sizes else None,'p90':round(sorted(sizes)[int(.9*(len(sizes)-1))],4) if sizes else None},'sizeBands':summary,'repeatedExactSizes':[{'sizePp':x,'count':n} for x,n in exact.most_common(20) if n>1],'broadMultiBook':{'count':len(broad),'towardWinnerPct':round(100*sum(bool(e['towardWinner']) for e in broad if e['towardWinner'] is not None)/max(1,sum(e['towardWinner'] is not None for e in broad)),2)},'largest':sorted(events,key=lambda e:e['reversalLegPp'],reverse=True)[:30],'events':events}
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--start-date',required=True);p.add_argument('--end-date',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    out=run(a.start_date,a.end_date);Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2,default=str));print(json.dumps({k:out[k] for k in ('recordedGameCount','gamesWithReversals','reversalEventCount','sizeStatsPp','broadMultiBook')},indent=2))
if __name__=='__main__':main()
