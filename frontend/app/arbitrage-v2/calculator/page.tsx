'use client';
import {useMemo,useState} from 'react';
import {AppHeader} from '@/components/AppHeader';

const parseAmerican=(raw:string)=>{const v=raw.trim();if(!/^[+-]\d+$/.test(v))return 0;const n=Number(v);return Number.isFinite(n)&&Math.abs(n)>=100?n:0;};
const dec=(a:number)=>Math.abs(a)>=100?(a>0?1+a/100:1+100/Math.abs(a)):0;
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number.isFinite(n)?n:0);

export default function CalculatorPage(){
 const[stake,setStake]=useState('1000');
 const[count,setCount]=useState<2|3>(2);
 const[odds,setOdds]=useState(['+115','-102','+250']);
 const total=Math.max(0,Number(stake.replace(/,/g,''))||0);
 const active=odds.slice(0,count).map(parseAmerican);
 const calc=useMemo(()=>{
  const ds=active.map(dec);
  if(!total||ds.some(d=>!d))return{stakes:active.map(()=>0),p:0,r:0,payout:0,valid:false};
  const inv=ds.map(d=>1/d),sum=inv.reduce((a,b)=>a+b,0),stakes=inv.map(v=>total*v/sum),payout=total/sum,p=payout-total;
  return{stakes,p,r:100*p/total,payout,valid:true};
 },[total,count,odds.join('|')]);
 const update=(i:number,v:string)=>{let clean=v.replace(/[^0-9+-]/g,'');const sign=clean.includes('-')?'-':clean.includes('+')?'+':'';clean=sign+clean.replace(/[+-]/g,'');setOdds(prev=>prev.map((x,n)=>n===i?clean:x));};

 return <main className="mockup-site">
  <AppHeader active="calculator"/>
  <section className="calculator-approved">
   <article className="calculator-card">
    <span className="mockup-eyebrow">ARB Calculator</span>
    <h1>Enter any stake and signed American odds</h1>
    <p>Use the exact + or − price shown by each sportsbook. InQsi calculates the stake split, equalized payout, guaranteed profit and ROI. Nothing here places a wager.</p>
    <div className="calculator-mode"><button className={count===2?'on':''} onClick={()=>setCount(2)}>2-way</button><button className={count===3?'on':''} onClick={()=>setCount(3)}>3-way</button></div>
    <label className="calculator-stake">Total Stake<div className="calculator-input"><span>$</span><input aria-label="Total stake" inputMode="decimal" value={stake} onChange={e=>setStake(e.target.value.replace(/[^0-9.,]/g,''))}/></div></label>
    <div className={'calculator-odds '+(count===3?'three':'')}>{odds.slice(0,count).map((v,i)=><label key={i}>Side {String.fromCharCode(65+i)}<input aria-label={`Side ${String.fromCharCode(65+i)} American odds`} inputMode="text" placeholder="+150 or -120" value={v} onChange={e=>update(i,e.target.value)}/></label>)}</div>
    {!calc.valid&&<div className="calculator-warning">Include + or − and enter valid American odds with an absolute value of at least 100 for every side.</div>}
    <div className={'calculator-alloc '+(count===3?'three':'')}>{calc.stakes.map((x,i)=><div key={i}><small>Stake on Side {String.fromCharCode(65+i)}</small><strong>{money(x)}</strong><span>Potential Payout<br/>{money(x*dec(active[i]))}</span></div>)}</div>
    <div className="calculator-profit"><div><span>{calc.p>0?'Guaranteed Profit':'No guaranteed profit'}</span><strong>{calc.p>0?money(calc.p):'—'}</strong></div><div><span>Return</span><strong>{calc.r.toFixed(2)}%</strong></div></div>
   </article>
  </section>
 </main>;
}
