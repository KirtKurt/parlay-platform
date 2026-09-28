'use client';
import {useMemo,useState} from 'react';
import '../arb-v2.css';

const parseAmerican=(raw:string)=>{
 const v=raw.trim();
 if(!/^[+-]\d+$/.test(v)) return 0;
 const n=Number(v);
 return Number.isFinite(n)&&Math.abs(n)>=100?n:0;
};
const dec=(a:number)=>Math.abs(a)>=100?(a>0?1+a/100:1+100/Math.abs(a)):0;
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number.isFinite(n)?n:0);

export default function CalculatorPage(){
 const[stake,setStake]=useState('1000');
 const[count,setCount]=useState<2|3>(2);
 const[odds,setOdds]=useState(['+115','-102','+250']);
 const total=Math.max(0,Number(stake.replace(/,/g,''))||0);
 const active=odds.slice(0,count).map(parseAmerican);
 const c=useMemo(()=>{
  const ds=active.map(dec);
  if(!total||ds.some(d=>!d))return{stakes:active.map(()=>0),p:0,r:0,payout:0,valid:false};
  const inv=ds.map(d=>1/d),sum=inv.reduce((a,b)=>a+b,0),stakes=inv.map(v=>total*v/sum),payout=total/sum,p=payout-total;
  return{stakes,p,r:100*p/total,payout,valid:true};
 },[total,count,odds.join('|')]);
 const update=(i:number,v:string)=>{
  let clean=v.replace(/[^0-9+-]/g,'');
  const sign=clean.includes('-')?'-':clean.includes('+')?'+':'';
  clean=sign+clean.replace(/[+-]/g,'');
  setOdds(prev=>prev.map((x,n)=>n===i?clean:x));
 };
 return <main className="arb2 standalone"><header className="top"><a className="brand" href="/arbitrage-v2"><i/>InQsi</a><nav><a href="/arbitrage-v2">▣ Arbitrage</a><a className="active">▦ Calculator</a><a href="/sports">⚑ Picks</a><a href="/parlay-scanner">☷ Slip Scanner</a></nav></header>
 <section className="standalone-card"><p className="eyebrow">ARB CALCULATOR</p><h1>Enter any stake and signed American odds</h1><p className="intro">Enter the + or − shown by the sportsbook for every side (for example +150 or -120). Nothing here places a wager.</p>
 <div className="calc-mode"><button className={count===2?'on':''} onClick={()=>setCount(2)}>2-way</button><button className={count===3?'on':''} onClick={()=>setCount(3)}>3-way</button></div>
 <label className="stake">Total Stake<div><span>$</span><input aria-label="Total stake" inputMode="decimal" value={stake} onChange={e=>setStake(e.target.value.replace(/[^0-9.,]/g,''))}/></div></label>
 <div className={'manual-odds '+(count===3?'three':'')}>{odds.slice(0,count).map((v,i)=><label key={i}>Side {String.fromCharCode(65+i)}<input aria-label={`Side ${String.fromCharCode(65+i)} American odds`} inputMode="text" placeholder="+150 or -120" value={v} onChange={e=>update(i,e.target.value)}/></label>)}</div>
 {!c.valid&&<div className="calc-warning">Include + or - and enter valid American odds (absolute value 100 or greater) for every side.</div>}
 <div className={'allocations '+(count===3?'multi':'')}>{c.stakes.map((x,i)=><div key={i}><small>Stake on Side {String.fromCharCode(65+i)}</small><strong>{money(x)}</strong><span>Potential Payout<br/>{money(x*dec(active[i]))}</span></div>)}</div>
 <div className={'profit '+(c.p<=0?'invalid':'')}><div><span>{c.p>0?'Guaranteed Profit':'No guaranteed profit'}</span><strong>{c.p>0?money(c.p):'—'}</strong></div><div><span>Return</span><strong>{c.r.toFixed(2)}%</strong></div></div>
 </section></main>;
}
