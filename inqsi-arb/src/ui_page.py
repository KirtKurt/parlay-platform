HTML = r'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Inqsi ARB Console</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#111;background:#f7f7f8}
*{box-sizing:border-box}body{margin:0}.w{max-width:1240px;margin:auto;padding:20px}.top{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}.brand{font-size:24px;font-weight:850;letter-spacing:-.03em}.sub{font-size:12px;color:#666}.grid{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-top:14px}@media(max-width:900px){.grid{grid-template-columns:1fr}}.panel,.card{background:#fff;border:1px solid #dedee3;border-radius:14px;padding:14px}.controls{display:grid;grid-template-columns:1.1fr 1.35fr .65fr .7fr 1fr auto auto;gap:8px;align-items:end}@media(max-width:900px){.controls{grid-template-columns:1fr 1fr}.controls button{width:100%}}label{font-size:11px;color:#666;display:block;margin-bottom:4px}input,select,button{font:inherit;padding:10px;border:1px solid #c9c9cf;border-radius:9px;background:#fff;min-width:0}button{background:#111;color:#fff;border-color:#111;font-weight:750;cursor:pointer}button.secondary{background:#fff;color:#111}.statusrow{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.pill{display:inline-flex;align-items:center;gap:5px;border:1px solid #d5d5db;border-radius:999px;padding:4px 8px;font-size:11px;background:#fff}.dot{width:7px;height:7px;border-radius:50%;background:#999}.dot.ok{background:#1e8e3e}.dot.bad{background:#b3261e}.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:12px}@media(max-width:650px){.metrics{grid-template-columns:repeat(2,1fr)}}.metric{border:1px solid #e4e4e8;border-radius:10px;padding:10px}.metric b{display:block;font-size:20px}.metric span{font-size:11px;color:#666}.arb{border:1px solid #dcdce1;border-radius:12px;padding:12px;margin-top:10px}.arb.unverified{border-color:#e2bf69;background:#fffaf0}.arb.middle{border-color:#8aa2c8;background:#f7faff}.arbhead{display:flex;justify-content:space-between;gap:10px;align-items:start}.event{font-weight:800}.meta{font-size:12px;color:#666;margin-top:3px}.leg{display:grid;grid-template-columns:1.3fr 1fr .7fr .8fr;gap:8px;background:#f6f6f8;padding:8px;border-radius:8px;margin-top:7px;font-size:12px}@media(max-width:650px){.leg{grid-template-columns:1fr 1fr}}.positive{color:#137333;font-weight:750}.warn{color:#9a6700;font-weight:750}.empty{color:#777;padding:14px 0;font-size:13px}.history{font-size:12px;border-top:1px solid #ececf0;padding:9px 0}.history:first-child{border-top:0}.history b{display:block}.small{font-size:11px;color:#777}.error{color:#b3261e}details{margin-top:7px}summary{cursor:pointer}
</style>
</head>
<body>
<div class="w">
  <div class="top">
    <div><div class="brand">Inqsi ARB Console</div><div class="sub">Cross-book value desk · fail-closed settlement validation · no automated bet placement</div></div>
    <div class="statusrow"><span class="pill"><span id="healthDot" class="dot"></span><span id="healthText">Checking API</span></span><span class="pill"><span id="wsDot" class="dot"></span><span id="wsText">Push offline</span></span></div>
  </div>
  <div class="grid">
    <div>
      <div class="panel">
        <div class="controls">
          <div><label>Sport key</label><input id="sport" value="baseball_mlb"></div>
          <div><label>Markets</label><input id="markets" value="h2h,spreads,totals"></div>
          <div><label>Bankroll</label><input id="bankroll" type="number" min="1" value="1000"></div>
          <div><label>Settlement scope</label><select id="jurisdiction"><option value="*" selected>Worldwide</option><option value="az">Arizona</option><option value="co">Colorado</option><option value="il">Illinois</option><option value="in">Indiana</option><option value="ma">Massachusetts</option><option value="nj">New Jersey</option><option value="nv">Nevada</option><option value="ny">New York</option><option value="oh">Ohio</option><option value="pa">Pennsylvania</option><option value="va">Virginia</option></select></div>
          <div><label>My books (optional)</label><input id="books" placeholder="draftkings,fanduel"></div>
          <button id="scanBtn">Scan</button>
          <button id="allBtn" class="secondary">All markets</button>
        </div>
        <div class="small" style="margin-top:7px">Worldwide scans include every provider-returned sportsbook. Verified is a quality stamp on a signal, not a hide-the-board switch. Middles are line gaps, shown separately from same-line surebets.</div>
        <div class="metrics">
          <div class="metric"><b id="mArbs">—</b><span>Verified arbs</span></div>
          <div class="metric"><b id="mUnverified">—</b><span>Math arbs held back</span></div>
          <div class="metric"><b id="mMiddles">—</b><span>Middles</span></div>
          <div class="metric"><b id="mMarkets">—</b><span>Markets evaluated</span></div>
          <div class="metric"><b id="mRejected">—</b><span>Rejected/incomplete</span></div>
        </div>
        <div id="status" class="small" style="margin-top:10px">Ready.</div>
      </div>
      <div id="out"></div>
      <div id="held"></div>
      <div id="middles"></div>
    </div>
    <div>
      <div class="panel"><b>Recent opportunity scans</b><div class="small">Durable scan history from the ARB audit ledger.</div><div id="history" style="margin-top:8px"><div class="empty">Loading…</div></div></div>
      <div class="panel" style="margin-top:14px"><b>Safety posture</b><div class="small" style="margin-top:8px">A mathematical edge is not shown as verified unless the outcome set is exact and every participating sportsbook has an explicitly reviewed, compatible settlement profile for the selected jurisdiction. Unknown or materially different combinations remain unverified.</div></div>
    </div>
  </div>
</div>
<script>
const api=location.origin+location.pathname.replace(/\/v1\/arb\/ui$/,'');
const wsUrl='__INQSI_WS_URL__';
const el=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const fmt=n=>Number(n||0).toFixed(2);
function setDot(id,ok){el(id).className='dot '+(ok?'ok':'bad')}
async function health(){try{const r=await fetch(api+'/v1/arb/health');const j=await r.json();const ok=!!j.ok;setDot('healthDot',ok);el('healthText').textContent=ok?'API healthy':'API degraded';if(!j.audit_persistence)el('healthText').textContent+=' · history off'}catch(e){setDot('healthDot',false);el('healthText').textContent='API unavailable'}}
function legs(x){return(x.legs||[]).map(l=>'<div class="leg"><div><b>'+esc(l.outcome)+'</b></div><div>'+esc(l.book)+'</div><div>'+esc(l.american??l.decimal)+'</div><div>$'+fmt(l.stake)+'</div></div>').join('')}
function renderHits(j){const rejectedMath=(j.rejected||[]).filter(x=>x.math_arb);const held=(j.detected_unverified||[]).concat(rejectedMath);el('mArbs').textContent=j.n_arbs??0;el('mMarkets').textContent=j.n_markets??0;el('mUnverified').textContent=j.n_held_unverified??held.length;el('mRejected').textContent=j.n_rejected??0;el('mMiddles').textContent=j.n_middles??(j.middles||[]).length;const hits=j.hits||[];el('out').innerHTML=hits.length?hits.map(x=>'<div class="card arb"><div class="arbhead"><div><div class="event">'+esc(x.event)+'</div><div class="meta">'+esc(x.market)+' · '+esc(x.commence_time||'')+'</div></div><div><span class="pill positive">VERIFIED '+fmt(x.margin_pct)+'%</span></div></div><div class="meta">Guaranteed minimum profit: <span class="positive">$'+fmt(x.minimum_profit)+'</span> on $'+fmt(x.allocated_stake||x.bankroll)+'</div>'+legs(x)+'</div>').join(''):'<div class="card empty">No settlement-verified arbitrage opportunities in this scan. Detected math and middles still appear below.</div>';const exchange=j.exchange_pending||[];el('held').innerHTML=(held.length||exchange.length)?'<div class="panel" style="margin-top:14px"><b>Held-back mathematical opportunities</b><div class="small">Visible for audit, never presented as executable until every gate passes.</div>'+held.map(x=>{const v=x.validation||{};return '<div class="arb unverified"><div class="arbhead"><div><div class="event">'+esc(x.event)+'</div><div class="meta">'+esc(x.market)+' · '+esc(x.commence_time||'')+'</div></div><span class="pill warn">HELD '+fmt(x.margin_pct)+'%</span></div><div class="meta">Reason: '+esc(v.settlement_reason||v.qualification_reason||'SETTLEMENT_NOT_VERIFIED')+(v.missing_books?.length?' · Missing rules: '+esc(v.missing_books.join(', ')):'')+'</div>'+legs(x)+'</div>'}).join('')+(exchange.length?'<details><summary>'+exchange.length+' exchange lay market(s) routed away from sportsbook math</summary><div class="small">These require commission, liability and liquidity-aware back/lay evaluation.</div></details>':'')+'</div>':'';const middles=j.middles||[];el('middles').innerHTML=middles.length?'<div class="panel" style="margin-top:14px"><b>Middles</b><div class="small">Line gaps across books. Free middles lock a profit even if the gap misses. Risk middles are not arbitrage.</div>'+middles.map(x=>'<div class="arb middle"><div class="arbhead"><div><div class="event">'+esc(x.event)+'</div><div class="meta">'+esc(x.market)+' · gap '+fmt(x.gap)+'</div></div><span class="pill">'+(x.kind==='free_middle'?'FREE MIDDLE':'RISK MIDDLE')+'</span></div><div class="meta">If gap hits: <span class="positive">$'+fmt(x.pnl_if_middle_hits)+'</span> · if gap misses: $'+fmt(x.minimum_miss_pnl)+'</div>'+legs(x)+'</div>').join('')+'</div>':''}
async function scan(){el('status').textContent='Scanning live provider data and settlement rules…';el('out').innerHTML='';el('held').innerHTML='';el('middles').innerHTML='';const p=new URLSearchParams({sport:el('sport').value.trim(),markets:el('markets').value.trim(),bankroll:el('bankroll').value,jurisdiction:el('jurisdiction').value});if(el('books').value.trim())p.set('books',el('books').value.trim());try{const r=await fetch(api+'/v1/arb/scan?'+p);const j=await r.json();if(!r.ok)throw new Error(j.error||'scan failed');renderHits(j);el('status').textContent='Scan complete · '+(j.version||'')+' · jurisdiction '+el('jurisdiction').value;refreshHistory()}catch(err){el('status').innerHTML='<span class="error">Scan unavailable: '+esc(err.message)+'</span>'}}
function scanAllMarkets(){el('markets').value='all';scan()}
async function refreshHistory(){try{const r=await fetch(api+'/v1/arb/history?limit=12');const j=await r.json();if(!r.ok)throw new Error(j.error||'history unavailable');const rows=j.history||[];el('history').innerHTML=rows.map(row=>{const p=row.payload||{};const d=new Date(Number(row.created_at_ms||0));const held=(p.detected_unverified||[]).concat((p.rejected||[]).filter(x=>x.math_arb));return '<div class="history"><b>'+esc(p.sport||'scan')+' · '+Number(p.n_arbs||0)+' verified</b><div class="small">'+esc(d.toLocaleString())+' · '+esc(p.jurisdiction||'*')+' · '+Number(p.n_markets||0)+' markets · '+Number(p.n_held_unverified??held.length)+' held back ('+held.length+' details) · '+Number(p.n_exchange_pending||0)+' exchange pending</div>'+(held.length?'<details><summary>Inspect held-back opportunities</summary>'+held.map(x=>'<div class="small"><b>'+esc(x.event)+' · '+esc(x.market)+' · '+fmt(x.margin_pct)+'%</b>'+esc((x.validation||{}).settlement_reason||(x.validation||{}).qualification_reason||'not verified')+'</div>').join('')+'</details>':'')+'</div>'}).join('')||'<div class="empty">No scan history yet.</div>'}catch(e){el('history').innerHTML='<div class="empty">History unavailable.</div>'}}
function connectWs(){if(!wsUrl||!wsUrl.startsWith('wss://')){setDot('wsDot',false);el('wsText').textContent='Push not configured';return}try{const ws=new WebSocket(wsUrl);ws.onopen=()=>{setDot('wsDot',true);el('wsText').textContent='Live push connected'};ws.onmessage=ev=>{try{const msg=JSON.parse(ev.data);if(msg.type==='ARB_SCAN_UPDATE'){el('wsText').textContent='Live update received';refreshHistory()}}catch(_){}};ws.onclose=()=>{setDot('wsDot',false);el('wsText').textContent='Push reconnecting';setTimeout(connectWs,5000)};ws.onerror=()=>{setDot('wsDot',false)}}catch(e){setDot('wsDot',false);el('wsText').textContent='Push unavailable'}}
el('scanBtn').addEventListener('click',scan);el('allBtn').addEventListener('click',scanAllMarkets);health();refreshHistory();connectWs();
</script>
</body>
</html>'''
