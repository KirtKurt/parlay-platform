import json
from typing import Any, Dict

import api
import inqsi_api


APP_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>InQsi | Pull-History Sports Market Intelligence</title>
  <meta name="description" content="InQsi tracks 15-minute sportsbook movement, college and pro sports signals, parlay risk, readiness, and market-quality checks." />
  <meta name="theme-color" content="#07111f" />
  <style>
    :root{--bg:#07111f;--panel:#0c1a2d;--panel2:#10243c;--line:#1d3f66;--text:#eef7ff;--muted:#92a9c3;--cyan:#23d2ff;--blue:#2f7cff;--green:#18d98b;--yellow:#ffd166;--red:#ff5d73;--white:#fff;--radius:22px;}
    *{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#07111f,#0a1525 42%,#07111f);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;min-height:100vh}.app{max-width:1240px;margin:0 auto;padding:16px 16px 96px}.topbar{position:sticky;top:0;z-index:20;background:rgba(7,17,31,.9);backdrop-filter:blur(16px);border-bottom:1px solid rgba(35,210,255,.14)}.top-inner{max-width:1240px;margin:0 auto;padding:12px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px}.brand{display:flex;align-items:center;gap:10px;font-weight:950;letter-spacing:.2px}.qmark{width:42px;height:42px;border-radius:14px;background:linear-gradient(135deg,#132a48,#0a1728);border:1px solid rgba(35,210,255,.35);display:grid;place-items:center;box-shadow:0 0 28px rgba(35,210,255,.18)}.qmark:before{content:"Q";font-size:25px;color:var(--cyan);font-weight:950}.brand-title{font-size:24px;line-height:1}.brand-sub{display:block;font-size:11px;color:var(--muted);font-weight:800}.btn{border:0;border-radius:999px;padding:11px 14px;background:var(--panel2);color:var(--text);font-weight:850;cursor:pointer;border:1px solid rgba(255,255,255,.08)}.btn.primary{background:linear-gradient(135deg,var(--cyan),var(--blue));color:#04101f}.hero{padding:22px 0 14px;display:grid;grid-template-columns:1.1fr .9fr;gap:16px}.hero-card,.signup-card,.section{border:1px solid rgba(35,210,255,.16);background:linear-gradient(180deg,rgba(16,36,60,.94),rgba(8,18,32,.94));border-radius:28px;padding:22px;box-shadow:0 18px 55px rgba(0,0,0,.34)}.signup-card{background:linear-gradient(180deg,#fff,#eaf7ff);color:#061320}h1{font-size:clamp(34px,6vw,66px);line-height:.95;margin:0 0 14px;letter-spacing:-2px}.gradient{background:linear-gradient(90deg,var(--cyan),#fff,var(--blue));-webkit-background-clip:text;background-clip:text;color:transparent}.lead{font-size:17px;color:#c8d9ec;line-height:1.5}.promo{display:inline-flex;margin:0 0 14px;padding:8px 11px;border-radius:999px;color:#051322;background:linear-gradient(90deg,var(--yellow),#fff1aa);font-weight:950;font-size:13px}.hero-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:16px}.mini-stat{padding:14px;border-radius:18px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08)}.mini-stat b{display:block;font-size:20px}.mini-stat span{font-size:12px;color:var(--muted);font-weight:800}.tabs{display:flex;gap:8px;overflow:auto;padding:8px 0 12px;scrollbar-width:none}.tab{white-space:nowrap;border-radius:999px;padding:10px 14px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);color:#d8e9fa;font-weight:900;cursor:pointer}.tab.active{background:linear-gradient(135deg,var(--cyan),var(--blue));color:#04101f}.group-title{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);font-weight:950;margin:18px 0 8px}.layout{display:grid;grid-template-columns:1.5fr .8fr;gap:16px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.section h2{font-size:21px;margin:0}.pill{font-size:12px;color:#c9d8e8;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);padding:7px 10px;border-radius:999px;font-weight:850}.game-list{display:grid;gap:10px}.game-card,.parlay-card{background:linear-gradient(180deg,rgba(17,38,64,.9),rgba(10,23,39,.9));border:1px solid rgba(35,210,255,.09);border-radius:20px;padding:13px}.game-row{display:flex;align-items:center;justify-content:space-between;gap:10px}.team-name{font-weight:950}.team-meta{font-size:12px;color:var(--muted);margin-top:3px}.score-chip{padding:8px 10px;border-radius:14px;background:rgba(255,255,255,.07);font-weight:950;color:var(--cyan);min-width:62px;text-align:center}.signals{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.sig{font-size:11px;font-weight:900;border:1px solid rgba(255,255,255,.09);padding:6px 8px;border-radius:999px;background:rgba(255,255,255,.05)}.green{color:var(--green)}.yellow{color:var(--yellow)}.red{color:var(--red)}.empty{border:1px dashed rgba(35,210,255,.25);border-radius:18px;padding:18px;text-align:center;color:#b7cee6;background:rgba(35,210,255,.04)}.empty b{display:block;color:#fff;margin-bottom:4px}.sidebar{display:grid;gap:16px;align-content:start}.rank{display:flex;justify-content:space-between;gap:8px;padding:9px;border-radius:12px;background:rgba(0,0,0,.16);margin-top:8px;font-size:13px}.feature-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}.feature{border-radius:18px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);padding:14px;min-height:110px}.feature b{display:block;font-size:15px;margin-bottom:6px}.feature span{font-size:12px;color:var(--muted);line-height:1.4}.bottom-nav{position:fixed;left:0;right:0;bottom:0;z-index:30;background:rgba(7,17,31,.92);backdrop-filter:blur(16px);border-top:1px solid rgba(35,210,255,.15)}.bottom-inner{max-width:680px;margin:0 auto;display:grid;grid-template-columns:repeat(5,1fr);gap:4px;padding:8px}.navbtn{border:0;background:transparent;color:var(--muted);font-size:11px;font-weight:900;padding:8px 4px;border-radius:14px}.navbtn.active{color:#04101f;background:linear-gradient(135deg,var(--cyan),var(--blue))}@media(max-width:900px){.hero,.layout{grid-template-columns:1fr}.feature-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:520px){.app{padding:12px 10px 92px}.hero-card,.signup-card,.section{border-radius:20px;padding:14px}.hero-grid,.feature-grid{grid-template-columns:1fr}h1{letter-spacing:-1px}.brand-title{font-size:21px}}
  </style>
</head>
<body>
  <header class="topbar"><div class="top-inner"><div class="brand"><span class="qmark"></span><span><span class="brand-title">InQsi</span><span class="brand-sub">15-minute pull-history intelligence</span></span></div><button class="btn primary" onclick="loadSport(current)">Refresh</button></div></header>
  <main class="app">
    <section class="hero"><div class="hero-card"><div class="promo">Pull-history architecture • No fake odds</div><h1>Find what looks wrong <span class="gradient">before you lock it in.</span></h1><p class="lead">InQsi now works from many timestamped 15-minute pulls instead of fixed T1-T3 snapshots. The frontend includes pro sports, college football, college baseball, college softball, men’s and women’s college basketball, and women’s college football as a placeholder until provider coverage is confirmed.</p><div class="hero-grid"><div class="mini-stat"><b>15 min</b><span>pull-history cadence</span></div><div class="mini-stat"><b>Many pulls</b><span>velocity + acceleration</span></div><div class="mini-stat"><b>Multi-book</b><span>agreement + divergence</span></div><div class="mini-stat"><b>Refuse</b><span>no forced parlays</span></div></div></div><aside class="signup-card"><h2>College coverage added</h2><p>The tabs now show college baseball men, college baseball women, college softball women, NCAAW, and women’s college football placeholder.</p><button class="btn primary" onclick="selectCollege()">Jump to college sports</button><p class="team-meta">Live odds availability still depends on provider support. Manual/provider-shaped pulls can be analyzed now.</p></aside></section>
    <div class="group-title">Sports Board</div><nav class="tabs" id="sportsTabs" aria-label="Sports"></nav>
    <section class="layout"><div><section class="section"><div class="section-head"><h2 id="sportTitle">Market Board</h2><span class="pill" id="lastUpdated">Waiting on pull history</span></div><div class="game-list" id="games"></div></section><section class="feature-grid"><div class="feature"><b>Pull-History Signals</b><span>Steam, resistance, reversal, velocity, acceleration, compression, and book divergence.</span></div><div class="feature"><b>Readiness Gate</b><span>Confirms whether enough 15-minute pulls exist before a build.</span></div><div class="feature"><b>Parlay Builder</b><span>Builds only when three unique eligible games exist.</span></div><div class="feature"><b>College Support</b><span>College tabs are represented even when live feed coverage is still pending.</span></div></section></div><aside class="sidebar"><section class="section"><div class="section-head"><h2>Readiness</h2><span class="pill">Algorithm</span></div><div id="readiness"></div></section><section class="section"><div class="section-head"><h2>Pull-History Parlay</h2><span class="pill">No force</span></div><div id="parlay"></div></section><section class="section"><div class="section-head"><h2>Data Quality</h2><span class="pill">Pull depth</span></div><div id="quality"></div></section></aside></section>
  </main>
  <nav class="bottom-nav"><div class="bottom-inner"><button class="navbtn active" onclick="scrollTo({top:0,behavior:'smooth'})">Home</button><button class="navbtn" onclick="scrollToId('games')">Games</button><button class="navbtn" onclick="selectCollege()">College</button><button class="navbtn" onclick="scrollToId('parlay')">Parlay</button><button class="navbtn" onclick="loadSport(current)">Refresh</button></div></nav>
<script>
const SPORTS=[
  ['nba','NBA','Pro'],['nfl','NFL','Pro'],['mlb','MLB','Pro'],['nhl','NHL','Pro'],['wnba','WNBA','Pro'],
  ['ncaam','NCAAM','College'],['ncaaw','NCAAW','College'],['cfb','NCAAF','College'],['college_football_women','Women’s CFB Placeholder','College'],
  ['college_baseball_men','College Baseball Men','College'],['college_baseball_women','College Baseball Women','College'],['college_softball_women','College Softball Women','College'],
  ['soccer','Soccer','Other'],['tennis','Tennis','Other'],['mma','MMA','Other'],['boxing','Boxing','Other'],['golf','Golf','Other'],['esports','eSports','Other']
];
let current=SPORTS[0][0];
function $(id){return document.getElementById(id)}
function empty(title,msg='Working on it. We are waiting on verified pull history.'){return `<div class="empty"><b>${title}</b><span>${msg}</span></div>`}
function scrollToId(id){document.getElementById(id)?.scrollIntoView({behavior:'smooth',block:'start'})}
function labelFor(k){return SPORTS.find(s=>s[0]===k)?.[1]||k}
async function apiGet(path){try{const r=await fetch(path,{headers:{accept:'application/json'}});if(!r.ok)throw new Error('not ready');return await r.json()}catch(e){return {ok:false,error:'Working on it'}}}
function renderTabs(){const el=$('sportsTabs');let last='';el.innerHTML=SPORTS.map(([k,n,g])=>`${g!==last?`<span class="pill">${(last=g)}</span>`:''}<button class="tab ${k===current?'active':''}" onclick="loadSport('${k}')">${n}</button>`).join('')}
function signalCard(s){const tags=(s.tags||[]).slice(0,6).map(t=>`<span class="sig yellow">${t}</span>`).join('');return `<article class="game-card"><div class="game-row"><div><div class="team-name">${s.awayTeam||'Away'} @ ${s.homeTeam||'Home'}</div><div class="team-meta">Pick lean: ${s.selection||'Waiting'} • ${s.level||'level pending'} ${s.gender?`• ${s.gender}`:''}</div></div><div class="score-chip">${s.score??'—'}</div></div><div class="signals"><span class="sig green">${s.grade||'WAITING'}</span>${tags}</div></article>`}
function renderSignals(data){const list=data.signals||[];$('lastUpdated').textContent=data.pullCount?`${data.pullCount} pull(s) stored`:'Waiting on pull history';if(!list.length){$('games').innerHTML=empty('No pull-history signals yet',`Add at least two 15-minute pulls for ${labelFor(current)} before signals appear.`);return}$('games').innerHTML=list.map(signalCard).join('')}
function renderReadiness(data){$('readiness').innerHTML=data&&data.ok?`<div class="parlay-card"><b>${data.status||'NOT_READY'}</b><p class="team-meta">Pulls: ${data.pullCount??0} • Eligible: ${data.eligibleSignals??0} • Strong: ${data.strongSignals??0}</p></div>`:empty('Readiness working on it')}
function renderParlay(data){if(!data||data.ok===false||data.buildStatus!=='BUILT'){$('parlay').innerHTML=empty('No parlay build',data?.message||data?.reason||'InQsi refused to force a parlay.');return}const ranks=data.rankedCombos||[];$('parlay').innerHTML=`<div class="parlay-card"><b>${data.structure||'Pull-history build'}</b><p class="team-meta">Pulls: ${data.pullCount??'—'}</p></div>`+ranks.slice(0,4).map(r=>`<div class="rank"><span>#${r.rank} Top-${r.top3?'3':'8'}</span><b>${r.score}</b></div>`).join('')}
function renderQuality(data){const report=(data.reports||[]).find(r=>r.sport===current)||{};$('quality').innerHTML=data&&data.ok?`<div class="parlay-card"><b>${report.status||'WAITING'}</b><p class="team-meta">${report.label||labelFor(current)} • Pulls: ${report.pullCount??0}</p></div>`:empty('Quality check working on it')}
async function loadSport(k){current=k;renderTabs();$('sportTitle').textContent=`${labelFor(k)} Pull-History Board`;$('games').innerHTML=empty('Loading verified pull history');$('readiness').innerHTML=empty('Checking readiness');$('parlay').innerHTML=empty('Checking build');$('quality').innerHTML=empty('Checking quality');const qs=`sport=${encodeURIComponent(k)}`;const [signals,ready,parlay,quality]=await Promise.all([apiGet(`/v1/inqsi/algorithm/signals?${qs}`),apiGet(`/v1/inqsi/algorithm/readiness?${qs}`),apiGet(`/v1/inqsi/parlays/build-pull-history?${qs}`),apiGet(`/v1/inqsi/monitoring/pull-data-quality?${qs}`)]);renderSignals(signals);renderReadiness(ready);renderParlay(parlay);renderQuality(quality)}
function selectCollege(){loadSport('ncaam');setTimeout(()=>document.getElementById('sportsTabs')?.scrollIntoView({behavior:'smooth'}),50)}
renderTabs();loadSport(current);
</script>
</body></html>'''


def _html(status: int, body: str) -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=60"}, "body": body}


def _text(status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": content_type, "cache-control": "public, max-age=300"}, "body": body}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event or {}
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if path.startswith("/v1/inqsi"):
        rewritten = dict(event)
        rewritten["path"] = path.replace("/v1/inqsi", "/v1/inqsi", 1)
        rewritten["rawPath"] = rewritten["path"]
        return inqsi_api.lambda_handler(rewritten, context)
    if path.startswith("/v1"):
        return api.lambda_handler(event, context)
    if path == "/robots.txt":
        return _text(200, "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n")
    if path == "/sitemap.xml":
        pages = ["/", "/winner-predictions", "/best-lines", "/parlay-scanner", "/live-market", "/performance", "/alerts", "/clv", "/watchlist", "/context"]
        body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">" + "".join([f"<url><loc>{p}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>" for p in pages]) + "</urlset>"
        return _text(200, body, "application/xml; charset=utf-8")
    if path == "/manifest.webmanifest":
        manifest = {"name": "InQsi", "short_name": "InQsi", "start_url": "/", "display": "standalone", "background_color": "#07111f", "theme_color": "#07111f", "description": "Sports market intelligence and pull-history parlay risk checks."}
        return _text(200, json.dumps(manifest), "application/manifest+json")
    return _html(200, APP_HTML)
