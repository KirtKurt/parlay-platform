'use client';

import { useEffect, useMemo, useState } from 'react';
import { AppHeader } from '@/components/AppHeader';
import { GENERATED_ARB_WEBSOCKET_URL } from '@/lib/generatedArbApi';

type Status='VERIFIED'|'HELD';
type Leg={book:string;bet:string;odds:number;lastUpdate?:string;limit?:number;link?:string};
type Opportunity={id:string;sport:string;event:string;market:string;roi:number;age:number;legs:Leg[];status:Status;start?:string};

const dec=(a:number)=>Math.abs(a)>=100?(a>0?1+a/100:1+100/Math.abs(a)):0;
const american=(n:number)=>n>0?`+${n}`:String(n);
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number.isFinite(n)?n:0);
const roiFrom=(legs:Leg[])=>{const s=legs.reduce((n,l)=>{const d=dec(l.odds);return n+(d?1/d:99)},0);return s>0?100*(1/s-1):0;};
const ageFrom=(legs:any[],fallbackMs?:number)=>{const now=Date.now();const ages=legs.map(l=>Date.parse(String(l?.last_update||l?.lastUpdate||''))).filter(Number.isFinite).map(t=>Math.max(0,Math.floor((now-t)/1000)));if(ages.length)return Math.max(...ages);return fallbackMs?Math.max(0,Math.floor((now-fallbackMs)/1000)):0};

function toLegs(raw:any[]):Leg[]{
  return raw.map((x:any)=>({
    book:String(x?.book||x?.bookmaker||'Sportsbook'),
    bet:String(x?.outcome||x?.name||'Outcome'),
    odds:Number(x?.american??x?.american_odds??x?.price??x?.odds??0),
    lastUpdate:x?.last_update?String(x.last_update):undefined,
    limit:Number.isFinite(Number(x?.limit))?Number(x.limit):undefined,
    link:typeof x?.link==='string'?x.link:undefined,
  })).filter((x:Leg)=>x.odds!==0);
}

function toRow(row:any,status:Status,fallbackMs?:number):Opportunity|null{
  const raw=Array.isArray(row?.legs)?row.legs:Array.isArray(row?.quotes)?row.quotes:[];
  const legs=toLegs(raw);
  if(legs.length<2)return null;
  return {
    id:String(row.market_id||row.event_id||row.event||`${status}-${legs[0].book}-${legs[1].book}`),
    sport:String(row.sport||row.league||'SPORT').toUpperCase(),
    event:String(row.event||row.event_id||'Market opportunity'),
    market:String(row.market||'Market'),
    roi:Number(row.margin_pct??roiFrom(legs)),
    age:ageFrom(raw,fallbackMs),
    legs,
    status,
    start:row?.commence_time||row?.start,
  };
}

function collect(data:any):Opportunity[]{
  const found:Opportunity[]=[]; const seen=new Set<string>();
  const push=(row:any,status:Status,fallbackMs?:number)=>{const item=toRow(row,status,fallbackMs);if(!item||seen.has(item.id))return;seen.add(item.id);found.push(item);};
  for(const row of (Array.isArray(data?.hits)?data.hits:[])) push(row,'VERIFIED');
  for(const row of (Array.isArray(data?.held)?data.held:[])) push(row,'HELD');
  for(const scan of (Array.isArray(data?.history)?data.history:[])){
    const p=scan?.payload||scan?.data||scan||{}; const at=Number(scan?.created_at_ms)||undefined;
    for(const row of (p.hits||[])) push(row,'VERIFIED',at);
    for(const row of (p.detected_unverified||p.held||[])) push(row,'HELD',at);
  }
  return found.sort((a,b)=>b.roi-a.roi).slice(0,100);
}

export default function ArbitragePage(){
  const [rows,setRows]=useState<Opportunity[]>([]);
  const [selectedId,setSelectedId]=useState('');
  const [mode,setMode]=useState<'loading'|'live'|'unavailable'>('loading');
  const [query,setQuery]=useState('');
  const [sport,setSport]=useState('All Sports');
  const [market,setMarket]=useState('All Markets');
  const [book,setBook]=useState('All Books');
  const [verifiedOnly,setVerifiedOnly]=useState(false);
  const [sortDesc,setSortDesc]=useState(true);
  const [bankText,setBankText]=useState('1000');

  useEffect(()=>{
    let active=true;
    const load=()=>fetch('/v1/inqsi/arbitrage/opportunities',{cache:'no-store'}).then(async r=>{
      const data=await r.json().catch(()=>({}));
      if(!active)return;
      const found=collect(data);
      const live=r.ok&&(data.mode==='live'||data.source==='live'||data.source==='store'||data.source==='inqsi-arb-live-scan');
      setRows(live?found:[]);
      setMode(live?'live':'unavailable');
      setSelectedId(prev=>live&&found.length?(found.some(x=>x.id===prev)?prev:found[0].id):'');
    }).catch(()=>{if(active){setRows([]);setSelectedId('');setMode('unavailable');}});
    load();
    const timer=setInterval(load,30000);
    let ws:WebSocket|null=null; let reconnect:ReturnType<typeof setTimeout>|null=null;
    const connect=()=>{
      if(!active||!GENERATED_ARB_WEBSOCKET_URL.startsWith('wss://'))return;
      try{
        ws=new WebSocket(GENERATED_ARB_WEBSOCKET_URL);
        ws.onmessage=(event)=>{try{const message=JSON.parse(String(event.data||'{}'));if(message?.type==='ARB_SCAN_UPDATE')load();}catch{}};
        ws.onclose=()=>{if(active)reconnect=setTimeout(connect,5000);};
        ws.onerror=()=>ws?.close();
      }catch{if(active)reconnect=setTimeout(connect,5000);}
    };
    connect();
    return()=>{active=false;clearInterval(timer);if(reconnect)clearTimeout(reconnect);ws?.close();};
  },[]);

  const selected=rows.find(x=>x.id===selectedId);
  const sports=['All Sports',...Array.from(new Set(rows.map(x=>x.sport)))];
  const markets=['All Markets',...Array.from(new Set(rows.map(x=>x.market)))];
  const books=['All Books',...Array.from(new Set(rows.flatMap(x=>x.legs.map(l=>l.book))))];

  const filtered=rows.filter(o=>{
    const q=query.trim().toLowerCase();
    const values=[o.event,o.sport,o.market,...o.legs.flatMap(l=>[l.book,l.bet])];
    return (!q||values.some(v=>v.toLowerCase().includes(q)))
      &&(sport==='All Sports'||o.sport===sport)
      &&(market==='All Markets'||o.market===market)
      &&(book==='All Books'||o.legs.some(l=>l.book===book))
      &&(!verifiedOnly||o.status==='VERIFIED');
  }).sort((a,b)=>sortDesc?b.roi-a.roi:a.roi-b.roi);

  const bank=Math.max(0,Number(bankText.replace(/,/g,''))||0);
  const calc=useMemo(()=>{
    if(!selected)return{stakes:[] as number[],p:0,r:0,payout:0};
    const ds=selected.legs.map(l=>dec(l.odds));
    if(!bank||ds.some(d=>!d))return{stakes:selected.legs.map(()=>0),p:0,r:0,payout:0};
    const inv=ds.map(d=>1/d),sum=inv.reduce((a,b)=>a+b,0),stakes=inv.map(v=>bank*v/sum),payout=bank/sum,p=payout-bank;
    return{stakes,p,r:100*p/bank,payout};
  },[selected,bank]);

  return <main className="arb-approved">
    <AppHeader active="arb" apiStatus={mode==='live'?'CONNECTED':mode==='loading'?'WAITING':'FAILED'} apiDetail={mode==='live'?'Live arbitrage feed connected':'Waiting on sportsbook market feed'} />
    {mode==='unavailable'&&<div className="mockup-unavailable">Live odds temporarily unavailable · ARB is wired and waiting for the provider feed</div>}
    <section className="arb-toolbar">
      <select aria-label="Sport filter" value={sport} onChange={e=>setSport(e.target.value)}>{sports.map(x=><option key={x}>{x}</option>)}</select>
      <select aria-label="Market filter" value={market} onChange={e=>setMarket(e.target.value)}>{markets.map(x=><option key={x}>{x}</option>)}</select>
      <select aria-label="Sportsbook filter" value={book} onChange={e=>setBook(e.target.value)}>{books.map(x=><option key={x}>{x}</option>)}</select>
      <label className="mockup-status"><input type="checkbox" checked={verifiedOnly} onChange={e=>setVerifiedOnly(e.target.checked)}/> Verified only</label>
      <div className="spacer"/>
      <label className="arb-search">⌕ <input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search teams, sports, or books..."/></label>
      <button className="mockup-pill" type="button" onClick={()=>setSortDesc(v=>!v)}>Sort by ROI {sortDesc?'↓':'↑'}</button>
      <span className={'mockup-status '+(mode==='live'?'':'down')}><i/>{mode==='live'?'Live':'Waiting'}</span>
    </section>
    <section className="arb-grid">
      <div className="arb-board">
        <div className="arb-head"><span>SPORT / MARKET</span><span>EVENT</span><span>SPORTSBOOKS (ODDS)</span><span>ROI</span><span>STATUS</span><span>LAST UPDATE</span></div>
        {filtered.length?<div className="arb-rows">{filtered.map(o=><button className={'arb-row '+(selectedId===o.id?'selected':'')} key={o.id} onClick={()=>setSelectedId(o.id)}>
          <span className="arb-sport"><b>{o.sport}</b><small>{o.market}</small></span>
          <span className="arb-event"><b>{o.event.includes(' @ ')?o.event.split(' @ ')[0]:o.event.split(' vs ')[0]}</b><b>{o.event.includes(' @ ')?o.event.split(' @ ')[1]:(o.event.split(' vs ')[1]||'')}</b></span>
          <span className="arb-books">{o.legs.slice(0,2).map((l,i)=><span className="arb-book" key={i}><small>{l.book}</small><b>{american(l.odds)}</b></span>)}</span>
          <span className="arb-roi">{o.roi>=0?'+':''}{o.roi.toFixed(2)}%</span>
          <span><em className={'arb-status '+(o.status==='VERIFIED'?'verified':'held')}>{o.status==='VERIFIED'?'VERIFIED':'HELD BACK'}</em></span>
          <span className="arb-age">{o.age}s ago　›</span>
        </button>)}</div>:<div className="mockup-empty"><div><b>{mode==='loading'?'Connecting to ARB':'No live opportunities to display'}</b><span>{mode==='loading'?'InQsi is connecting to the live market feed.':'The interface is fully wired. No sample opportunities are shown while the Odds API is unavailable.'}</span></div></div>}
      </div>
      <aside className="arb-detail" id="arb-detail">
        {!selected?<div className="arb-empty-detail"><div><b>Opportunity details</b><span>Select a live opportunity when the market feed returns. Stake math and book allocations will calculate here instantly.</span></div></div>:<>
          <div className="detail-kicker"><span>{selected.sport} · {selected.market}</span><span>{selected.age}s ago ●</span></div>
          <h2>{selected.event}</h2>
          <div className="detail-time">{selected.start||'Current market'}</div>
          <div className="arb-roi-hero"><div><strong>{selected.roi>=0?'+':''}{selected.roi.toFixed(2)}%</strong><small>Estimated ROI</small></div><em className={'arb-status '+(selected.status==='VERIFIED'?'verified':'held')}>{selected.status==='VERIFIED'?'VERIFIED EXECUTABLE':'HELD BACK'}</em></div>
          <div className="arb-quote-pair">{selected.legs.slice(0,2).map((l,i)=><div className="arb-quote-card" key={i}><small>{l.book}</small><span>{l.bet}</span><b>{american(l.odds)}</b></div>)}</div>
          <label className="arb-stake">Total Stake<div className="arb-stake-box"><span>$</span><input aria-label="Total stake" inputMode="decimal" value={bankText} onChange={e=>setBankText(e.target.value.replace(/[^0-9.,]/g,''))}/></div></label>
          <div className="arb-presets">{[50,100,500,1000,2500].map(n=><button key={n} className={bank===n?'on':''} onClick={()=>setBankText(String(n))}>{money(n).replace('.00','')}</button>)}</div>
          <div className="arb-alloc">{selected.legs.slice(0,2).map((l,i)=><div key={i}><small>Bet on {l.bet}<br/>at {l.book}</small><strong>{money(calc.stakes[i]||0)}</strong><span>Potential Payout<br/>{money((calc.stakes[i]||0)*dec(l.odds))}</span></div>)}</div>
          <div className="arb-profit"><div><span>Guaranteed Profit</span><strong>{calc.p>0?money(calc.p):'—'}</strong></div><div><span>Return</span><strong>{calc.r.toFixed(2)}%</strong></div></div>
          <button className="arb-detail-button" type="button">View Full Market Details →</button>
          <div className="arb-tabs"><span>Market Details</span><span>Book Info</span><span>Settlement</span><span>Historical Odds</span></div>
        </>}
      </aside>
    </section>
  </main>;
}
