#!/usr/bin/env python3
"""Read-only MLB moneyline reversal-size and multi-book synchrony audit."""
from __future__ import annotations
import argparse, json, math, os, statistics, urllib.parse, urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence, Tuple
import boto3
from boto3.dynamodb.conditions import Key

EPS_PP = 0.05
MEANINGFUL_PP = 0.30
SYNC_MIN = 30


def dt(v):
    try:
        x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        return (x if x.tzinfo else x.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception: return None

def plain(v):
    if isinstance(v,Decimal): return int(v) if v==v.to_integral_value() else float(v)
    if isinstance(v,dict): return {str(k):plain(x) for k,x in v.items()}
    if isinstance(v,list): return [plain(x) for x in v]
    return v

def ap(v):
    try: a=float(v)
    except Exception: return None
    if not a or not math.isfinite(a): return None
    return abs(a)/(abs(a)+100) if a<0 else 100/(a+100)

def dv(h,a):
    h,a=ap(h),ap(a)
    return None if h is None or a is None or h+a<=0 else (h/(h+a),a/(h+a))

def norm(v): return ' '.join(str(v or '').lower().strip().split())

def ident(g):
    for k in ('official_game_pk','officialGamePk','provider_event_id','providerEventId','game_id','gameId','id','game_key','gameKey'):
        if g.get(k) not in (None,''): return str(g[k]).replace('provider:','',1)
    return f"{norm(g.get('away_team') or g.get('awayTeam'))}|{norm(g.get('home_team') or g.get('homeTeam'))}|{g.get('commence_time') or g.get('commenceTime')}"

def teams(g): return str(g.get('home_team') or g.get('homeTeam') or ''), str(g.get('away_team') or g.get('awayTeam') or '')
def start(g): return dt(g.get('commence_time') or g.get('commenceTime'))

def probs(g):
    books=g.get('books') or g.get('bookmakers') or {}
    if isinstance(books,list): books={str(x.get('key') or x.get('title') or '').lower():x for x in books if isinstance(x,dict)}
    out={}
    for b,p in (books or {}).items():
        if not isinstance(p,dict): continue
        ml=p.get('ml') or p.get('moneyline') or p.get('h2h') or {}
        pair=dv(ml.get('home',ml.get('home_price')),ml.get('away',ml.get('away_price')))
        if pair: out[str(b).lower()]={'home':pair[0],'away':pair[1]}
    return out

def query(table,slate):
    rows=[]; last=None
    while True:
        q={'KeyConditionExpression':Key('PK').eq(f'PULLS#mlb#{slate}'),'ConsistentRead':True}
        if last:q['ExclusiveStartKey']=last
        r=table.query(**q)
        rows += [plain(x.get('data') or {}) for x in r.get('Items',[]) if x.get('record_type')=='pull_run']
        last=r.get('LastEvaluatedKey')
        if not last:break
    rows.sort(key=lambda x:(dt(x.get('pulled_at')) or datetime.min.replace(tzinfo=timezone.utc),str(x.get('pull_id') or '')))
    slots={}
    for x in rows:
        t=dt(x.get('pulled_at'))
        if t: slots.setdefault(t.replace(minute=t.minute//15*15,second=0,microsecond=0),x)
    return [slots[k] for k in sorted(slots)]

def finals(slate):
    url='https://statsapi.mlb.com/api/v1/schedule?'+urllib.parse.urlencode({'sportId':1,'date':slate,'hydrate':'team,linescore'})
    req=urllib.request.Request(url,headers={'accept':'application/json','user-agent':'inqsi-reversal-audit/1.0'})
    with urllib.request.urlopen(req,timeout=45) as r: p=json.loads(r.read().decode())
    out={}
    for d in p.get('dates',[]):
        for g in d.get('games',[]):
            pk=str(g.get('gamePk') or ''); ts=g.get('teams') or {}; h=ts.get('home') or {}; a=ts.get('away') or {}
            state=str((g.get('status') or {}).get('abstractGameState') or '').lower(); win=None
            try:
                if state=='final' and int(h.get('score'))!=int(a.get('score')): win=((h if int(h['score'])>int(a['score']) else a).get('team') or {}).get('name')
            except Exception: pass
            if pk: out[pk]={'winner':win,'home':(h.get('team') or {}).get('name'),'away':(a.get('team') or {}).get('name')}
    return out

def legs(points:Sequence[Tuple[datetime,float]]):
    if len(points)<2:return []
    dirs=[]
    for (_,x),(_,y) in zip(points,points[1:]):
        q=(y-x)*100; dirs.append(1 if q>EPS_PP else -1 if q<-EPS_PP else 0)
    out=[]; cur=0; begin=0
    for i,s in enumerate(dirs):
        if not s:continue
        if not cur:cur=s;begin=i;continue
        if s==cur:continue
        a,b=points[begin],points[i]; out.append({'dir':cur,'start':a[0],'end':b[0],'v0':a[1],'v1':b[1],'amp':abs(b[1]-a[1])*100})
        cur=s;begin=i
    if cur:
        a,b=points[begin],points[-1]; out.append({'dir':cur,'start':a[0],'end':b[0],'v0':a[1],'v1':b[1],'amp':abs(b[1]-a[1])*100})
    return out

def series(pulls,target,cutoff):
    tid=ident(target); th,ta=teams(target); cons=[]; by=defaultdict(list)
    for p in pulls:
        t=dt(p.get('pulled_at'))
        if not t or t>cutoff:continue
        hit=None
        for g in p.get('games') or []:
            if not isinstance(g,dict):continue
            gh,ga=teams(g)
            if ident(g)==tid or (norm(gh)==norm(th) and norm(ga)==norm(ta) and start(g)==start(target)):hit=g;break
        if not hit:continue
        bp=probs(hit)
        if not bp:continue
        cons.append((t,sum(x['home'] for x in bp.values())/len(bp)))
        for b,x in bp.items():by[b].append((t,x['home']))
    def uniq(xs):
        z={};[z.setdefault(t,v) for t,v in xs];return [(t,z[t]) for t in sorted(z)]
    return uniq(cons),{b:uniq(x) for b,x in by.items()}

def winner_dir(h,a,w):
    if not w:return None
    return 1 if norm(w)==norm(h) else -1 if norm(w)==norm(a) else None

def band(x):
    for hi,n in ((.5,'0.30-0.49'),(1,'0.50-0.99'),(1.5,'1.00-1.49'),(2,'1.50-1.99'),(3,'2.00-2.99'),(5,'3.00-4.99')):
        if x<hi:return n
    return '5.00+'

def pct(a,b):return round(a/b*100,2) if b else None

def summarize(rows):
    out=[]
    for name in ('0.30-0.49','0.50-0.99','1.00-1.49','1.50-1.99','2.00-2.99','3.00-4.99','5.00+'):
        x=[r for r in rows if band(r['amplitudePp'])==name]
        if not x:continue
        g=[r for r in x if r['towardWinner'] is not None]; c=sum(r['towardWinner'] is True for r in g)
        out.append({'bandPp':name,'events':len(x),'games':len({r['gameKey'] for r in x}),'medianPp':round(statistics.median(r['amplitudePp'] for r in x),3),'towardWinnerPct':pct(c,len(g)),'graded':len(g)})
    return out

def cond(rows):
    g=[r for r in rows if r['towardWinner'] is not None];c=sum(r['towardWinner'] is True for r in g)
    return {'events':len(rows),'games':len({r['gameKey'] for r in rows}),'medianPp':round(statistics.median([r['amplitudePp'] for r in rows]),3) if rows else None,'towardWinnerPct':pct(c,len(g)),'graded':len(g)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--start-date',default='2026-06-28');p.add_argument('--end-date',default='2026-07-22');p.add_argument('--table',default=os.getenv('SNAPSHOTS_TABLE','parlay_platform_snapshots'));p.add_argument('--output',required=True);a=p.parse_args()
    table=boto3.resource('dynamodb').Table(a.table); day=date.fromisoformat(a.start_date); end=date.fromisoformat(a.end_date)
    ce=[];be=[];games=[];diag=[]
    while day<=end:
        slate=day.isoformat(); pulls=query(table,slate)
        try: fs=finals(slate); ferr=None
        except Exception as e: fs={};ferr=f'{type(e).__name__}:{e}'
        latest=next(([g for g in p0.get('games',[]) if isinstance(g,dict)] for p0 in reversed(pulls) if p0.get('games')),[]); seen=set();done=0
        for target in latest:
            gid=ident(target)
            if gid in seen:continue
            seen.add(gid); st=start(target)
            if not st:continue
            cs,bs=series(pulls,target,st-timedelta(minutes=45))
            if len(cs)<2:continue
            done+=1;h,aw=teams(target);pk=str(target.get('official_game_pk') or target.get('officialGamePk') or '');w=(fs.get(pk) or {}).get('winner');wd=winner_dir(h,aw,w);key=pk or gid;match=f'{aw} @ {h}'
            bel=[]
            for book,pts in bs.items():
                ll=legs(pts)
                for i,l in enumerate(ll[1:],1):
                    pr=ll[i-1];r={'level':'book','book':book,'slateDateEt':slate,'weekday':day.strftime('%A'),'gameKey':key,'matchup':match,'winner':w,'startAtUtc':l['start'].isoformat(),'endAtUtc':l['end'].isoformat(),'direction':'home' if l['dir']>0 else 'away','directionSign':l['dir'],'amplitudePp':round(l['amp'],4),'priorLegPp':round(pr['amp'],4),'recoveryRatio':round(l['amp']/pr['amp'],4) if pr['amp'] else None,'minutesBeforeGame':round((st-l['start']).total_seconds()/60,2),'marketFlip':min(l['v0'],l['v1'])<.5<max(l['v0'],l['v1']),'towardWinner':l['dir']==wd if wd else None}
                    bel.append(r);be.append(r)
            cl=legs(cs)
            for i,l in enumerate(cl[1:],1):
                pr=cl[i-1];r={'level':'consensus','slateDateEt':slate,'weekday':day.strftime('%A'),'gameKey':key,'matchup':match,'winner':w,'startAtUtc':l['start'].isoformat(),'endAtUtc':l['end'].isoformat(),'direction':'home' if l['dir']>0 else 'away','directionSign':l['dir'],'amplitudePp':round(l['amp'],4),'priorLegPp':round(pr['amp'],4),'recoveryRatio':round(l['amp']/pr['amp'],4) if pr['amp'] else None,'minutesBeforeGame':round((st-l['start']).total_seconds()/60,2),'marketFlip':min(l['v0'],l['v1'])<.5<max(l['v0'],l['v1']),'towardWinner':l['dir']==wd if wd else None}
                cand=[]
                for x in bel:
                    if x['directionSign']==r['directionSign'] and abs((dt(x['startAtUtc'])-l['start']).total_seconds())<=SYNC_MIN*60:cand.append(x)
                closest={}
                for x in cand:
                    dist=abs((dt(x['startAtUtc'])-l['start']).total_seconds());b=x['book']
                    if b not in closest or dist<closest[b][0]:closest[b]=(dist,x)
                m=[x for _,x in closest.values()];amps=[x['amplitudePp'] for x in m]
                r.update({'eligibleBooks':len(bs),'matchedBooks':len(m),'breadthPct':pct(len(m),len(bs)),'books':sorted(closest),'bookMedianPp':round(statistics.median(amps),3) if amps else None,'bookRangePp':round(max(amps)-min(amps),3) if len(amps)>1 else 0 if amps else None,'tightSameSize':bool(len(amps)>=4 and max(amps)-min(amps)<=.5),'matchedBookEvents':m})
                ce.append(r)
            games.append({'slateDateEt':slate,'weekday':day.strftime('%A'),'gameKey':key,'matchup':match,'winner':w,'pullsBeforeT45':len(cs),'books':len(bs),'consensusReversals':max(0,len(cl)-1),'bookReversals':len(bel)})
        diag.append({'slateDateEt':slate,'pulls':len(pulls),'latestGames':len(latest),'processedGames':done,'officialGames':len(fs),'error':ferr});day+=timedelta(days=1)
    mc=[x for x in ce if x['amplitudePp']>=MEANINGFUL_PP];mb=[x for x in be if x['amplitudePp']>=MEANINGFUL_PP]
    rep=defaultdict(list)
    for x in mc:rep[round(x['amplitudePp']*4)/4].append(x)
    repeated=[{'roundedSizePp':k,'events':len(v),'games':len({x['gameKey'] for x in v}),'slates':len({x['slateDateEt'] for x in v}),'towardWinnerPct':cond(v)['towardWinnerPct'],'examples':[{'date':x['slateDateEt'],'matchup':x['matchup'],'amplitudePp':x['amplitudePp'],'direction':x['direction']} for x in v[:8]]} for k,v in rep.items() if len(v)>=3 and len({x['gameKey'] for x in v})>=2]
    repeated.sort(key=lambda x:(-x['events'],x['roundedSizePp']))
    sync=[x for x in mc if x['matchedBooks']>=3];tight=[x for x in sync if x['tightSameSize']]
    out={'ok':True,'version':'MLB-REVERSAL-SIZE-AUDIT-v1','readOnly':True,'createdAtUtc':datetime.now(timezone.utc).isoformat(),'scope':{'startDate':a.start_date,'endDate':a.end_date,'cutoffMinutes':45,'stepThresholdPp':EPS_PP,'meaningfulPp':MEANINGFUL_PP,'sizeBinPp':.25,'syncWindowMinutes':SYNC_MIN},'summary':{'games':len(games),'gamesAnyConsensusReversal':len({x['gameKey'] for x in ce}),'gamesMeaningfulConsensusReversal':len({x['gameKey'] for x in mc}),'consensusEvents':len(ce),'meaningfulConsensusEvents':len(mc),'bookEvents':len(be),'meaningfulBookEvents':len(mb),'synchronizedAtLeast3Books':len(sync),'tightSameSizeAtLeast4Books':len(tight),'mondayGames':len({x['gameKey'] for x in games if x['weekday']=='Monday'}),'mondayMeaningfulEvents':len([x for x in mc if x['weekday']=='Monday'])},'consensusBands':summarize(mc),'bookBands':summarize(mb),'repeatedConsensusQuarterPointSizes':repeated[:50],'patterns':{'all':cond(mc),'breadth50':cond([x for x in mc if (x['breadthPct'] or 0)>=50]),'breadth75':cond([x for x in mc if (x['breadthPct'] or 0)>=75]),'tightSameSize':cond(tight),'recoveryAtLeast1':cond([x for x in mc if (x['recoveryRatio'] or 0)>=1]),'recoveryBelow1':cond([x for x in mc if x['recoveryRatio'] is not None and x['recoveryRatio']<1]),'late60':cond([x for x in mc if 0<=x['minutesBeforeGame']<=60]),'marketFlip':cond([x for x in mc if x['marketFlip']]),'mondays':cond([x for x in mc if x['weekday']=='Monday'])},'topSynchronizedEvents':sorted(sync,key=lambda x:(-x['matchedBooks'],x['bookRangePp'] if x['bookRangePp'] is not None else 999,-x['amplitudePp']))[:75],'tightSameSizeEvents':sorted(tight,key=lambda x:(-x['matchedBooks'],x['bookRangePp']))[:75],'games':games,'consensusEvents':ce,'bookEvents':be,'dateDiagnostics':diag}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
    print(json.dumps(out['summary'],indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
