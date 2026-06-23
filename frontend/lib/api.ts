export type InqsiGame = {
  id: string;
  game_id: string;
  sport_key: string;
  league: string;
  matchup: string;
  start: string;
  home_team: string;
  away_team: string;
  favorite: string;
  underdog: string;
  total: string;
  spread?: string;
  movement: string;
  signals: string[];
  risk: 'LOW' | 'MODERATE' | 'HIGH' | string;
  confidence: string;
  marketNote?: string;
  commence_time?: string;
  signal_score?: number;
  primary_signal?: string;
  stability_classification?: string;
  status_label?: string;
  what_looks_wrong?: string;
  market_direction?: { side?: string; team?: string };
  favoriteMl?: number | string;
  favorite_ml?: number | string;
  underdogMl?: number | string;
  underdog_ml?: number | string;
  bookCount?: number;
};

export type InqsiPrediction = {
  game_id: string;
  sport_key: string;
  home_team: string;
  away_team: string;
  predicted_winner?: string;
  predicted_side?: string;
  confidence_score?: number;
  short_explanation?: string;
  visible_at?: string;
  commence_time?: string;
};

export type LineMovementPoint = {
  time: string;
  bufMoneyline: number;
  miaMoneyline: number;
  signal?: string;
  milestone?: string;
};

export type InqsiSnapshot = {
  apiStatus: 'CONNECTED' | 'WAITING' | 'FAILED';
  apiDetail: string;
  sports: string[];
  selectedSport: string;
  games: InqsiGame[];
  predictions: InqsiPrediction[];
  autoParlay: any;
  liveMarket: any;
  alerts: any[];
  performance: any;
  lineMovement: LineMovementPoint[];
  rankings: any[];
};

const defaultSports = ['nfl', 'cfb', 'nba', 'ncaam', 'mlb', 'wnba', 'nhl', 'soccer', 'tennis'];

const providerToInqisSport: Record<string, string> = {
  americanfootball_nfl: 'nfl',
  americanfootball_ncaaf: 'cfb',
  basketball_nba: 'nba',
  basketball_wnba: 'wnba',
  basketball_ncaab: 'ncaam',
  baseball_mlb: 'mlb',
  icehockey_nhl: 'nhl',
  soccer_epl: 'soccer',
  soccer_usa_mls: 'soccer',
  soccer_uefa_champs_league: 'soccer',
  tennis_atp_singles: 'tennis',
  tennis_wta_singles: 'tennis'
};

function configuredApiBase() {
  const value = process.env.NEXT_PUBLIC_INQSI_API_URL || process.env.NEXT_PUBLIC_INQSI_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || '';
  return value.trim().replace(/\/$/, '');
}

function siteOrigin() {
  const explicit = process.env.NEXT_PUBLIC_SITE_URL || process.env.NEXT_PUBLIC_APP_URL || '';
  if (explicit) return explicit.trim().replace(/\/$/, '');
  const vercel = process.env.VERCEL_URL || '';
  if (vercel) return `https://${vercel.replace(/\/$/, '')}`;
  return '';
}

function requestTargets(path: string) {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  const base = configuredApiBase();
  const origin = siteOrigin();
  const targets = [] as string[];
  if (base) targets.push(`${base}${cleanPath}`);
  if (origin) targets.push(`${origin}${cleanPath}`);
  targets.push(cleanPath);
  return Array.from(new Set(targets));
}

async function safeFetch<T>(path: string, fallback: T): Promise<T & { __inqsiFetchMeta?: any }> {
  const errors: string[] = [];
  for (const target of requestTargets(path)) {
    try {
      const res = await fetch(target, { cache: 'no-store' });
      if (!res.ok) {
        errors.push(`${target} -> HTTP ${res.status}`);
        continue;
      }
      const payload = (await res.json()) as T & { __inqsiFetchMeta?: any };
      payload.__inqsiFetchMeta = { target, ok: true };
      return payload;
    } catch (exc: any) {
      errors.push(`${target} -> ${exc?.message || String(exc)}`);
    }
  }
  return { ...(fallback as any), __inqsiFetchMeta: { ok: false, attempted: requestTargets(path), errors } };
}

function formatAmerican(value: any): string {
  if (value === undefined || value === null || value === '') return '—';
  const numeric = Number(value);
  if (!Number.isNaN(numeric)) return numeric > 0 ? `+${numeric}` : `${numeric}`;
  return String(value);
}

function formatSpreadFromBooks(books: any[] | undefined, favorite: string): string {
  const firstSpread = (books || []).map((b) => b?.spread).find(Boolean);
  if (!firstSpread) return 'Waiting';
  const homePoint = firstSpread.home_point;
  const awayPoint = firstSpread.away_point;
  const homePrice = firstSpread.home_price !== undefined ? formatAmerican(firstSpread.home_price) : '';
  const awayPrice = firstSpread.away_price !== undefined ? formatAmerican(firstSpread.away_price) : '';
  const favLooksHome = homePoint !== undefined && Number(homePoint) < 0;
  const point = favLooksHome ? homePoint : awayPoint;
  const price = favLooksHome ? homePrice : awayPrice;
  if (point === undefined || point === null || point === '') return 'Waiting';
  return `${favorite} ${point}${price ? ` (${price})` : ''}`;
}

function formatTotalFromBooks(books: any[] | undefined): string {
  const firstTotal = (books || []).map((b) => b?.total || b?.overUnder).find(Boolean);
  if (!firstTotal) return 'Waiting';
  const over = firstTotal.over_point ?? firstTotal.point ?? firstTotal.total ?? '';
  const overPrice = firstTotal.over_price !== undefined ? ` (${formatAmerican(firstTotal.over_price)})` : '';
  const underPrice = firstTotal.under_price !== undefined ? ` / U ${formatAmerican(firstTotal.under_price)}` : '';
  return over !== '' ? `O/U ${over}${overPrice}${underPrice}` : 'O/U';
}

function bestBook(game: any): any | undefined {
  return (game.books || [])[0];
}

function normalizeMarketBoardGame(raw: any, sport: string): InqsiGame {
  const book = bestBook(raw);
  const ml = book?.moneyline || {};
  const homeMl = ml.home;
  const awayMl = ml.away;
  const home = raw.homeTeam || raw.home_team || 'Home';
  const away = raw.awayTeam || raw.away_team || 'Away';
  const homeIsFavorite = Number(homeMl) < Number(awayMl);
  const favorite = homeMl !== undefined && awayMl !== undefined ? (homeIsFavorite ? home : away) : home;
  const underdog = favorite === home ? away : home;
  const favoriteMl = favorite === home ? homeMl : awayMl;
  const underdogMl = favorite === home ? awayMl : homeMl;
  const id = raw.gameId || raw.game_id || raw.id || `${sport}-${away}-${home}`.toLowerCase().replace(/[^a-z0-9]+/g, '-');

  return {
    id,
    game_id: id,
    sport_key: sport,
    league: sport,
    matchup: `${away} @ ${home}`,
    start: raw.commenceTime || raw.commence_time || 'TBD',
    home_team: home,
    away_team: away,
    favorite,
    underdog,
    favoriteMl,
    favorite_ml: favoriteMl,
    underdogMl,
    underdog_ml: underdogMl,
    spread: formatSpreadFromBooks(raw.books, favorite),
    total: formatTotalFromBooks(raw.books),
    movement: `${raw.bookCount || 0} books · active-slate market board`,
    signals: ['ACTIVE_SLATE', 'MARKET_BOARD'],
    risk: 'MODERATE',
    confidence: 'Market data live',
    marketNote: book ? `Primary book shown: ${book.book}` : 'Market board active; books pending.',
    commence_time: raw.commenceTime || raw.commence_time,
    primary_signal: 'ACTIVE_SLATE',
    status_label: 'Live',
    bookCount: raw.bookCount || 0
  };
}

function normalizeLegacyGame(raw: any): InqsiGame {
  const home = raw.home_team || raw.homeTeam || 'Home';
  const away = raw.away_team || raw.awayTeam || 'Away';
  const id = raw.id || raw.game_id || `${raw.sport_key || 'sport'}-${away}-${home}`.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  const favorite = raw.favorite || raw.market_direction?.team || home;
  const underdog = raw.underdog || (favorite === home ? away : home);

  return {
    id,
    game_id: raw.game_id || id,
    sport_key: raw.sport_key || raw.league || 'sport',
    league: raw.league || raw.sport_key || 'SPORT',
    matchup: raw.matchup || `${away} @ ${home}`,
    start: raw.start || raw.commence_time || 'TBD',
    home_team: home,
    away_team: away,
    favorite,
    underdog,
    spread: String(raw.spread ?? raw.line ?? 'Waiting'),
    total: String(raw.total ?? raw.over_under ?? 'Waiting'),
    movement: raw.movement || raw.what_looks_wrong || raw.status_label || 'Waiting on verified market movement.',
    signals: Array.isArray(raw.signals) ? raw.signals : raw.primary_signal ? [raw.primary_signal] : ['WAITING'],
    risk: raw.risk || raw.stability_classification || 'MODERATE',
    confidence: raw.confidence || raw.status_label || 'Working on it',
    marketNote: raw.marketNote || raw.short_explanation,
    commence_time: raw.commence_time,
    signal_score: raw.signal_score,
    primary_signal: raw.primary_signal,
    stability_classification: raw.stability_classification,
    status_label: raw.status_label,
    what_looks_wrong: raw.what_looks_wrong,
    market_direction: raw.market_direction
  };
}

function gamesFromMarketBoard(boardPayload: any): InqsiGame[] {
  const boards = Array.isArray(boardPayload?.boards) ? boardPayload.boards : [];
  return boards.flatMap((board: any) => {
    const sport = board?.sport || providerToInqisSport[String(board?.providerSportKey || '')] || 'sport';
    return (board?.games || []).map((game: any) => normalizeMarketBoardGame(game, sport));
  });
}

export async function getInqsiSnapshot(sportKey = process.env.NEXT_PUBLIC_DEFAULT_SPORT || 'nfl'): Promise<InqsiSnapshot> {
  const selectedSport = providerToInqisSport[sportKey] || sportKey || defaultSports[0];

  const [marketBoardPayload, predictionsPayload, parlayPayload, livePayload, alertsPayload, performancePayload] = await Promise.all([
    safeFetch<any>('/v1/inqsi/markets/board', { boards: [] }),
    safeFetch<any>(`/winner-predictions?sport_key=${encodeURIComponent(selectedSport)}`, { predictions: [] }),
    safeFetch<any>(`/auto-parlay?sport_key=${encodeURIComponent(selectedSport)}`, { built: false, rankings: [] }),
    safeFetch<any>(`/live-market?sport_key=${encodeURIComponent(selectedSport)}`, { games: [], lineMovement: [] }),
    safeFetch<any>(`/alerts?sport_key=${encodeURIComponent(selectedSport)}`, { alerts: [] }),
    safeFetch<any>(`/performance?sport_key=${encodeURIComponent(selectedSport)}`, {})
  ]);

  const marketBoardGames = gamesFromMarketBoard(marketBoardPayload);
  const legacyGames = (livePayload.games || []).map(normalizeLegacyGame) as InqsiGame[];
  const games = marketBoardGames.length ? marketBoardGames : legacyGames;
  const predictions = (predictionsPayload.predictions || []) as InqsiPrediction[];
  const rankings = parlayPayload.rankings || parlayPayload.combinations || parlayPayload.top_rankings || [];
  const marketFetch = marketBoardPayload.__inqsiFetchMeta || {};

  return {
    apiStatus: games.length || predictions.length || parlayPayload?.built ? 'CONNECTED' : marketFetch.ok === false ? 'FAILED' : 'WAITING',
    apiDetail: marketBoardGames.length
      ? `Connected to InQsi active-slate market board via ${marketFetch.target || '/v1/inqsi/markets/board'}.`
      : marketFetch.ok === false
        ? `Market board not connected. Tried: ${(marketFetch.attempted || []).join(', ')}`
        : 'Connected to InQsi API. Waiting for active-slate market board games.',
    sports: defaultSports,
    selectedSport,
    games,
    predictions,
    autoParlay: parlayPayload,
    liveMarket: marketBoardPayload,
    alerts: alertsPayload.alerts || [],
    performance: performancePayload,
    lineMovement: livePayload.lineMovement || livePayload.line_movement || [],
    rankings
  };
}

export const getApiSnapshot = getInqsiSnapshot;
