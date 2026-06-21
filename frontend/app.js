const DEFAULT_SPORTS = [
  { key: 'americanfootball_nfl', title: 'NFL' },
  { key: 'basketball_nba', title: 'NBA' },
  { key: 'baseball_mlb', title: 'MLB' },
  { key: 'icehockey_nhl', title: 'NHL' },
  { key: 'basketball_ncaab', title: 'NCAAM' },
  { key: 'soccer_usa_mls', title: 'MLS' },
  { key: 'tennis_atp', title: 'Tennis' }
];

const API_BASE = window.INQSI_API_BASE || localStorage.getItem('INQSI_API_BASE') || window.location.origin;
const GOOGLE_AUTH_URL = window.INQSI_GOOGLE_AUTH_URL || '';
const APPLE_AUTH_URL = window.INQSI_APPLE_AUTH_URL || '';

let selectedSport = DEFAULT_SPORTS[0].key;
let sports = DEFAULT_SPORTS;

const $ = (id) => document.getElementById(id);
const emptyTemplate = () => $('emptyTemplate').content.cloneNode(true);

function safeText(value, fallback = 'Waiting') {
  return value === null || value === undefined || value === '' ? fallback : String(value);
}

function initials(name) {
  return (name || 'IQ').split(/\s+/).filter(Boolean).slice(0, 2).map((x) => x[0]).join('').toUpperCase();
}

function fmtTime(value) {
  if (!value) return 'Start time pending';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return 'Start time pending';
  return d.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
}

function apiUrl(path, params = {}) {
  const url = new URL(path, API_BASE.replace(/\/$/, '') + '/');
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  });
  return url.toString();
}

async function fetchJson(path, params = {}, options = {}) {
  const res = await fetch(apiUrl(path, params), { headers: { 'content-type': 'application/json' }, ...options });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

function showWaiting(node, title = 'InQsi is warming up the market feed.', body = 'No fake data will be shown here. Waiting on sportsbook data from the API.') {
  node.innerHTML = '';
  const frag = emptyTemplate();
  frag.querySelector('h3').textContent = title;
  frag.querySelector('p').textContent = body;
  node.appendChild(frag);
}

function renderSportsTabs() {
  const tabs = $('sportsTabs');
  tabs.innerHTML = '';
  sports.forEach((sport) => {
    const btn = document.createElement('button');
    btn.className = `tab ${sport.key === selectedSport ? 'active' : ''}`;
    btn.textContent = sport.title || sport.key;
    btn.onclick = () => {
      selectedSport = sport.key;
      renderSportsTabs();
      loadSport(selectedSport);
    };
    tabs.appendChild(btn);
  });
}

function gameCard(game) {
  const home = game.home_team || game.homeTeam || 'Home team pending';
  const away = game.away_team || game.awayTeam || 'Away team pending';
  const score = game.signal_score ?? game.confidence_score ?? '—';
  const signal = game.primary_signal || 'Waiting';
  const stability = game.stability_classification || game.status_label || 'Waiting on market';
  const what = game.what_looks_wrong || game.market_direction_summary || 'Lines are still coming in. InQsi is not showing fake movement.';
  const card = document.createElement('article');
  card.className = 'card game-card';
  card.innerHTML = `
    <div class="teams">
      <div class="team-row"><div class="jersey">${initials(away)}</div><div><div class="team-name">${safeText(away)}</div><div class="team-meta">Away · ${fmtTime(game.commence_time)}</div></div><div class="score-pill">${score}</div></div>
      <div class="team-row"><div class="jersey">${initials(home)}</div><div><div class="team-name">${safeText(home)}</div><div class="team-meta">Home · ${safeText(stability)}</div></div><div class="score-pill">${safeText(signal)}</div></div>
    </div>
    <div class="signal-row">
      <div class="signal"><b>Steam</b><small>${game.indicators?.steam ? 'Active' : 'Watching'}</small></div>
      <div class="signal"><b>Resist</b><small>${game.indicators?.resistance ? 'Active' : 'Watching'}</small></div>
      <div class="signal"><b>Reverse</b><small>${game.indicators?.reversal ? 'Active' : 'Watching'}</small></div>
      <div class="signal"><b>Chaos</b><small>${game.indicators?.chaos ? 'Active' : 'Clear'}</small></div>
    </div>
    <div class="market-note"><strong>What to watch:</strong> ${what}</div>
    <div class="mini-actions">
      <button class="mini-btn" data-action="best">Best Lines</button>
      <button class="mini-btn" data-action="live">Live Market</button>
      <button class="mini-btn" data-action="watch">Watch</button>
    </div>`;
  card.querySelector('[data-action="best"]').onclick = () => loadBestLines(game.game_id);
  card.querySelector('[data-action="live"]').onclick = () => loadLiveMarket();
  card.querySelector('[data-action="watch"]').onclick = () => addWatch(game.game_id);
  return card;
}

async function loadSports() {
  renderSportsTabs();
  try {
    const data = await fetchJson('/v1/inqsi/sports');
    const available = data.available_sports || [];
    if (available.length) {
      sports = available.map((s) => ({ key: s.key, title: s.title || s.group || s.key })).filter((s) => s.key);
      selectedSport = sports[0]?.key || selectedSport;
      $('apiStatus').textContent = 'API connected';
      renderSportsTabs();
    }
  } catch (err) {
    $('apiStatus').textContent = 'Waiting on API';
  }
  loadSport(selectedSport);
}

async function loadSport(sportKey) {
  $('sportTitle').textContent = sports.find((s) => s.key === sportKey)?.title || sportKey;
  const feed = $('gameFeed');
  showWaiting(feed, 'Checking the board.', 'InQsi is asking the API for real games and real sportsbook movement.');
  try {
    const data = await fetchJson('/v1/inqsi/games', { sport_key: sportKey });
    const games = data.games || [];
    $('lastUpdated').textContent = games[0]?.asof ? `Updated ${new Date(games[0].asof).toLocaleTimeString()}` : 'No market pull yet';
    feed.innerHTML = '';
    if (!games.length) {
      showWaiting(feed, 'Lines are still in the tunnel.', 'No games are available from the API for this sport yet.');
    } else {
      games.forEach((game) => feed.appendChild(gameCard(game)));
    }
  } catch (err) {
    showWaiting(feed, 'The API is on the sideline.', 'We are waiting on the current API issue to be resolved. No fake teams or lines are shown.');
    $('lastUpdated').textContent = 'API unavailable';
  }
  loadAutoParlay(sportKey);
  loadWinnerPredictions(sportKey);
  loadPerformance(sportKey);
}

async function loadAutoParlay(sportKey) {
  const box = $('autoParlayBox');
  showWaiting(box, 'Auto parlay waiting.', 'InQsi needs real market data before building 2 anchors + 1 moderate risk leg.');
  try {
    const data = await fetchJson('/v1/inqsi/auto-parlay', { sport_key: sportKey });
    box.innerHTML = '';
    if (!data.built) return showWaiting(box, 'No safe auto parlay yet.', data.refusal?.reason || 'The market has not produced a clean structure yet.');
    (data.selected_legs || []).forEach((leg) => {
      const div = document.createElement('div');
      div.className = 'market-note';
      div.innerHTML = `<strong>${safeText(leg.team)}</strong><br><span>${safeText(leg.side)} · score ${safeText(leg.score, 'pending')}</span>`;
      box.appendChild(div);
    });
  } catch (err) {
    showWaiting(box, 'Auto parlay is warming up.', 'Waiting on real backend parlay data.');
  }
}

async function loadWinnerPredictions(sportKey) {
  const box = $('winnerBox');
  showWaiting(box, 'Winner leans are hidden for now.', 'Predicted winners appear 1 hour before the event starts.');
  try {
    const data = await fetchJson('/v1/inqsi/winner-predictions', { sport_key: sportKey });
    const predictions = data.predictions || [];
    box.innerHTML = '';
    if (!predictions.length) return showWaiting(box, 'Leans are not live yet.', 'InQsi shows winner leans one hour before start time.');
    predictions.slice(0, 5).forEach((p) => {
      const div = document.createElement('div');
      div.className = 'market-note';
      div.innerHTML = `<strong>InQsi leans ${safeText(p.predicted_winner)}</strong><br><span>${safeText(p.short_explanation)}</span>`;
      box.appendChild(div);
    });
  } catch (err) {
    showWaiting(box, 'Winner leans are waiting.', 'No prediction data is available from the API yet.');
  }
}

async function loadPerformance(sportKey) {
  try {
    const data = await fetchJson('/v1/inqsi/performance', { sport_key: sportKey });
    $('performanceBox').textContent = data.winner_predictions_graded ? `Winner accuracy: ${Math.round((data.winner_prediction_accuracy || 0) * 100)}%. Top-3 containment: ${data.top_3_parlay_containment === null ? 'pending' : Math.round(data.top_3_parlay_containment * 100) + '%'}.` : 'Waiting for graded results from the nightly autopsy.';
  } catch (err) {
    $('performanceBox').textContent = 'Performance will appear after the API and nightly autopsy return graded records.';
  }
}

async function loadBestLines(gameId) {
  if (!gameId) return;
  try {
    const data = await fetchJson('/v1/inqsi/best-lines', { sport_key: selectedSport, game_id: gameId });
    alert(data.ok ? 'Best available lines loaded. Check console for book-level detail.' : 'Best lines not available yet.');
    console.log('InQsi best lines', data);
  } catch (err) {
    alert('Best lines are waiting on real sportsbook data.');
  }
}

async function loadLiveMarket() {
  try {
    const data = await fetchJson('/v1/inqsi/live-market', { sport_key: selectedSport });
    console.log('InQsi live market', data);
    alert((data.games || []).length ? 'Live Market Mode loaded. Check console for live state.' : 'No live games are active yet.');
  } catch (err) {
    alert('Live Market Mode is waiting on live API data.');
  }
}

async function addWatch(gameId) {
  try {
    await fetchJson('/v1/inqsi/watchlist/add', { sport_key: selectedSport, game_id: gameId, user_id: localStorage.getItem('INQSI_USER_ID') || 'anonymous' }, { method: 'POST' });
    alert('Added to watchlist.');
  } catch (err) {
    alert('Watchlist will save when user login and API are fully connected.');
  }
}

function openSignup() { $('signupModal').classList.remove('hidden'); }
function closeSignup() { $('signupModal').classList.add('hidden'); }
function authMessage(msg) { $('authMessage').textContent = msg; }

function wireAuth() {
  ['openSignup', 'heroSignup', 'sideSignup'].forEach((id) => $(id)?.addEventListener('click', openSignup));
  $('closeSignup')?.addEventListener('click', closeSignup);
  $('googleLogin')?.addEventListener('click', () => GOOGLE_AUTH_URL ? location.href = GOOGLE_AUTH_URL : authMessage('Google sign-in needs OAuth client URL configuration before production.'));
  $('appleLogin')?.addEventListener('click', () => APPLE_AUTH_URL ? location.href = APPLE_AUTH_URL : authMessage('Apple sign-in needs OAuth client URL configuration before production.'));
  $('emailSignup')?.addEventListener('submit', (event) => {
    event.preventDefault();
    const email = new FormData(event.currentTarget).get('email');
    localStorage.setItem('INQSI_USER_ID', String(email || 'anonymous'));
    authMessage('Promo account captured locally. Connect auth/payment backend before production billing.');
  });
  $('refreshAll')?.addEventListener('click', () => loadSport(selectedSport));
}

wireAuth();
loadSports();
