import {
  games as mockGames,
  lineMovement as mockLineMovement,
  rankings as mockRankings,
  statusCards as mockStatusCards,
  type Game,
  type LineMovementPoint,
  type Ranking
} from './mockData';

type SlatePayload = {
  games?: Game[];
  rankings?: Ranking[];
  source?: string;
};

type LineMovementPayload = {
  lineMovement?: LineMovementPoint[];
  source?: string;
};

export type ApiSnapshot = {
  games: Game[];
  rankings: Ranking[];
  lineMovement: LineMovementPoint[];
  statusCards: typeof mockStatusCards;
  apiStatus: 'CONNECTED' | 'MOCK' | 'FAILED';
  apiDetail: string;
};

const fallbackSnapshot: ApiSnapshot = {
  games: mockGames,
  rankings: mockRankings,
  lineMovement: mockLineMovement,
  statusCards: mockStatusCards,
  apiStatus: 'MOCK',
  apiDetail: 'Using local demo data until API base URL is configured'
};

function getApiBaseUrl() {
  const value = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();

  if (!value || value === 'https://api.yourdomain.com') {
    return null;
  }

  return value.replace(/\/$/, '');
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { cache: 'no-store' });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function getApiSnapshot(): Promise<ApiSnapshot> {
  const apiBaseUrl = getApiBaseUrl();

  if (!apiBaseUrl) {
    return fallbackSnapshot;
  }

  try {
    const slate = await fetchJson<SlatePayload>(`${apiBaseUrl}/v1/slates/today`);
    const firstGameId = slate.games?.[0]?.id ?? 'nfl-001';
    const movement = await fetchJson<LineMovementPayload>(`${apiBaseUrl}/v1/games/${firstGameId}/line-movement`);

    const apiStatusCard = {
      label: 'API Status',
      value: 'Connected',
      detail: `Live backend source: ${slate.source ?? movement.source ?? 'Silvers API'}`
    };

    return {
      games: slate.games?.length ? slate.games : mockGames,
      rankings: slate.rankings?.length ? slate.rankings : mockRankings,
      lineMovement: movement.lineMovement?.length ? movement.lineMovement : mockLineMovement,
      statusCards: [apiStatusCard, ...mockStatusCards.slice(0, 3)],
      apiStatus: 'CONNECTED',
      apiDetail: `Connected to ${apiBaseUrl}`
    };
  } catch (error) {
    const apiStatusCard = {
      label: 'API Status',
      value: 'Fallback',
      detail: error instanceof Error ? error.message : 'API unavailable; using local demo data'
    };

    return {
      ...fallbackSnapshot,
      statusCards: [apiStatusCard, ...mockStatusCards.slice(0, 3)],
      apiStatus: 'FAILED',
      apiDetail: 'API call failed; using local demo data'
    };
  }
}
