const dec=a=>a>0?1+a/100:a<0?1+100/Math.abs(a):0;
const calc=(odds,bank)=>{const ds=odds.map(dec);if(!bank||ds.some(d=>!d))return null;const inv=ds.map(d=>1/d),sum=inv.reduce((a,b)=>a+b,0),stakes=inv.map(v=>bank*v/sum),payout=bank/sum;return{stakes,payout,profit:payout-bank,roi:100*(payout-bank)/bank};};
const near=(a,b,e=0.01)=>{if(Math.abs(a-b)>e)throw new Error(`${a} != ${b}`)};
for(const odds of [[115,102],[245,360,250]]){const r=calc(odds,1000);if(!r)throw new Error('calculation failed');near(r.stakes.reduce((a,b)=>a+b,0),1000);const payouts=r.stakes.map((s,i)=>s*dec(odds[i]));payouts.forEach(p=>near(p,r.payout));if(!(r.profit>0))throw new Error('expected positive arbitrage');}
if(calc([0,110],1000)!==null)throw new Error('zero odds must fail closed');
console.log('ARB math checks passed');
