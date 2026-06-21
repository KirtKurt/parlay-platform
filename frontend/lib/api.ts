export type InqsiGame = {
  game_id: string;
  sport_key: string;
  home_team: string;
  away_team: string;
  commence_time?: string;
  signal_score?: number;
  primary_signal?: string;
  stability_classification?: string;
  status_label?: string;
  what_looks_wrong?: string;
  market_direction?: { side?: string; team?: string };
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
};

const defaultSports = ['americanfootball_nfl', 'basketball_nba', 'baseball_mlb', 'icehockey_nhl', 'basketball_ncaab', 'soccer_epl', 'tennis_atp'];

function apiBase() {
  const value = process.env.NEXT_PUBLIC_INQSI_API_URL || process.env.NEXT_PUBLIC_INQSI_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || '';
  return value.trim().replace(/\/$/, '');
}

function joinUrl(base: string, path: string) {
  if (!base) return '';
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

async function safeFetch<T>(path: string, fallback: T): Promise<T> {
  const base = apiBase();
  if (!base) return fallback;
  try {
    const res = await fetch(joinUrl(base, path), { cache: 'no-store' });
    if (!res.ok) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

export async function getInqsiSnapshot(sportKey = process.env.NEXT_PUBLIC_DEFAULT_SPORT || 'americanfootball_nfl'): Promise<InqsiSnapshot> {
  const base = apiBase();
  const sportsPayload = await safeFetch<any>('/sports', { configured_sports: defaultSports, available_sports: [] });
  const sports = (sportsPayload.configured_sports?.length ? sportsPayload.configured_sports : defaultSports) as string[];
  const selectedSport = sportKey || sports[0] || defaultSports[0];

  const [gamesPayload, predictionsPayload, parlayPayload, livePayload, alertsPayload, performancePayload] = await Promise.all([
    safeFetch<any>(`/games?sport_key=${encodeURIComponent(selectedSport)}`, { games: [] }),
    safeFetch<any>(`/winner-predictions?sport_key=${encodeURIComponent(selectedSport)}`, { predictions: [] }),
    safeFetch<any>(`/auto-parlay?sport_key=${encodeURIComponent(selectedSport)}`, { built: false }),
    safeFetch<any>(`/live-market?sport_key=${encodeURIComponent(selectedSport)}`, { games: [] }),
    safeFetch<any>(`/alerts?sport_key=${encodeURIComponent(selectedSport)}`, { alerts: [] }),
    safeFetch<any>(`/performance?sport_key=${encodeURIComponent(selectedSport)}`, {})
  ]);

  const games = (gamesPayload.games || []) as InqsiGame[];
  const predictions = (predictionsPayload.predictions || []) as InqsiPrediction[];

  return {
    apiStatus: base ? (games.length || predictions.length || parlayPayload?.built ? 'CONNECTED' : 'WAITING') : 'WAITING',
    apiDetail: base ? 'Connected to InQsi API. Waiting areas will appear until sportsbook data is available.' : 'Waiting on API URL. Set NEXT_PUBLIC_INQSI_API_URL to your current backend URL.',
    sports,
    selectedSport,
    games,
    predictions,
    autoParlay: parlayPayload,
    liveMarket: livePayload,
    alerts: alertsPayload.alerts || [],
    performance: performancePayload
  };
}

export const getApiSnapshot = getInqsiSnapshot;
