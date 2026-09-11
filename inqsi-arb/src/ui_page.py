HTML = r'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Inqsi ARB Console</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#111;background:#f7f7f8}
*{box-sizing:border-box}body{margin:0}.w{max-width:1240px;margin:auto;padding:20px}.top{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}.brand{font-size:24px;font-weight:850;letter-spacing:-.03em}.sub{font-size:12px;color:#666}.grid{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-top:14px}@media(max-width:900px){.grid{grid-template-columns:1fr}}.panel,.card{background:#fff;border:1px solid #dedee3;border-radius:14px;padding:14px}.controls{display:grid;grid-template-columns:1.2fr 1.5fr .7fr auto auto;gap:8px;align-items:end}@media(max-width:800px){.controls{grid-template-columns:1fr 1fr}.controls button{width:100%}}label{font-size:11px;color:#666;display:block;margin-bottom:4px}input,button{font:inherit;padding:10px;border:1px solid #c9c9cf;border-radius:9px;background:#fff}button{background:#111;color:#fff;border-color:#111;font-weight:750;cursor:pointer}button.secondary{background:#fff;color:#111}.statusrow{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.pill{display:inline-flex;align-items:center;gap:5px;border:1px solid #d5d5db;border-radius:999px;padding:4px 8px;font-size:11px;background:#fff}.dot{width:7px;height:7px;border-radius:50%;background:#999}.dot.ok{background:#1e8e3e}.dot.bad{background:#b3261e}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}@media(max-width:650px){.metrics{grid-template-columns:repeat(2,1fr)}}.metric{border:1px solid #e4e4e8;border-radius:10px;padding:10px}.metric b{display:block;font-size:20px}.metric span{font-size:11px;color:#666}.arb{border:1px solid #dcdce1;border-radius:12px;padding:12px;margin-top:10px}.arbhead{display:flex;justify-content:space-between;gap:10px;align-items:start}.event{font-weight:800}.meta{font-size:12px;color:#666;margin-top:3px}.leg{display:grid;grid-template-columns:1.3fr 1fr .7fr .8fr;gap:8px;background:#f6f6f8;padding:8px;border-radius:8px;margin-top:7px;font-size:12px}@media(max-width:650px){.leg{grid-template-columns:1fr 1fr}}.positive{color:#137333;font-weight:750}.warn{color:#9a6700}.empty{color:#777;padding:14px 0;font-size:13px}.history{font-size:12px;border-top:1px solid #ececf0;padding:9px 0}.history:first-child{border-top:0}.history b{display:block}.small{font-size:11px;color:#777}.error{color:#b3261e}.live{animation:pulse 1.5s infinite}@keyframes pulse{50%{opacity:.45}}
</style>
</head>
<body>
<div class="w">
  <div class="top">
    <div><div class="brand">Inqsi ARB Console</div><div class="sub">Verified arbitrage only · fail-closed settlement validation · no automated bet placement</div></div>
    <div class="statusrow"><span class="pill"><span id="healthDot" class="dot"></span><span id="healthText">Checking API</span></span><span class="pill"><span id="wsDot" class="dot"></span><span id="wsText">Push offline</span></span></div>
  </div>
  <div class="grid">
    <div>
      <div class="panel">
        <div class="controls">
          <div><label>Sport key</label><input id="sport" value="baseball_mlb"></div>
          <div><label>Markets</label><input id="markets" value="h2h,spreads,totals"></div>
          <div><label>Bankroll</label><input id="bankroll" type="number" min="1" value="1000"></div>
          <button onclick="scan()">Scan</button>
          <button class="secondary" onclick="scanAllMarkets()">All markets</button>
        </div>
        <div class="metrics">
          <div class="metric"><b id="mArbs">—</b><span>Verified arbs</span></div>
          <div class="metric"><b id="mMarkets">—</b><span>Markets evaluated</span></div>
          <div class="metric"><b id="mUnverified">—</b><span>Math arbs held back</span></div>
          <div class="metric"><b id="mRejected">—</b><span>Rejected/incomplete</span></div>
        </div>
        <div id="status" class="small" style="margin-top:10px">Ready.</div>
      </div>
      <div id="out"></div>
    </div>
    <div>
      <div class="panel"><b>Recent opportunity scans</b><div class="small">Durable scan history from the ARB audit ledger.</div><div id="history" style="margin-top:8px"><div class="empty">Loading…</div></div></div>
      <div class="panel" style="margin-top:14px"><b>Safety posture</b><div class="small" style="margin-top:8px">A mathematical edge is not shown as verified unless the outcome set is exact and every participating sportsbook has an explicitly reviewed, compatible settlement profile. Unknown combinations remain unverified.</div></div>
    </div>
  </div>
</div>
<script>
const api=location.origin+location.pathname.replace(/\/v1\/arb\/ui$/,'');
const wsUrl='__INQSI_WS_URL__';
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const fmt=n=>Number(n||0).toFixed(2);
function setDot(id,ok){document.getElementById(id).className='dot '+(ok?'ok':'bad')}
async function health(){try{const r=await fetch(api+'/v1/arb/health');const j=await r.json();const ok=!!j.ok;setDot('healthDot',ok);healthText.textContent=ok?'API healthy':'API degraded';if(!j.audit_persistence)healthText.textContent+=' · history off'}catch(e){setDot('healthDot',false);healthText.textContent='API unavailable'}}
function renderHits(j){mArbs.textContent=j.n_arbs??0;mMarkets.textContent=j.n_markets??0;mUnverified.textContent=j.n_detected_unverified??0;mRejected.textContent=j.n_rejected??0;const hits=j.hits||[];if(!hits.length){out.innerHTML='<div class="card empty">No settlement-verified arbitrage opportunities in this scan.</div>';return}out.innerHTML=hits.map(x=>'<div class="card arb"><div class="arbhead"><div><div class="event">'+esc(x.event)+'</div><div class="meta">'+esc(x.market)+' · '+esc(x.commence_time||'')+'</div></div><div><span class="pill positive">VERIFIED '+fmt(x.margin_pct)+'%</span></div></div><div class="meta">Guaranteed minimum profit: <span class="positive">$'+fmt(x.minimum_profit)+'</span> on $'+fmt(x.bankroll)+'</div>'+(x.legs||[]).map(l=>'<div class="leg"><div><b>'+esc(l.outcome)+'</b></div><div>'+esc(l.book)+'</div><div>'+esc(l.american)+'</div><div>$'+fmt(l.stake)+'</div></div>').join('')+'</div>').join('')}
async function scan(){status.textContent='Scanning live provider data and settlement rules…';out.innerHTML='';const p=new URLSearchParams({sport:sport.value.trim(),markets:markets.value.trim(),bankroll:bankroll.value});try{const r=await fetch(api+'/v1/arb/scan?'+p);const j=await r.json();if(!r.ok)throw new Error(j.error||'scan failed');renderHits(j);status.textContent='Scan complete · '+(j.version||'');refreshHistory()}catch(err){status.innerHTML='<span class="error">Scan unavailable: '+esc(err.message)+'</span>'}}
function scanAllMarkets(){markets.value='all';scan()}
async function refreshHistory(){try{const r=await fetch(api+'/v1/arb/history?limit=12');const j=await r.json();if(!r.ok)throw new Error(j.error||'history unavailable');const rows=j.history||[];history.innerHTML=rows.map(row=>{const p=row.payload||{};const d=new Date(Number(row.created_at_ms||0));return '<div class="history"><b>'+esc(p.sport||'scan')+' · '+Number(p.n_arbs||0)+' verified</b><div class="small">'+esc(d.toLocaleString())+' · '+Number(p.n_markets||0)+' markets · '+Number(p.n_detected_unverified||0)+' held back</div></div>'}).join('')||'<div class="empty">No scan history yet.</div>'}catch(e){history.innerHTML='<div class="empty">History unavailable.</div>'}}
function connectWs(){if(!wsUrl||!wsUrl.startsWith('wss://')){setDot('wsDot',false);wsText.textContent='Push not configured';return}try{const ws=new WebSocket(wsUrl);ws.onopen=()=>{setDot('wsDot',true);wsText.textContent='Live push connected'};ws.onmessage=ev=>{try{const msg=JSON.parse(ev.data);if(msg.type==='ARB_SCAN_UPDATE'){wsText.textContent='Live update received';refreshHistory()}}catch(_){}};ws.onclose=()=>{setDot('wsDot',false);wsText.textContent='Push reconnecting';setTimeout(connectWs,5000)};ws.onerror=()=>{setDot('wsDot',false)}}catch(e){setDot('wsDot',false);wsText.textContent='Push unavailable'}}
health();refreshHistory();connectWs();
</script>
</body>
</html>'''
