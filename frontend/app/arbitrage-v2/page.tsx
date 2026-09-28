'use client';
import {useEffect,useMemo,useState} from 'react';
import './arb-v2.css';

type Status='VERIFIED'|'HELD';
type O={id:string;sport:string;event:string;market:string;roi:number;age:number;aBook:string;aBet:string;aOdds:number;bBook:string;bBet:string;bOdds:number;status:Status;reason?:string};
const demo:O[]=[
{id:'1',sport:'MLB',event:'Yankees vs Red Sox',market:'Moneyline',roi:2.34,age:4,aBook:'DraftKings',aBet:'Yankees',aOdds:115,bBook:'FanDuel',bBet:'Red Sox',bOdds:102,status:'VERIFIED'},
{id:'2',sport:'NBA',event:'Lakers vs Warriors',market:'Moneyline',roi:1.98,age:7,aBook:'BetMGM',aBet:'Lakers',aOdds:120,bBook:'Caesars',bBet:'Warriors',bOdds:-110,status:'VERIFIED'},
{id:'3',sport:'NFL',event:'Chiefs vs Bills',market:'Spread',roi:1.72,age:11,aBook:'FanDuel',aBet:'Chiefs -2.5',aOdds:108,bBook:'DraftKings',bBet:'Bills +3.0',bOdds:110,status:'VERIFIED'},
{id:'4',sport:'NHL',event:'Oilers vs Canucks',market:'Total',roi:1.61,age:15,aBook:'bet365',aBet:'Over 6.5',aOdds:-120,bBook:'DraftKings',bBet:'Under 6.5',bOdds:100,status:'VERIFIED'},
{id:'5',sport:'NCAAB',event:'UConn vs Marquette',market:'Moneyline',roi:1.48,age:22,aBook:'Caesars',aBet:'UConn',aOdds:105,bBook:'FanDuel',bBet:'Marquette',bOdds:-118,status:'HELD',reason:'Settlement verification pending'},
{id:'6',sport:'MLB',event:'Dodgers vs Padres',market:'Total',roi:1.32,age:27,aBook:'DraftKings',aBet:'Over 8.5',aOdds:-115,bBook:'BetMGM',bBet:'Under 8.5',bOdds:95,status:'VERIFIED'}];
const dec=(a:number)=>a>=100?1+a/100:a>0?1+a/100:1+100/Math.abs(a);
const american=(n:number)=>n>0?`+${n}`:String(n);
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number.isFinite(n)?n:0);

export default function Page(){
 const[selected,setSelected]=useState<O>(demo[0]); const[rows,setRows]=useState<O[]>(demo); const[mode,setMode]=useState<'loading'|'live'|'preview'|'unavailable'>('loading');
 const[bankText,setBankText]=useState('1000'); const[sport,setSport]=useState('All Sports'); const[market,setMarket]=useState('All Markets'); const[verifiedOnly,setVerifiedOnly]=useState(false); const[tab,setTab]=useState('Market Details');
 useEffect(()=>{fetch('/v1/inqsi/arbitrage/history',{cache:'no-store'}).then(async r=>{const data=await r.json(); if(!r.ok||data.mode!=='live'){setMode('preview');return} const found:O[]=[]; for(const scan of (Array.isArray(data.history)?data.history:[])){const p=scan?.payload||scan?.data||scan||{}; for(const [key,status] of [['hits','VERIFIED'],['detected_unverified','HELD']] as const){for(const row of (p[key]||[])){const legs=row.legs||row.quotes||[];if(legs.length<2)continue;const odds=(x:any)=>Number(x?.american??x?.american_odds??x?.price??x?.odds??0); found.push({id:String(row.market_id||row.event_id||row.event||found.length),sport:String(row.sport||p.sport||'Sport').toUpperCase(),event:String(row.event||row.event_id||'Market opportunity'),market:String(row.market||'Market'),roi:Number(row.margin_pct??row.margin??row.edge??row.arb_pct??0),age:0,aBook:String(legs[0]?.book||legs[0]?.bookmaker||'Book 1'),aBet:String(legs[0]?.outcome||legs[0]?.name||'Side 1'),aOdds:odds(legs[0]),bBook:String(legs[1]?.book||legs[1]?.bookmaker||'Book 2'),bBet:String(legs[1]?.outcome||legs[1]?.name||'Side 2'),bOdds:odds(legs[1]),status,reason:row?.validation?.settlement_reason||row?.reason});}}} if(found.length){found.sort((a,b)=>b.roi-a.roi);setRows(found.slice(0,50));setSelected(found[0]);} setMode('live')}).catch(()=>setMode('preview'));},[]);
 const bank=Math.max(0,Number(bankText.replace(/,/g,''))||0);
 const calc=useMemo(()=>{const d1=dec(selected.aOdds),d2=dec(selected.bOdds),x=bank*d2/(d1+d2),y=bank-x,p=Math.min(x*d1,y*d2)-bank;return{x,y,p,r:bank?100*p/bank:0,payout:bank+p}},[selected,bank]);\n const isArb=calc.p>0; const roiMismatch=mode==='live'&&Math.abs(calc.r-selected.roi)>.15;
 const filtered=rows.filter(o=>(sport==='All Sports'||o.sport===sport)&&(market==='All Markets'||o.market===market)&&(!verifiedOnly||o.status==='VERIFIED'));
 const sports=['All Sports',...Array.from(new Set(rows.map(x=>x.sport)))]; const markets=['All Markets',...Array.from(new Set(rows.map(x=>x.market)))];
 return <main className="arb2">
  <header className="top"><a className="brand" href="/"><i/>InQsi</a><nav><a className="active">▣ Arbitrage</a><a>▦ Calculator</a><a href="/sports">⚑ Picks</a><a href="/parlay-scanner">☷ Slip Scanner</a></nav><div className="search">⌕ <span>Search teams, sports, or books...</span></div><button className="avatar">JK</button></header>
  <div className="workspace">
   <section className="feed">
    <div className="filters"><select value={sport} onChange={e=>setSport(e.target.value)}>{sports.map(x=><option key={x}>{x}</option>)}</select><select value={market} onChange={e=>setMarket(e.target.value)}>{markets.map(x=><option key={x}>{x}</option>)}</select><select><option>All Books</option></select><label className="toggle"><input type="checkbox" checked={verifiedOnly} onChange={e=>setVerifiedOnly(e.target.checked)}/><span/>Verified Only</label><button className="sort">Sort by ROI⌄</button><div className={'connection '+mode}><i/>{mode==='live'?'Live':mode==='loading'?'Connecting':'Preview'} · {selected.age}s ago</div></div>
    <div className="head"><span>SPORT / MARKET</span><span>EVENT</span><span>SPORTSBOOKS (ODDS)</span><span>ROI</span><span>STATUS</span><span>LAST UPDATE</span></div>
    <div className="rows">{filtered.map(o=><button key={o.id} className={'row '+(selected.id===o.id?'selected':'')} onClick={()=>setSelected(o)}>
     <span className="sport"><b>{o.sport}</b><small>{o.market}</small></span><span className="event"><b>{o.event.split(' vs ')[0]}</b><b>{o.event.split(' vs ')[1]||''}</b></span>
     <span className="books"><span><small>{o.aBook}</small><b>{american(o.aOdds)}</b></span><span><small>{o.bBook}</small><b>{american(o.bOdds)}</b></span></span>
     <span className="roi">+{o.roi.toFixed(2)}%</span><span><em className={o.status==='VERIFIED'?'verified':'held'}>{o.status==='VERIFIED'?'VERIFIED':'HELD BACK'}</em></span><span className="age">{o.age}s ago　›</span>
    </button>)}</div>
   </section>
   <aside className="detail">
    <div className="detail-top"><div><small>{selected.sport}　·　{selected.market}</small><b>{selected.age}s ago <i/></b></div><h2>{selected.event}</h2></div>
    <div className="roi-hero"><div><strong>+{selected.roi.toFixed(2)}%</strong><small>Estimated ROI</small></div><em className={selected.status==='VERIFIED'?'verified':'held'}>● {selected.status==='VERIFIED'?'VERIFIED EXECUTABLE':'HELD BACK'}</em></div>
    <div className="quote-pair"><div><small>{selected.aBook}</small><span>{selected.aBet}</span><b>{american(selected.aOdds)}</b></div><i>vs</i><div><small>{selected.bBook}</small><span>{selected.bBet}</span><b>{american(selected.bOdds)}</b></div></div>
    <label className="stake">Total Stake<div><span>$</span><input aria-label="Total stake" inputMode="decimal" value={bankText} onChange={e=>setBankText(e.target.value.replace(/[^0-9.,]/g,''))}/></div></label>
    <div className="presets">{[50,100,500,1000,2500].map(n=><button key={n} className={bank===n?'on':''} onClick={()=>setBankText(String(n))}>{money(n).replace('.00','')}</button>)}</div>
    <div className="allocations"><div><small>Bet on {selected.aBet}<br/>at {selected.aBook}</small><strong>{money(calc.x)}</strong><span>Potential Payout<br/>{money(calc.x*dec(selected.aOdds))}</span></div><div><small>Bet on {selected.bBet}<br/>at {selected.bBook}</small><strong>{money(calc.y)}</strong><span>Potential Payout<br/>{money(calc.y*dec(selected.bOdds))}</span></div></div>
    <div className={'profit '+(!isArb?'invalid':'')}><div><span>{isArb?'Guaranteed Profit':'No guaranteed profit'}</span><strong>{isArb?money(calc.p):'—'}</strong></div><div><span>Calculated Return</span><strong>{calc.r.toFixed(2)}%</strong></div></div>{roiMismatch&&<div className="calc-warning">Displayed prices no longer reproduce the recorded ROI. Treat this opportunity as stale until refreshed.</div>}
    <button className="details-button">View Full Market Details　→</button>
    <div className="tabs">{['Market Details','Book Info','Settlement','Historical Odds'].map(x=><button onClick={()=>setTab(x)} className={tab===x?'active':''} key={x}>{x}</button>)}</div>
    <div className="evidence">{tab==='Market Details'?<>{mode==='live'?<><p>✓ Opportunity loaded from ARB audit history</p><p>✓ Displayed prices independently recalculated</p><p>✓ Status preserved from backend verification</p></>:<p>Preview data — not a live betting opportunity.</p>}</>:<p>{tab} information remains secondary to the betting workflow.</p>}{selected.reason&&<p className="reason">Holdback: {selected.reason}</p>}</div>
   </aside>
  </div>
  <nav className="mobile-nav"><a className="active">▣<small>Arb</small></a><a>▦<small>Calculator</small></a><a>⚑<small>Picks</small></a><a>•••<small>More</small></a></nav>
 </main>
}