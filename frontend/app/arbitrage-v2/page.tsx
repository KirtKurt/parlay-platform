'use client';
import {useEffect,useMemo,useState} from 'react';
import './arb-v2.css';

type Status='VERIFIED'|'HELD';
type Leg={book:string;bet:string;odds:number;lastUpdate?:string;limit?:number};
type O={id:string;sport:string;event:string;market:string;roi:number;age:number;legs:Leg[];status:Status;reason?:string};
const dec=(a:number)=>Math.abs(a)>=100?(a>0?1+a/100:1+100/Math.abs(a)):0;
const american=(n:number)=>n>0?`+${n}`:String(n);
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number.isFinite(n)?n:0);
const roiFrom=(legs:Leg[])=>{const s=legs.reduce((n,l)=>{const d=dec(l.odds);return n+(d?1/d:99)},0);return s>0?100*(1/s-1):0};
const sample=(id:string,sport:string,event:string,market:string,legs:Leg[],age:number,status:Status='VERIFIED',reason?:string):O=>({id,sport,event,market,legs,age,status,reason,roi:roiFrom(legs)});
const demo:O[]=[
 sample('1','MLB','Yankees vs Red Sox','Moneyline',[{book:'DraftKings',bet:'Yankees',odds:115},{book:'FanDuel',bet:'Red Sox',odds:102}],4),
 sample('2','NBA','Lakers vs Warriors','Moneyline',[{book:'BetMGM',bet:'Lakers',odds:120},{book:'Caesars',bet:'Warriors',odds:105}],7),
 sample('3','NFL','Chiefs vs Bills','Spread',[{book:'FanDuel',bet:'Chiefs -2.5',odds:108},{book:'DraftKings',bet:'Bills +3.0',odds:110}],11),
 sample('4','Soccer','Arsenal vs Chelsea','3-way Moneyline',[{book:'DraftKings',bet:'Arsenal',odds:245},{book:'FanDuel',bet:'Draw',odds:360},{book:'BetMGM',bet:'Chelsea',odds:250}],15),
 sample('5','NCAAB','UConn vs Marquette','Moneyline',[{book:'Caesars',bet:'UConn',odds:125},{book:'FanDuel',bet:'Marquette',odds:110}],22,'HELD','Settlement verification pending'),
 sample('6','MLB','Dodgers vs Padres','Total',[{book:'DraftKings',bet:'Over 8.5',odds:112},{book:'BetMGM',bet:'Under 8.5',odds:105}],27)
];
const empty:O={id:'',sport:'',event:'',market:'',roi:0,age:0,legs:[],status:'HELD'};
const ageFrom=(legs:any[],fallbackMs?:number)=>{const now=Date.now();const ages=legs.map(l=>Date.parse(String(l?.last_update||l?.lastUpdate||''))).filter(Number.isFinite).map(t=>Math.max(0,Math.floor((now-t)/1000)));if(ages.length)return Math.max(...ages);return fallbackMs?Math.max(0,Math.floor((now-fallbackMs)/1000)):0};

export default function Page(){
 const[selected,setSelected]=useState<O>(empty); const[rows,setRows]=useState<O[]>([]); const[query,setQuery]=useState(''); const[book,setBook]=useState('All Books'); const[updatedAt,setUpdatedAt]=useState<number|null>(null); const[mode,setMode]=useState<'loading'|'live'|'preview'>('loading');
 const[bankText,setBankText]=useState('1000'); const[detailOpen,setDetailOpen]=useState(true); const[favorites,setFavorites]=useState<Set<string>>(new Set()); const[sortDesc,setSortDesc]=useState(true); const[sport,setSport]=useState('All Sports'); const[market,setMarket]=useState('All Markets'); const[verifiedOnly,setVerifiedOnly]=useState(false); const[tab,setTab]=useState('Market Details');
 useEffect(()=>{fetch('/v1/inqsi/arbitrage/opportunities',{cache:'no-store'}).then(async r=>{const data=await r.json();if(!r.ok||data.mode!=='live'){setRows([]);setMode('preview');return}const found:O[]=[];for(const scan of (Array.isArray(data.history)?data.history:[])){const p=scan?.payload||scan?.data||scan||{};for(const [key,status] of [['hits','VERIFIED'],['detected_unverified','HELD']] as const){for(const row of (p[key]||[])){const raw=Array.isArray(row.legs)?row.legs:Array.isArray(row.quotes)?row.quotes:[];const legs:Leg[]=raw.map((x:any)=>({book:String(x?.book||x?.bookmaker||'Book'),bet:String(x?.outcome||x?.name||'Outcome'),odds:Number(x?.american??x?.american_odds??x?.price??x?.odds??0),lastUpdate:x?.last_update?String(x.last_update):undefined,limit:Number.isFinite(Number(x?.limit))?Number(x.limit):undefined})).filter((x:Leg)=>x.odds!==0);if(legs.length<2)continue;found.push({id:String(row.market_id||row.event_id||row.event||found.length),sport:String(row.sport||p.sport||'Sport').toUpperCase(),event:String(row.event||row.event_id||'Market opportunity'),market:String(row.market||'Market'),roi:Number(row.margin_pct??roiFrom(legs)),age:ageFrom(raw,Number(scan?.created_at_ms)||undefined),legs,status,reason:row?.validation?.settlement_reason||row?.reason});}}}if(found.length){found.sort((a,b)=>b.roi-a.roi);setRows(found.slice(0,50));setSelected(found[0]);}else setRows([]);setUpdatedAt(Date.now());setMode('live')}).catch(()=>{setRows([]);setMode('preview')});},[]);
 const bank=Math.max(0,Number(bankText.replace(/,/g,''))||0);
 const calc=useMemo(()=>{const ds=selected.legs.map(l=>dec(l.odds));if(!bank||ds.some(d=>!d))return{stakes:selected.legs.map(()=>0),p:0,r:0,payout:0};const inv=ds.map(d=>1/d),sum=inv.reduce((a,b)=>a+b,0),stakes=inv.map(v=>bank*v/sum),payout=bank/sum,p=payout-bank;return{stakes,p,r:100*p/bank,payout}},[selected,bank]);
 const isArb=calc.p>0; const roiMismatch=mode==='live'&&Math.abs(calc.r-selected.roi)>.15; const overLimit=selected.legs.some((l,i)=>Number.isFinite(l.limit)&&Number(l.limit)<calc.stakes[i]);
 const filtered=rows.filter(o=>{const q=query.trim().toLowerCase();const values=[o.event,o.sport,o.market,...o.legs.flatMap(l=>[l.book,l.bet])];const matchesQuery=!q||values.some(v=>v.toLowerCase().includes(q));return matchesQuery&&(sport==='All Sports'||o.sport===sport)&&(market==='All Markets'||o.market===market)&&(book==='All Books'||o.legs.some(l=>l.book===book))&&(!verifiedOnly||o.status==='VERIFIED')}).sort((a,b)=>sortDesc?b.roi-a.roi:a.roi-b.roi);
 const sports=['All Sports',...Array.from(new Set(rows.map(x=>x.sport)))]; const markets=['All Markets',...Array.from(new Set(rows.map(x=>x.market)))]; const books=['All Books',...Array.from(new Set(rows.flatMap(x=>x.legs.map(l=>l.book))))];
 return <main className="arb2">
  <header className="top"><a className="brand" href="/"><i/>InQsi</a><nav><a className="active">▣ Arbitrage</a><a href="/arbitrage-v2/calculator">▦ Calculator</a><a href="/sports">⚑ Picks</a><a href="/parlay-scanner">☷ Slip Scanner</a></nav><label className="search">⌕ <input aria-label="Search opportunities" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search teams, sports, or books..."/></label><button className="avatar" aria-label="Account">●</button></header>
  {mode==='preview'&&<div className="preview-banner">LIVE ODDS TEMPORARILY UNAVAILABLE · NO SAMPLE OPPORTUNITIES ARE BEING SHOWN</div>}
  <div className="command-shell">
   <aside className="filter-rail">
    <div className="filter-title"><b>Filters</b><button type="button" onClick={()=>{setSport('All Sports');setMarket('All Markets');setBook('All Books');setVerifiedOnly(false)}}>Reset All</button></div>
    <label>Sports<select value={sport} onChange={e=>setSport(e.target.value)}>{sports.map(x=><option key={x}>{x}</option>)}</select></label>
    <div className="sport-list">{sports.filter(x=>x!=='All Sports').slice(0,10).map(x=><button type="button" className={sport===x?'on':''} key={x} onClick={()=>setSport(x)}>◉ {x}</button>)}</div>
    <label>Sportsbooks<select value={book} onChange={e=>setBook(e.target.value)}>{books.map(x=><option key={x}>{x}</option>)}</select></label>
    <div className="book-list">{books.filter(x=>x!=='All Books').slice(0,8).map(x=><label key={x}><input type="checkbox" checked={book===x} onChange={()=>setBook(book===x?'All Books':x)}/>{x}</label>)}</div>
    <label>State / Region<select><option>All States (US)</option></select></label>
    <label>Minimum ROI<select><option>0.5%</option><option>1.0%</option><option>2.0%</option></select></label>
    <div className="timing"><b>Event Timing</b><label><input type="checkbox" defaultChecked/> Pre-game</label><label><input type="checkbox" defaultChecked/> Live</label></div>
   </aside>
   <section className="command-main">
    <div className="command-heading"><div><h1>ARB Command Center</h1><p>Real-time sports arbitrage opportunities across top sportsbooks.</p></div><div className={'connection '+mode}><i/>{mode==='live'?'Live Data':mode==='loading'?'Connecting':'Unavailable'}</div></div>
    <div className="kpis"><div><b>⚡ {rows.length}</b><span>Live Opportunities</span></div><div><b>▥ {rows.length?Math.max(...rows.map(x=>x.roi)).toFixed(1):'0.0'}%</b><span>Best ROI</span></div><div><b>◷ {selected.age}s</b><span>Avg. Freshness</span></div><div><b>◉ {books.length-1} / 11</b><span>Books Online</span></div></div>
    <section className="feed">
    <div className="filters"><select value={sport} onChange={e=>setSport(e.target.value)}>{sports.map(x=><option key={x}>{x}</option>)}</select><select value={market} onChange={e=>setMarket(e.target.value)}>{markets.map(x=><option key={x}>{x}</option>)}</select><select value={book} onChange={e=>setBook(e.target.value)}>{books.map(x=><option key={x}>{x}</option>)}</select><label className="toggle"><input type="checkbox" checked={verifiedOnly} onChange={e=>setVerifiedOnly(e.target.checked)}/><span/>Verified Only</label><button className="sort" type="button" onClick={()=>setSortDesc(v=>!v)}>ROI {sortDesc?'High → Low':'Low → High'}</button><div className={'connection '+mode}><i/>{mode==='live'?'Live':mode==='loading'?'Connecting':'Unavailable'}{mode==='live'&&updatedAt?' · refreshed just now':''}</div></div>
    <div className="head"><span>SPORT / MARKET</span><span>EVENT</span><span>SPORTSBOOKS (ODDS)</span><span>ROI</span><span>STATUS</span><span>LAST UPDATE</span></div>
    <div className="rows">{filtered.length===0?<div className="empty">No opportunities match these filters.</div>:filtered.map(o=><button key={o.id} className={'row '+(selected.id===o.id?'selected':'')} onClick={()=>{setSelected(o);if(typeof window!=='undefined'&&window.innerWidth<=980)setTimeout(()=>document.getElementById('arb-detail')?.scrollIntoView({behavior:'smooth',block:'start'}),0)}}>
     <span className="sport"><b>{o.sport}</b><small>{o.market}</small></span><span className="event"><b>{o.event.split(' vs ')[0]}</b><b>{o.event.split(' vs ')[1]||''}</b></span>
     <span className="books">{o.legs.slice(0,2).map((l,i)=><span key={i}><small>{l.book}</small><b>{american(l.odds)}</b></span>)}{o.legs.length>2&&<small>+{o.legs.length-2} more</small>}</span>
     <span className="roi">{o.roi>=0?'+':''}{o.roi.toFixed(2)}%</span><span><em className={o.status==='VERIFIED'?'verified':'held'}>{o.status==='VERIFIED'?'VERIFIED':'HELD BACK'}</em></span><span className="age">{o.age}s ago　›</span>
    </button>)}</div>
   </section>
   </section>
   <aside className={'detail '+(rows.length?'':'hidden')} id="arb-detail">
    <div className="detail-top"><div><small>{selected.sport}　·　{selected.market}</small><b>{selected.age}s ago <i/></b></div><div className="title-line"><h2>{selected.event}</h2><button className={'favorite '+(favorites.has(selected.id)?'on':'')} aria-label="Save opportunity" onClick={()=>setFavorites(prev=>{const next=new Set(prev);next.has(selected.id)?next.delete(selected.id):next.add(selected.id);return next})}>☆</button></div></div>
    <div className="roi-hero"><div><strong>{selected.roi>=0?'+':''}{selected.roi.toFixed(2)}%</strong><small>Recorded ROI</small></div><em className={mode==='preview'?'held':selected.status==='VERIFIED'?'verified':'held'}>● {mode==='preview'?'PREVIEW SAMPLE':selected.status==='VERIFIED'?'VERIFIED EXECUTABLE':'HELD BACK'}</em></div>
    <div className={'quote-pair '+(selected.legs.length>2?'multi':'')}>{selected.legs.map((l,i)=><div key={i}><small>{l.book}</small><span>{l.bet}</span><b>{american(l.odds)}</b></div>)}</div>
    <label className="stake">Total Stake<div><span>$</span><input aria-label="Total stake" inputMode="decimal" value={bankText} onChange={e=>setBankText(e.target.value.replace(/[^0-9.,]/g,''))}/></div></label>
    <div className="presets">{[50,100,500,1000,2500].map(n=><button key={n} className={bank===n?'on':''} onClick={()=>setBankText(String(n))}>{money(n).replace('.00','')}</button>)}</div>
    <div className={'allocations '+(selected.legs.length>2?'multi':'')}>{selected.legs.map((l,i)=><div key={i}><small>Bet on {l.bet}<br/>at {l.book}</small><strong>{money(calc.stakes[i]||0)}</strong><span>Potential Payout<br/>{money((calc.stakes[i]||0)*dec(l.odds))}</span>{Number.isFinite(l.limit)&&<span>Book limit {money(Number(l.limit))}</span>}</div>)}</div>
    <div className={'profit '+(!isArb?'invalid':'')}><div><span>{isArb?'Guaranteed Profit':'No guaranteed profit'}</span><strong>{isArb?money(calc.p):'—'}</strong></div><div><span>Calculated Return</span><strong>{calc.r.toFixed(2)}%</strong></div></div>
    {roiMismatch&&<div className="calc-warning">Displayed prices no longer reproduce the recorded ROI. Treat this opportunity as stale until refreshed.</div>}
    {overLimit&&<div className="calc-warning">Requested stake exceeds at least one known sportsbook limit. The requested amount was not silently changed.</div>}
    <button className="details-button" onClick={()=>setDetailOpen(v=>!v)}>{detailOpen?'Hide Market Details　↑':'View Full Market Details　→'}</button>
    {detailOpen&&<><div className="tabs">{['Market Details','Book Info','Settlement','Historical Odds'].map(x=><button onClick={()=>setTab(x)} className={tab===x?'active':''} key={x}>{x}</button>)}</div>
    <div className="evidence">{tab==='Market Details'?<>{mode==='live'?<><p>✓ Opportunity loaded from ARB audit history</p><p>✓ Displayed prices independently recalculated across all legs</p><p>✓ Status preserved from backend verification</p></>:<p>Preview data — not a live betting opportunity.</p>}</>:<p>{tab} information remains secondary to the betting workflow.</p>}{selected.reason&&<p className="reason">Holdback: {selected.reason}</p>}</div></>}
   </aside>
  </div>
  <section className={'execution-workspace '+(rows.length?'':'hidden')}>
   <div className="execution-head"><a href="#arb-detail">← Back to Opportunities</a><div><h2>{selected.event}</h2><span>{selected.sport} · {selected.market}</span></div><strong>{selected.roi.toFixed(1)}% ARB<small>Estimated ROI</small></strong><span>● Last updated<br/><b>{selected.age} seconds ago</b></span></div>
   <div className="execution-grid">
    <div className="leg-cards">{selected.legs.slice(0,2).map((l,i)=><div className={'leg '+(i?'blue':'green')} key={i}><small>Leg {i+1} · {l.book}</small><div><b>{l.bet}</b><strong>{american(l.odds)}</strong></div><div><span>Stake <b>{money(calc.stakes[i]||0)}</b></span><span>To Win <b>{money((calc.stakes[i]||0)*(dec(l.odds)-1))}</b></span><span>Total Payout <b>{money((calc.stakes[i]||0)*dec(l.odds))}</b></span></div><button type="button">Open at {l.book} →</button></div>)}</div>
    <div className="history-card"><b>Opportunity History</b><div className="spark"><i/><i/><i/><i/><i/><i/></div><small>12PM　 4PM　 8PM　 12AM　 4AM　 8AM</small></div>
    <div className="movement-card"><b>Market Movement</b>{selected.legs.slice(0,2).map((l,i)=><p key={i}><span>● {Math.max(1,selected.age+i*8)}s ago</span><strong>{american(l.odds)}</strong></p>)}</div>
   </div>
   <div className="execution-bottom"><div className="mini-calc"><b>⚠ Arbitrage Calculator</b><div><label>Total Stake<input value={bankText} onChange={e=>setBankText(e.target.value.replace(/[^0-9.,]/g,''))}/></label><label>Odds Format<select><option>American</option></select></label><span>Guaranteed Profit<strong>{money(calc.p)}</strong></span><span>ROI<strong>{calc.r.toFixed(1)}%</strong></span></div></div><div className="steps"><b>Execution Steps</b><ol><li>Verify both prices</li><li>Open first book</li><li>Recheck second price</li><li>Complete second leg</li></ol></div></div>
   <div className="safety-strip">⚠ Verify prices before placing either leg. InQsi does not place wagers. Odds can change at any time.</div>
  </section>
  <nav className="mobile-nav"><a className="active">▣<small>Arb</small></a><a href="/arbitrage-v2/calculator">▦<small>Calculator</small></a><a href="/sports">⚑<small>Picks</small></a><a href="/parlay-scanner">•••<small>More</small></a></nav>
 </main>
}