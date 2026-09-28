'use client';
import {useEffect,useMemo,useState} from 'react';
import {GENERATED_ARB_WEBSOCKET_URL} from '@/lib/generatedArbApi';
import {ArbSignals} from '@/components/ArbSignals';
import './arb-v2.css';

type Status='VERIFIED'|'HELD';
type Leg={book:string;bet:string;odds:number;lastUpdate?:string;limit?:number;link?:string};
type O={id:string;sport:string;event:string;market:string;roi:number;age:number;legs:Leg[];status:Status;reason?:string;start?:string;mathArb?:boolean;executable?:boolean;books?:number;validation?:any};
const dec=(a:number)=>Math.abs(a)>=100?(a>0?1+a/100:1+100/Math.abs(a)):0;
const american=(n:number)=>n>0?`+${n}`:String(n);
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number.isFinite(n)?n:0);
const roiFrom=(legs:Leg[])=>{const s=legs.reduce((n,l)=>{const d=dec(l.odds);return n+(d?1/d:99)},0);return s>0?100*(1/s-1):0;};
const empty:O={id:'',sport:'',event:'',market:'',roi:0,age:0,legs:[],status:'HELD'};
const ageFrom=(legs:any[],fallbackMs?:number)=>{const now=Date.now();const ages=legs.map(l=>Date.parse(String(l?.last_update||l?.lastUpdate||''))).filter(Number.isFinite).map(t=>Math.max(0,Math.floor((now-t)/1000)));if(ages.length)return Math.max(...ages);return fallbackMs?Math.max(0,Math.floor((now-fallbackMs)/1000)):0};

function toLegs(raw:any[]):Leg[]{
  return raw.map((x:any)=>({
    book:String(x?.book||x?.bookmaker||'Book'),
    bet:String(x?.outcome||x?.name||'Outcome'),
    odds:Number(x?.american??x?.american_odds??x?.price??x?.odds??0),
    lastUpdate:x?.last_update?String(x.last_update):undefined,
    limit:Number.isFinite(Number(x?.limit))?Number(x.limit):undefined,
    link:typeof x?.link==='string'?x.link:undefined,
  })).filter((x:Leg)=>x.odds!==0);
}

function toRow(row:any,status:Status,fallbackMs?:number):O|null{
  const raw=Array.isArray(row?.legs)?row.legs:Array.isArray(row?.quotes)?row.quotes:[];
  const legs=toLegs(raw);
  if(legs.length<2)return null;
  return {
    id:String(row.market_id||row.event_id||row.event||`${status}-${legs[0].book}-${legs[1].book}`),
    sport:String(row.sport||row.league||'MLB').toUpperCase(),
    event:String(row.event||row.event_id||'Market opportunity'),
    market:String(row.market||'Market'),
    roi:Number(row.margin_pct??roiFrom(legs)),
    age:ageFrom(raw,fallbackMs),
    legs,
    status,
    reason:row?.validation?.settlement_reason||row?.reason,
    start:row?.commence_time||row?.start,
    mathArb:Boolean(row?.math_arb),
    executable:Boolean(row?.validation?.executable??row?.executable),
    books:legs.length,
    validation:row?.validation,
  };
}

function collect(data:any):O[]{
  const found:O[]=[];
  const seen=new Set<string>();
  const push=(row:any,status:Status,fallbackMs?:number)=>{
    const item=toRow(row,status,fallbackMs);
    if(!item||seen.has(item.id))return;
    seen.add(item.id);
    found.push(item);
  };
  for(const row of (Array.isArray(data?.hits)?data.hits:[])) push(row,'VERIFIED');
  for(const row of (Array.isArray(data?.held)?data.held:[])) push(row,'HELD');
  for(const scan of (Array.isArray(data?.history)?data.history:[])){
    const p=scan?.payload||scan?.data||scan||{};
    const at=Number(scan?.created_at_ms)||undefined;
    for(const row of (p.hits||[])) push(row,'VERIFIED',at);
    for(const row of (p.detected_unverified||p.held||[])) push(row,'HELD',at);
  }
  return found.sort((a,b)=>b.roi-a.roi).slice(0,100);
}

export default function Page(){
 const[selected,setSelected]=useState<O>(empty);
 const[rows,setRows]=useState<O[]>([]);
 const[query,setQuery]=useState('');
 const[book,setBook]=useState('All Books');
 const[updatedAt,setUpdatedAt]=useState<number|null>(null);
 const[mode,setMode]=useState<'loading'|'live'|'preview'>('loading');
 const[bankText,setBankText]=useState('1000');
 const[detailOpen,setDetailOpen]=useState(true);
 const[favorites,setFavorites]=useState<Set<string>>(new Set());
 const[sortDesc,setSortDesc]=useState(true);
 const[sport,setSport]=useState('All Sports');
 const[market,setMarket]=useState('All Markets');
 const[verifiedOnly,setVerifiedOnly]=useState(false);

 useEffect(()=>{
  let active=true;
  const load=()=>fetch('/v1/inqsi/arbitrage/opportunities',{cache:'no-store'}).then(async r=>{
    const data=await r.json();
    if(!active)return;
    const found=collect(data);
    const live=r.ok && (data.mode==='live' || data.source==='live' || data.source==='store' || data.source==='inqsi-arb-live-scan');
    setRows(live?found:[]);
    if(live&&found.length)setSelected(prev=>found.find(x=>x.id===prev.id)||found[0]);
    setUpdatedAt(Date.now());
    setMode(live?'live':'preview');
  }).catch(()=>{if(active){setRows([]);setMode('preview');}});
  load();
  const id=setInterval(load,30000);
  let ws:WebSocket|null=null;
  let reconnect:ReturnType<typeof setTimeout>|null=null;
  const connect=()=>{
    if(!active||!GENERATED_ARB_WEBSOCKET_URL.startsWith('wss://'))return;
    try{
      ws=new WebSocket(GENERATED_ARB_WEBSOCKET_URL);
      ws.onmessage=(event)=>{
        try{
          const message=JSON.parse(String(event.data||'{}'));
          if(message?.type==='ARB_SCAN_UPDATE') load();
        }catch{}
      };
      ws.onclose=()=>{if(active)reconnect=setTimeout(connect,5000);};
      ws.onerror=()=>ws?.close();
    }catch{if(active)reconnect=setTimeout(connect,5000);}
  };
  connect();
  return()=>{active=false;clearInterval(id);if(reconnect)clearTimeout(reconnect);ws?.close();};
 },[]);

 const bank=Math.max(0,Number(bankText.replace(/,/g,''))||0);
 const calc=useMemo(()=>{
  const ds=selected.legs.map(l=>dec(l.odds));
  if(!bank||ds.some(d=>!d))return{stakes:selected.legs.map(()=>0),p:0,r:0,payout:0};
  const inv=ds.map(d=>1/d),sum=inv.reduce((a,b)=>a+b,0),stakes=inv.map(v=>bank*v/sum),payout=bank/sum,p=payout-bank;
  return{stakes,p,r:100*p/bank,payout};
 },[selected,bank]);
 const isArb=calc.p>0;
 const roiMismatch=mode==='live'&&Math.abs(calc.r-selected.roi)>.15;
 const overLimit=selected.legs.some((l,i)=>Number.isFinite(l.limit)&&Number(l.limit)<calc.stakes[i]);
 const filtered=rows.filter(o=>{
  const q=query.trim().toLowerCase();
  const values=[o.event,o.sport,o.market,...o.legs.flatMap(l=>[l.book,l.bet])];
  const matchesQuery=!q||values.some(v=>v.toLowerCase().includes(q));
  return matchesQuery&&(sport==='All Sports'||o.sport===sport)&&(market==='All Markets'||o.market===market)&&(book==='All Books'||o.legs.some(l=>l.book===book))&&(!verifiedOnly||o.status==='VERIFIED');
 }).sort((a,b)=>sortDesc?b.roi-a.roi:a.roi-b.roi);
 const sports=['All Sports',...Array.from(new Set(rows.map(x=>x.sport)))];
 const markets=['All Markets',...Array.from(new Set(rows.map(x=>x.market)))];
 const books=['All Books',...Array.from(new Set(rows.flatMap(x=>x.legs.map(l=>l.book))))];
 const avgAge=rows.length?Math.round(rows.reduce((n,x)=>n+x.age,0)/rows.length):0;

 return <main className="arb2">
  <header className="top"><a className="brand" href="/"><i/>InQsi</a><nav><a className="active">▣ Arbitrage</a><a href="/arbitrage-v2/calculator">▦ Calculator</a><a href="/sports">⚑ Picks</a><a href="/parlay-scanner">☷ Slip Scanner</a></nav><label className="search">⌕ <input aria-label="Search opportunities" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search teams, sports, or books..."/></label><button className="avatar" aria-label="Account">JK</button></header>
  {mode==='preview'&&<div className="preview-banner">LIVE ODDS TEMPORARILY UNAVAILABLE · NO SAMPLE OPPORTUNITIES ARE BEING SHOWN</div>}
  <div className="command-shell">
   <aside className="filter-rail">
    <div className="filter-title"><b>Filters</b><button type="button" onClick={()=>{setSport('All Sports');setMarket('All Markets');setBook('All Books');setVerifiedOnly(false);setQuery('');}}>Reset All</button></div>
    <label>Sports<select value={sport} onChange={e=>setSport(e.target.value)}>{sports.map(x=><option key={x}>{x}</option>)}</select></label>
    <div className="sport-list">{sports.filter(x=>x!=='All Sports').slice(0,10).map(x=><button type="button" className={sport===x?'on':''} key={x} onClick={()=>setSport(sport===x?'All Sports':x)}>◉ {x}</button>)}</div>
    <label>Sportsbooks<select value={book} onChange={e=>setBook(e.target.value)}>{books.map(x=><option key={x}>{x}</option>)}</select></label>
    <div className="book-list">{books.filter(x=>x!=='All Books').slice(0,8).map(x=><label key={x}><input type="checkbox" checked={book===x} onChange={()=>setBook(book===x?'All Books':x)}/>{x}</label>)}</div>
    <label>State / Region<select><option>All States (US)</option></select></label>
    <label>Minimum ROI<select><option>0.5%</option></select></label>
    <div className="timing"><b>Event Timing</b><label><input type="checkbox" defaultChecked/> Pre-game</label><label><input type="checkbox" defaultChecked/> Live</label></div>
   </aside>
   <section className="command-main">
    <div className="command-heading"><div><h1>ARB Command Center</h1><p>Real-time sports arbitrage opportunities across top sportsbooks.</p></div><div className={'connection '+mode}><i/>{mode==='live'?'Live Data':mode==='loading'?'Connecting':'Unavailable'}</div></div>
    <div className="kpis"><div><b>⚡ {rows.length}</b><span>Live Opportunities</span></div><div><b>◥ {rows.length?Math.max(...rows.map(x=>x.roi)).toFixed(1):'0.0'}%</b><span>Best ROI</span></div><div><b>◷ {avgAge}s</b><span>Avg. Freshness</span></div><div><b>◉ {Math.max(0,books.length-1)}</b><span>Books Online</span></div></div>
    <section className="feed">
    <div className="filters"><span className="opp-count">Opportunities ({filtered.length})</span><label className="toggle"><input type="checkbox" checked={verifiedOnly} onChange={e=>setVerifiedOnly(e.target.checked)}/><span/>Verified Only</label><button className="sort" type="button" onClick={()=>setSortDesc(v=>!v)}>ROI {sortDesc?'High → Low':'Low → High'}</button><div className={'connection '+mode}><i/>{mode==='live'?'Live':mode==='loading'?'Connecting':'Unavailable'}{mode==='live'&&updatedAt?' · refreshed just now':''}</div></div>
    <div className="head"><span>SPORT / MARKET</span><span>EVENT</span><span>SPORTSBOOKS (ODDS)</span><span>ROI</span><span>STATUS</span><span>LAST UPDATE</span></div>
    <div className="rows">{filtered.length===0?<div className="empty">{mode==='live'?'No live opportunities match these filters.':'Waiting on live ARB scan — sample slates are not shown.'}</div>:filtered.map(o=><button key={o.id} className={'row '+(selected.id===o.id?'selected':'')} onClick={()=>{setSelected(o);if(typeof window!=='undefined'&&window.innerWidth<=980)setTimeout(()=>document.getElementById('arb-detail')?.scrollIntoView({behavior:'smooth',block:'start'}),0)}}>
     <span className="sport"><b>{o.sport}</b><small>{o.market}</small></span><span className="event"><b>{o.event.includes(' @ ')?o.event.split(' @ ')[0]:o.event.split(' vs ')[0]}</b><b>{o.event.includes(' @ ')?o.event.split(' @ ')[1]:(o.event.split(' vs ')[1]||'')}</b></span>
     <span className="books">{o.legs.slice(0,2).map((l,i)=><span key={i}><small>{l.book}</small><b>{american(l.odds)}</b></span>)}{o.legs.length>2&&<small>+{o.legs.length-2} more</small>}</span>
     <span className="roi">{o.roi>=0?'+':''}{o.roi.toFixed(2)}%</span><span><em className={o.status==='VERIFIED'?'verified':'held'}>{o.status==='VERIFIED'?'VERIFIED':'HELD BACK'}</em></span><span className="age">{o.age}s ago　›</span>
    </button>)}</div>
   </section>
   </section>
   <aside className={'detail '+(rows.length?'':'hidden')} id="arb-detail">
    <div className="detail-top"><div><small>{selected.sport}　·　{selected.market}</small><b>{selected.age}s ago <i/></b></div><div className="title-line"><h2>{selected.event}</h2><button className={'favorite '+(favorites.has(selected.id)?'on':'')} aria-label="Save opportunity" onClick={()=>setFavorites(prev=>{const next=new Set(prev);next.has(selected.id)?next.delete(selected.id):next.add(selected.id);return next})}>☆</button></div></div>
    <div className="roi-hero"><div><strong>{selected.roi>=0?'+':''}{selected.roi.toFixed(2)}%</strong><small>Estimated ROI</small></div><em className={selected.status==='VERIFIED'?'verified':'held'}>{selected.status==='VERIFIED'?'VERIFIED EXECUTABLE':'HELD BACK'}</em></div>
    <div className={'quote-pair '+(selected.legs.length>2?'multi':'')}>{selected.legs.map((l,i)=><div key={i}><small>{l.book}</small><span>{l.bet}</span><b>{american(l.odds)}</b></div>)}</div>
    <label className="stake">Total Stake<div><span>$</span><input aria-label="Total stake" inputMode="decimal" value={bankText} onChange={e=>setBankText(e.target.value.replace(/[^0-9.,]/g,''))}/></div></label>
    <div className="presets">{[50,100,500,1000,2500].map(n=><button key={n} className={bank===n?'on':''} onClick={()=>setBankText(String(n))}>{money(n).replace('.00','')}</button>)}</div>
    <div className={'allocations '+(selected.legs.length>2?'multi':'')}>{selected.legs.map((l,i)=><div key={i}><small>Bet on {l.bet}<br/>at {l.book}</small><strong>{money(calc.stakes[i]||0)}</strong><span>Potential Payout<br/>{money((calc.stakes[i]||0)*dec(l.odds))}</span>{Number.isFinite(l.limit)&&<span>Book limit {money(Number(l.limit))}</span>}</div>)}</div>
    <div className={'profit '+(!isArb?'invalid':'')}><div><span>{isArb?'Guaranteed Profit':'No guaranteed profit'}</span><strong>{isArb?money(calc.p):'—'}</strong></div><div><span>Return</span><strong>{calc.r.toFixed(2)}%</strong></div></div>
    {roiMismatch&&<div className="calc-warning">Displayed prices no longer reproduce the recorded ROI. Treat this opportunity as stale until refreshed.</div>}
    {overLimit&&<div className="calc-warning">Requested stake exceeds at least one known sportsbook limit.</div>}
    <button className="details-button" onClick={()=>setDetailOpen(v=>!v)}>{detailOpen?'Hide Market Details ↑':'View Full Market Details →'}</button>
    {detailOpen&&<div className="evidence"><ArbSignals row={selected}/></div>}
   </aside>
  </div>
  <section className={'execution-workspace '+(rows.length?'':'hidden')}>
   <div className="execution-head"><a href="#arb-detail">← Back to Opportunities</a><div><h2>{selected.event}</h2><span>{selected.sport} · {selected.market}</span></div><strong>{selected.roi.toFixed(1)}% ARB<small>Estimated ROI</small></strong><span>Last updated<br/><b>{selected.age} seconds ago</b></span></div>
   <div className="execution-grid">
    <div className="leg-cards">{selected.legs.slice(0,2).map((l,i)=><div className={'leg '+(i?'blue':'green')} key={i}><small>Leg {i+1} · {l.book}</small><div><b>{l.bet}</b><strong>{american(l.odds)}</strong></div><div><span>Stake <b>{money(calc.stakes[i]||0)}</b></span><span>To Win <b>{money((calc.stakes[i]||0)*(dec(l.odds)-1))}</b></span><span>Total Payout <b>{money((calc.stakes[i]||0)*dec(l.odds))}</b></span></div>{l.link?<a className="book-link" href={l.link} target="_blank" rel="noreferrer">Open at {l.book} →</a>:<button type="button">Open at {l.book} →</button>}</div>)}</div>
    <div className="history-card"><b>Opportunity History</b><div className="spark"><i/><i/><i/><i/><i/><i/></div><small>Freshness from last live pull</small></div>
    <div className="movement-card"><b>Market Movement</b>{selected.legs.slice(0,2).map((l,i)=><p key={i}><span>{Math.max(1,selected.age+i*8)}s ago</span><strong>{american(l.odds)}</strong></p>)}</div>
   </div>
   <div className="execution-bottom"><div className="mini-calc"><b>Arbitrage Calculator</b><div><label>Total Stake<input value={bankText} onChange={e=>setBankText(e.target.value.replace(/[^0-9.,]/g,''))}/></label><label>Odds Format<select><option>American</option></select></label><span>Guaranteed Profit<strong>{money(calc.p)}</strong></span><span>ROI<strong>{calc.r.toFixed(1)}%</strong></span></div></div><div className="steps"><b>Execution Steps</b><ol><li>Verify both prices</li><li>Open first book</li><li>Recheck second price</li><li>Complete second leg</li></ol></div></div>
   <div className="safety-strip">Verify prices before placing either leg. InQsi does not place wagers. Odds can change at any time.</div>
  </section>
  <section className="how">
    <article><b>1. Open InQsi</b><p>Live opportunities appear as soon as the ARB scan returns quotes.</p></article>
    <article><b>2. Select opportunity</b><p>Click a row to load books, ROI, and stake math.</p></article>
    <article><b>3. Enter any amount</b><p>Type a stake or tap a preset. Allocations update instantly.</p></article>
    <article><b>4. Get instant results</b><p>See exact stakes and guaranteed profit from the live prices.</p></article>
    <article><b>5. View details</b><p>Check verification, limits, and book links before you act.</p></article>
  </section>
  <nav className="mobile-nav"><a className="active">▣<small>Arb</small></a><a href="/arbitrage-v2/calculator">▦<small>Calculator</small></a><a href="/sports">⚑<small>Picks</small></a><a href="/parlay-scanner">•••<small>More</small></a></nav>
 </main>;
}
