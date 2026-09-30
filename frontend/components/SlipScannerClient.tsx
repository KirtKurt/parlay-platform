'use client';

import { useEffect, useMemo, useState } from 'react';

type BookQuote={book:string;homeMl?:number;awayMl?:number;homeSpread?:number;awaySpread?:number;homeSpreadPrice?:number;awaySpreadPrice?:number;totalPoint?:number;overPrice?:number;underPrice?:number};
type BoardGame={key:string;sport:string;away:string;home:string;matchup:string;books:BookQuote[]};
type Draft={sport:string;gameKey:string;market:string;selection:string;book:string};
type SavedPick=Draft&{id:string;matchup:string;displaySelection:string;odds:string;line:string};

const blank:Draft={sport:'',gameKey:'',market:'moneyline',selection:'',book:''};
const markets=[{value:'moneyline',label:'Moneyline'},{value:'spread',label:'Spread'},{value:'total',label:'Total'}];

function n(value:unknown){const out=Number(value);return Number.isFinite(out)?out:undefined}
function american(value?:number){if(!Number.isFinite(value))return 'Waiting';return Number(value)>0?`+${value}`:String(value)}
function normSport(value?:string){
 const raw=String(value||'').toLowerCase();
 if(raw.includes('baseball')||raw==='mlb')return'MLB';
 if(raw.includes('ncaaf')||raw==='cfb')return'CFB';
 if(raw.includes('nfl')||raw.includes('americanfootball_nfl'))return'NFL';
 if(raw.includes('wnba'))return'WNBA';
 if(raw.includes('ncaab')||raw==='ncaam')return'NCAAM';
 if(raw.includes('nba')||raw.includes('basketball_nba'))return'NBA';
 if(raw.includes('nhl')||raw.includes('hockey'))return'NHL';
 if(raw.includes('soccer')||raw.includes('epl')||raw.includes('mls'))return'Soccer';
 if(raw.includes('tennis'))return'Tennis';
 return raw?raw.toUpperCase():'';
}

function parseBoard(payload:any):BoardGame[]{
 const boards=Array.isArray(payload?.boards)?payload.boards:[]; const games:BoardGame[]=[];
 for(const board of boards){
  const sport=normSport(board?.sport||board?.providerSportKey);
  for(const game of board?.games||[]){
   const away=String(game.awayTeam||game.away_team||'').trim(),home=String(game.homeTeam||game.home_team||'').trim();
   if(!away||!home)continue;
   const books:BookQuote[]=[]; const seen=new Set<string>();
   for(const book of game.books||[]){
    const name=String(book.book||book.bookmaker||'').trim(); if(!name||seen.has(name))continue; seen.add(name);
    books.push({book:name,homeMl:n(book?.moneyline?.home),awayMl:n(book?.moneyline?.away),homeSpread:n(book?.spread?.home_point),awaySpread:n(book?.spread?.away_point),homeSpreadPrice:n(book?.spread?.home_price),awaySpreadPrice:n(book?.spread?.away_price),totalPoint:n(book?.total?.over_point??book?.total?.point),overPrice:n(book?.total?.over_price),underPrice:n(book?.total?.under_price)});
   }
   games.push({key:`${sport}|${away}|${home}`,sport,away,home,matchup:`${away} @ ${home}`,books});
  }
 }
 const unique=new Map<string,BoardGame>();
 for(const game of games){const prior=unique.get(game.key);if(!prior){unique.set(game.key,game);continue;}const seen=new Set(prior.books.map(b=>b.book));for(const book of game.books)if(!seen.has(book.book))prior.books.push(book);}
 return Array.from(unique.values());
}

function oddsFor(book:BookQuote,game:BoardGame,draft:Draft){
 if(draft.market==='total')return draft.selection==='Under'?book.underPrice:book.overPrice;
 const away=draft.selection===game.away;
 if(draft.market==='spread')return away?book.awaySpreadPrice:book.homeSpreadPrice;
 return away?book.awayMl:book.homeMl;
}

function pointFor(book:BookQuote,game:BoardGame,draft:Draft){
 if(draft.market==='total')return book.totalPoint;
 if(draft.market==='spread')return draft.selection===game.away?book.awaySpread:book.homeSpread;
 return undefined;
}

function bestBookFor(game:BoardGame|undefined,draft:Draft){
 if(!game||!draft.selection)return'';
 const priced=game.books.filter(book=>Number.isFinite(oddsFor(book,game,draft)));
 if(!priced.length)return'';
 const refPoint=pointFor(priced[0],game,draft);
 return priced.reduce<{book:string;odds:number}|null>((best,book)=>{
  if(draft.market!=='moneyline'&&pointFor(book,game,draft)!==refPoint)return best;
  const odds=Number(oddsFor(book,game,draft));
  return !best||odds>best.odds?{book:book.book,odds}:best;
 },null)?.book||'';
}

function quote(game:BoardGame|undefined,draft:Draft){
 if(!game)return{display:'',odds:'Waiting',line:''};
 const book=game.books.find(b=>b.book===draft.book)||game.books[0];
 if(!book)return{display:draft.selection,odds:'Waiting',line:''};
 if(draft.market==='total'){
  const over=draft.selection==='Over',point=book.totalPoint;
  return{display:`${draft.selection} ${point??''}`.trim(),odds:american(over?book.overPrice:book.underPrice),line:point!=null?String(point):''};
 }
 const away=draft.selection===game.away;
 if(draft.market==='spread'){
  const point=away?book.awaySpread:book.homeSpread,price=away?book.awaySpreadPrice:book.homeSpreadPrice;
  return{display:`${draft.selection} ${point??''}`.trim(),odds:american(price),line:point!=null?String(point):''};
 }
 return{display:draft.selection,odds:american(away?book.awayMl:book.homeMl),line:''};
}

export function SlipScannerClient(){
 const[games,setGames]=useState<BoardGame[]>([]);
 const[mode,setMode]=useState<'loading'|'live'|'waiting'>('loading');
 const[draft,setDraft]=useState<Draft>({...blank});
 const[picks,setPicks]=useState<SavedPick[]>([]);
 const[state,setState]=useState<{loading:boolean;error?:string;result?:any}>({loading:false});

 useEffect(()=>{let active=true;fetch('/v1/inqsi/markets/board',{cache:'no-store'}).then(r=>r.json()).then(payload=>{if(!active)return;const found=parseBoard(payload);setGames(found);setMode(found.length?'live':'waiting');}).catch(()=>{if(active)setMode('waiting')});return()=>{active=false}},[]);

 const sports=useMemo(()=>Array.from(new Set(games.map(g=>g.sport))).sort(),[games]);
 const sportGames=useMemo(()=>draft.sport?games.filter(g=>g.sport===draft.sport):[],[games,draft.sport]);
 const game=games.find(g=>g.key===draft.gameKey);
 const selections=!game?[]:draft.market==='total'?['Over','Under']:[game.away,game.home];
 const bestBook=bestBookFor(game,draft);
 const effectiveDraft={...draft,book:draft.book||bestBook};
 const currentQuote=quote(game,effectiveDraft);
 const ready=Boolean(game&&draft.selection&&effectiveDraft.book&&currentQuote.odds!=='Waiting');
 const stage=!draft.sport?1:!draft.gameKey?2:!draft.selection?3:3;

 function patch(p:Partial<Draft>){
  setDraft(current=>{
   const next={...current,...p};
   if(p.sport!==undefined&&p.sport!==current.sport){next.gameKey='';next.selection='';next.book='';}
   if(p.gameKey!==undefined&&p.gameKey!==current.gameKey){next.selection='';next.book='';}
   if(p.market!==undefined&&p.market!==current.market){next.selection='';next.book='';}
   if(p.selection!==undefined){next.book='';}
   return next;
  });
 }

 function addPick(){
  if(!ready||!game||picks.length>=3)return;
  const id=`${game.key}|${draft.market}|${draft.selection}`;
  if(picks.some(p=>p.id===id)){setState({loading:false,error:'That selection is already in your scan.'});return;}
  setPicks(rows=>[...rows,{...effectiveDraft,id,matchup:game.matchup,displaySelection:currentQuote.display,odds:currentQuote.odds,line:currentQuote.line}]);
  setDraft({...blank});
  setState({loading:false});
 }

 async function runScan(){
  if(!picks.length){setState({loading:false,error:'Add at least one selection before scanning.'});return;}
  const payload=picks.map(p=>{
   const g=games.find(x=>x.key===p.gameKey);
   return{sport:p.sport,marketType:p.market,selection:p.displaySelection,book:p.book,oddsAmerican:p.odds,line:p.line,matchup:p.matchup,marketSnapshots:g?g.books.map(book=>{const d={...p,book:book.book};const q=quote(g,d);return{book:book.book,oddsAmerican:q.odds,line:q.line,observedAt:new Date().toISOString(),source:'LIVE_MARKET_BOARD'};}).filter(x=>x.oddsAmerican!=='Waiting'):[]};
  });
  setState({loading:true});
  try{
   const response=await fetch('/v1/scanner/scan',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({legs:payload,save:false})});
   const data=await response.json().catch(()=>({}));
   if(!response.ok)throw new Error(data.error||data.message||'Scanner intelligence is not ready for this selection.');
   setState({loading:false,result:data});
  }catch(error){setState({loading:false,error:error instanceof Error?error.message:'Scanner intelligence is not ready.'});}
 }

 const scanResult=(state.result?.scan||state.result?.assessment||state.result)||null;
 const reads=Array.isArray(scanResult?.legReads)?scanResult.legReads:[];
 const overall=String(scanResult?.overallRead||scanResult?.risk_level||scanResult?.riskLevel||'Reviewed');
 const score=scanResult?.overallScore??scanResult?.risk_score??scanResult?.riskScore;

 return <section className="scanner-grid">
  <div className="scanner-card">
   <div className="scanner-steps" aria-label="Selection workflow">
    {['Sport','Game','Pick'].map((label,index)=><div className={'scanner-step '+(stage>index+1?'done':stage===index+1?'active':'')} key={label}><i>{index+1}</i><span>{label}</span></div>)}
   </div>
   {mode!=='live'&&<div className="scanner-sync"><b>{mode==='loading'?'Connecting to live board':'Live odds temporarily unavailable'}</b><span>The scanner is fully wired. Sport, game and price selections will populate automatically when the provider feed returns.</span></div>}
   <div className="scanner-form">
    <label className="scanner-field"><span>Sport</span><select value={draft.sport} onChange={e=>patch({sport:e.target.value})} disabled={!sports.length}><option value="">Select sport</option>{sports.map(s=><option key={s}>{s}</option>)}</select></label>
    <label className="scanner-field"><span>Game</span><select value={draft.gameKey} onChange={e=>patch({gameKey:e.target.value})} disabled={!draft.sport}><option value="">Select game</option>{sportGames.map(g=><option key={g.key} value={g.key}>{g.matchup}</option>)}</select></label>
    <label className="scanner-field"><span>Market</span><select value={draft.market} onChange={e=>patch({market:e.target.value})} disabled={!game}><option value="moneyline">Moneyline</option><option value="spread">Spread</option><option value="total">Total</option></select></label>
    <label className="scanner-field"><span>Selection</span><select value={draft.selection} onChange={e=>patch({selection:e.target.value})} disabled={!game}><option value="">Select side</option>{selections.map(s=><option key={s}>{s}</option>)}</select></label>
    {ready&&<div className="scanner-current"><small>{game?.matchup} · {draft.market==='total'?'Total':draft.market==='spread'?'Spread':'Moneyline'}</small><strong>{currentQuote.display}</strong><b>{currentQuote.odds} · {effectiveDraft.book}</b></div>}
    <div className="scanner-actions"><button className="scanner-add" type="button" disabled={!ready||picks.length>=3} onClick={addPick}>{picks.length?'Add Another Selection':'Add Selection'}</button><button className="scanner-scan" type="button" disabled={!picks.length||state.loading} onClick={runScan}>{state.loading?'Scanning…':'Scan My Picks →'}</button></div>
   </div>
   {picks.length>0&&<div className="scanner-selected">{picks.map((pick,index)=><article key={pick.id}><div><small>{index+1} · {pick.sport} · {pick.matchup}</small><strong>{pick.displaySelection}</strong><b>{pick.odds} · {pick.book}</b></div><button aria-label="Remove selection" onClick={()=>setPicks(rows=>rows.filter(x=>x.id!==pick.id))}>×</button></article>)}</div>}
   {state.error&&<div className="mock-error">{state.error}</div>}
  </div>
  <div className="scanner-card scanner-result">
   <div className="scanner-result-head"><div><span className="mockup-eyebrow">Slip Scanner</span><h2>Your Analysis</h2></div>{picks.length>0&&<small>{picks.length} selection{picks.length===1?'':'s'}</small>}</div>
   {!scanResult?<div className="scanner-no-result"><div><b>Find the risk before you bet.</b><p>Choose a sport, game and selection. Add up to three picks if you want to scan a small slip. InQsi will show concise risk first, with deeper market and model details only when qualified data exists.</p></div></div>:<>
    <div className="scanner-risk-banner"><div><span>Overall risk</span><strong>{overall}</strong></div>{score!=null&&<strong>{score}/100</strong>}</div>
    <div className="scanner-result-list">{reads.length?reads.map((read:any,index:number)=>{const risk=String(read?.risk||read?.read||'low').toLowerCase();const cls=risk.includes('high')?'high':risk.includes('moderate')||risk.includes('elevated')?'moderate':'low';return <article className={'scanner-result-row '+cls} key={index}><header><strong>{read?.selection||picks[index]?.displaySelection||`Selection ${index+1}`}</strong><span className={'risk-pill '+cls}>{String(read?.risk||read?.read||'LOW RISK').toUpperCase()}</span></header><p>{read?.summary||read?.explanation||'InQsi reviewed the available market and selection context.'}</p></article>}):<article className="scanner-result-row"><header><strong>{picks[0]?.displaySelection||'Selection reviewed'}</strong><span className="risk-pill low">REVIEWED</span></header><p>{scanResult?.consumer_message||scanResult?.message||'InQsi completed the available risk review for this selection.'}</p></article>}</div>
   </>}
  </div>
 </section>;
}
