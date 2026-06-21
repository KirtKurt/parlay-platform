export type InqsiSignalName =
  | 'STEAM'
  | 'RESISTANCE'
  | 'REVERSAL'
  | 'CHAOS'
  | 'COMPRESSION'
  | 'MOMENTUM'
  | 'STABILITY'
  | 'CONTEXT';

export type InqsiDataStatus = 'ready' | 'working_on_it' | 'missing_provider' | 'insufficient_snapshots';

export type InqsiMarketSnapshot = {
  sport: string;
  eventId: string;
  source: string;
  capturedAt: string;
  home?: string;
  away?: string;
  moneylineHome?: number;
  moneylineAway?: number;
  spreadHome?: number;
  spreadAway?: number;
  total?: number;
};

export type InqsiSignalResult = {
  status: InqsiDataStatus;
  signalScore: number | null;
  stability: 'HIGH' | 'MODERATE' | 'FRAGILE' | 'WORKING_ON_IT';
  lean: string | null;
  signals: InqsiSignalName[];
  explanation: string;
  whatToWatch: string[];
};

export function evaluateMarketSignal(snapshots: InqsiMarketSnapshot[]): InqsiSignalResult {
  if (!snapshots.length) {
    return {
      status: 'working_on_it',
      signalScore: null,
      stability: 'WORKING_ON_IT',
      lean: null,
      signals: [],
      explanation: 'Working on it. Verified market data is not available yet.',
      whatToWatch: ['provider connection', 'snapshot count', 'market update timing']
    };
  }

  const sources = new Set(snapshots.map((snapshot) => snapshot.source));
  if (sources.size < 2) {
    return {
      status: 'insufficient_snapshots',
      signalScore: null,
      stability: 'WORKING_ON_IT',
      lean: null,
      signals: ['CONTEXT'],
      explanation: 'Working on it. More verified sources are needed before InQsi can score this market.',
      whatToWatch: ['additional source agreement', 'later snapshot movement']
    };
  }

  return {
    status: 'ready',
    signalScore: 50,
    stability: 'MODERATE',
    lean: null,
    signals: ['STABILITY', 'CONTEXT'],
    explanation: 'Market data is present. Full scoring activates after historical movement rules are connected.',
    whatToWatch: ['line movement', 'source agreement', 'late stability']
  };
}

export function buildThreeSelectionRanking(results: InqsiSignalResult[]) {
  if (results.length !== 3 || results.some((result) => result.status !== 'ready')) {
    return {
      status: 'working_on_it' as const,
      message: 'Working on it. Three verified signals are required before ranking all 8 combinations.',
      rankings: [] as Array<{ rank: number; label: string; score: number }>
    };
  }

  return {
    status: 'ready' as const,
    message: 'Ranking framework ready. Final scoring rules connect to verified signal history.',
    rankings: Array.from({ length: 8 }, (_, index) => ({
      rank: index + 1,
      label: `Combination ${index + 1}`,
      score: Math.max(1, 100 - index * 7)
    }))
  };
}
