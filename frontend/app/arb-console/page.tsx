'use client';

import { useEffect, useMemo, useState } from 'react';

type Sport = { key: string; title?: string; group?: string };
type Leg = { outcome: string; book: string; american: number | string; stake?: number; last_update?: string; link?: string };
type Hit = { market_id: string; event: string; market: string; margin_pct: number; minimum_profit?: number; commence_time?: string; legs: Leg[]; validation?: { rules_status?: string } };
type Scan = { n_arbs?: number; n_detected_unverified?: number; n_markets?: number; hits?: Hit[]; detected_unverified?: Hit[]; status?: unknown };

const API = (process.env.NEXT_PUBLIC_ARB_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || '').replace(/\/$/, '');

function money(value?: number) { return typeof value === 'number' ? `$${value.toFixed(2)}` : '—'; }

export default function ArbConsolePage() {
  const [sports, setSports] = useState<Sport[]>([]);
  const [sport, setSport] = useState('baseball_mlb');
  const [bankroll, setBankroll] = useState(1000);
  const [mode, setMode] = useState<'featured'|'all'>('featured');
  const [scan, setScan] = useState<Scan | null>(null);
  const [health, setHealth] = useState<string>('Checking live ARB service…');
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<'verified'|'unverified'>('verified');

  useEffect(() => {
    if (!API) { setHealth('ARB API is not configured for this frontend deployment.'); return; }
    Promise.all([
      fetch(`${API}/v1/arb/health`, { cache: 'no-store' }).then(r => r.json()),
      fetch(`${API}/v1/arb/catalog`, { cache: 'no-store' }).then(r => r.json()),
    ]).then(([h, c]) => {
      setHealth(h?.ok ? `Live · ${h.version} · fail-closed settlement validation` : 'ARB service unavailable');
      if (Array.isArray(c?.sports)) setSports(c.sports);
    }).catch(() => setHealth('ARB service unavailable'));
  }, []);

  const rows = useMemo(() => tab === 'verified' ? (scan?.hits || []) : (scan?.detected_unverified || []), [scan, tab]);

  async function runScan() {
    if (!API || busy) return;
    setBusy(true); setScan(null);
    try {
      const q = new URLSearchParams({ sport, bankroll: String(bankroll), markets: mode === 'all' ? 'all' : 'h2h,spreads,totals' });
      const r = await fetch(`${API}/v1/arb/scan?${q}`, { cache: 'no-store' });
      const body = await r.json();
      setScan(body);
    } finally { setBusy(false); }
  }

  return (
    <main style={{maxWidth:1180,margin:'0 auto',padding:'24px 16px 72px'}}>
      <div style={{display:'flex',justifyContent:'space-between',gap:16,alignItems:'flex-start',flexWrap:'wrap'}}>
        <div><div style={{fontSize:12,fontWeight:800,letterSpacing:1.2,textTransform:'uppercase'}}>InQsi ARB</div><h1 style={{margin:'6px 0',fontSize:34}}>Live arbitrage console</h1><p style={{maxWidth:720,color:'#666',margin:0}}>Cross-book mathematical detection with market identity, source timestamps, settlement qualification and exact stake analysis. InQsi never labels an unreviewed settlement combination as a verified arb.</p></div>
        <div style={{border:'1px solid #ddd',borderRadius:999,padding:'8px 12px',fontSize:12}}>{health}</div>
      </div>

      <section style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(190px,1fr))',gap:12,marginTop:24,padding:16,border:'1px solid #e4e4e4',borderRadius:16}}>
        <label>Sport<select value={sport} onChange={e=>setSport(e.target.value)} style={{width:'100%',padding:10,marginTop:6}}>{sports.length ? sports.map(s=><option key={s.key} value={s.key}>{s.title || s.key}</option>) : <option value={sport}>{sport}</option>}</select></label>
        <label>Market coverage<select value={mode} onChange={e=>setMode(e.target.value as 'featured'|'all')} style={{width:'100%',padding:10,marginTop:6}}><option value="featured">Moneyline + spread + total</option><option value="all">All discovered event markets</option></select></label>
        <label>Analysis bankroll<input type="number" min="1" value={bankroll} onChange={e=>setBankroll(Number(e.target.value)||1)} style={{width:'100%',padding:10,marginTop:6,boxSizing:'border-box'}} /></label>
        <button onClick={runScan} disabled={busy || !API} style={{alignSelf:'end',padding:11,border:0,borderRadius:10,background:'#111',color:'#fff',fontWeight:800}}>{busy?'Scanning…':'Scan all available books'}</button>
      </section>

      {scan && <>
        <div style={{display:'flex',gap:10,margin:'18px 0',flexWrap:'wrap'}}><button onClick={()=>setTab('verified')} style={{padding:'9px 12px',borderRadius:9,border:'1px solid #bbb',background:tab==='verified'?'#111':'#fff',color:tab==='verified'?'#fff':'#111'}}>Verified ({scan.n_arbs||0})</button><button onClick={()=>setTab('unverified')} style={{padding:'9px 12px',borderRadius:9,border:'1px solid #bbb',background:tab==='unverified'?'#111':'#fff',color:tab==='unverified'?'#fff':'#111'}}>Detected / unverified ({scan.n_detected_unverified||0})</button><span style={{alignSelf:'center',fontSize:13,color:'#666'}}>{scan.n_markets||0} normalized markets evaluated</span></div>
        <div style={{display:'grid',gap:12}}>{rows.length ? rows.map(h=><article key={h.market_id} style={{border:'1px solid #ddd',borderRadius:14,padding:16}}><div style={{display:'flex',justifyContent:'space-between',gap:16,flexWrap:'wrap'}}><div><strong>{h.event}</strong><div style={{fontSize:12,color:'#666',marginTop:4}}>{h.market} · {h.commence_time || 'start time unavailable'}</div></div><div style={{textAlign:'right'}}><div style={{fontSize:20,fontWeight:900}}>{Number(h.margin_pct||0).toFixed(2)}%</div><div style={{fontSize:12,color:'#666'}}>minimum conditional profit {money(h.minimum_profit)}</div></div></div><div style={{display:'grid',gap:7,marginTop:12}}>{(h.legs||[]).map((l,i)=><div key={`${l.book}-${i}`} style={{padding:10,borderRadius:9,background:'#f6f6f6',display:'flex',justifyContent:'space-between',gap:10,flexWrap:'wrap'}}><span><b>{l.outcome}</b> · {l.book} · {String(l.american)}</span><span style={{fontSize:12,color:'#666'}}>stake {money(l.stake)} · source {l.last_update || 'timestamp unavailable'} {l.link ? <a href={l.link} target="_blank" rel="noreferrer" style={{marginLeft:8}}>open market</a> : null}</span></div>)}</div><div style={{marginTop:10,fontSize:12,fontWeight:700}}>{tab==='verified'?'Settlement rules reviewed and compatible':'Mathematical edge detected; settlement qualification still pending — not presented as a verified arb'}</div></article>) : <div style={{border:'1px dashed #bbb',borderRadius:14,padding:28,textAlign:'center'}}>No {tab==='verified'?'verified':'unverified'} opportunities in this scan.</div>}</div>
      </>}
    </main>
  );
}
