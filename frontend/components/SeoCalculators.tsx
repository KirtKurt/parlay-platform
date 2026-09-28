'use client';
import {useMemo,useState} from 'react';
import Link from 'next/link';

const americanToDecimal=(a:number)=>a>0?1+a/100:1+100/Math.abs(a);
const implied=(a:number)=>a>0?100/(a+100):Math.abs(a)/(Math.abs(a)+100);
const parse=(v:string)=>{const n=Number(v);return Number.isFinite(n)&&Math.abs(n)>=100?n:0};
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(Number.isFinite(n)?n:0);

export function ProbabilityCalculator(){
 const[o,setO]=useState('-110'); const a=parse(o); const p=a?implied(a)*100:0; const d=a?americanToDecimal(a):0;
 return <Tool title="Implied Probability Calculator"><Field label="American odds" value={o} set={setO}/><Result rows={[['Implied probability',p?p.toFixed(2)+'%':'—'],['Decimal odds',d?d.toFixed(3):'—']]}/></Tool>
}
export function AmericanCalculator(){
 const[o,setO]=useState('+150'); const[s,setS]=useState('100'); const a=parse(o),stake=Number(s)||0,d=a?americanToDecimal(a):0;
 return <Tool title="American Odds Calculator"><Field label="American odds" value={o} set={setO}/><Field label="Stake" value={s} set={setS}/><Result rows={[['Decimal odds',d?d.toFixed(3):'—'],['Implied probability',a?(implied(a)*100).toFixed(2)+'%':'—'],['Potential payout',d?money(stake*d):'—']]}/></Tool>
}
export function VigCalculator(){
 const[a,setA]=useState('-110'),[b,setB]=useState('-110'); const x=parse(a),y=parse(b),pa=x?implied(x):0,pb=y?implied(y):0,sum=pa+pb;
 return <Tool title="Sportsbook Vig Calculator"><Field label="Outcome A" value={a} set={setA}/><Field label="Outcome B" value={b} set={setB}/><Result rows={[['Combined implied probability',sum?(sum*100).toFixed(2)+'%':'—'],['Overround / hold',sum?((sum-1)*100).toFixed(2)+'%':'—'],['No-vig A',sum?(pa/sum*100).toFixed(2)+'%':'—'],['No-vig B',sum?(pb/sum*100).toFixed(2)+'%':'—']]}/></Tool>
}
export function SurebetCalculator(){
 const[a,setA]=useState('+110'),[b,setB]=useState('+105'),[s,setS]=useState('1000'); const x=parse(a),y=parse(b),stake=Number(s)||0;
 const calc=useMemo(()=>{if(!x||!y||!stake)return null;const da=americanToDecimal(x),db=americanToDecimal(y),sum=1/da+1/db,payout=stake/sum;return{sum,payout,sa:stake*(1/da)/sum,sb:stake*(1/db)/sum}},[x,y,stake]);
 return <Tool title="Surebet Calculator"><Field label="Outcome A" value={a} set={setA}/><Field label="Outcome B" value={b} set={setB}/><Field label="Total stake" value={s} set={setS}/>{calc&&<Result rows={[['Arbitrage?',calc.sum<1?'Yes':'No'],['Combined implied probability',(calc.sum*100).toFixed(2)+'%'],['Stake A',money(calc.sa)],['Stake B',money(calc.sb)],['Balanced payout',money(calc.payout)],['Calculated profit',money(calc.payout-stake)]]}/>}</Tool>
}
export function MiddleCalculator(){
 const[low,setLow]=useState('-3.5'),[high,setHigh]=useState('+4.5'); const l=Number(low),h=Number(high),width=Number.isFinite(l)&&Number.isFinite(h)?h+l:0;
 return <Tool title="Middle Calculator"><label>Favorite spread<input value={low} onChange={e=>setLow(e.target.value)}/></label><label>Opposing spread<input value={high} onChange={e=>setHigh(e.target.value)}/></label><Result rows={[['Interval between lines',width>0?width.toFixed(1)+' points':'No positive middle'],['Reminder','A middle is not a guaranteed arbitrage. Prices and settlement still matter.']]}/></Tool>
}
function Field({label,value,set}:{label:string,value:string,set:(v:string)=>void}){return <label>{label}<input inputMode="text" value={value} onChange={e=>set(e.target.value.replace(/[^0-9+-.]/g,''))}/></label>}
function Result({rows}:{rows:[string,string][]}){return <div style={{marginTop:20}}>{rows.map(([k,v])=><p key={k}><strong>{k}:</strong> {v}</p>)}</div>}
function Tool({title,children}:{title:string;children:React.ReactNode}){return <section className="standalone-card" style={{maxWidth:760,margin:'32px auto'}}><p className="eyebrow">FREE INQSI TOOL</p><h1>{title}</h1><p>Educational calculation only. This tool does not place wagers or guarantee executable sportsbook prices.</p><div className="manual-odds" style={{marginTop:20}}>{children}</div><p style={{marginTop:24}}><Link href="/learn/arbitrage">Explore the InQsi arbitrage learning center →</Link></p></section>}
