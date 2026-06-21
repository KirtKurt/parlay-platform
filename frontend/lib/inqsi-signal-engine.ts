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

function americanToImplied(price?: number) {
  if (typeof price !== 'number' || Number.isNaN(price) || price === 0) return null;
  return price > 0 ? 100 / (price + 100) : Math.abs(price) / (Math.abs(price) + 100);
}

function average(values: number[]) {
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function clamp(value: number, min = 1, max = 99) {
  return Math.min(max, Math.max(min, Math.round(value)));
}

export function evaluateMarketSignal(snapshots: InqsiMarketSnapshot[]): InqsiSignalResult {
  if (!snapshots.length) {
    return {
      status: 'working_on_it',
      signalScore: null,
      stability: 'WORKING_ON_IT',
      lean: null,
      signals: [],
      explanation: 'Working on it. Verified market data is not available yet.',
      whatToWatch: ['feed connection', 'snapshot count', 'market update timing']
    };
  }

  const sources = new Set(snapshots.map((snapshot) => snapshot.source));
  const times = new Set(snapshots.map((snapshot) => snapshot.capturedAt));
  if (sources.size < 2 || times.size < 2) {
    return {
      status: 'insufficient_snapshots',
      signalScore: null,
      stability: 'WORKING_ON_IT',
      lean: null,
      signals: ['CONTEXT'],
      explanation: 'Working on it. InQsi needs multiple verified sources and multiple capture times before scoring this market.',
      whatToWatch: ['additional source agreement', 'later movement', 'verified timing']
    };
  }

  const sorted = [...snapshots].sort((a, b) => a.capturedAt.localeCompare(b.capturedAt));
  const first = sorted.slice(0, Math.ceil(sorted.length / 2));
  const last = sorted.slice(Math.floor(sorted.length / 2));
  const firstHome = average(first.map((item) => americanToImplied(item.moneylineHome)).filter((value): value is number => value !== null));
  const lastHome = average(last.map((item) => americanToImplied(item.moneylineHome)).filter((value): value is number => value !== null));
  const firstAway = average(first.map((item) => americanToImplied(item.moneylineAway)).filter((value): value is number => value !== null));
  const lastAway = average(last.map((item) => americanToImplied(item.moneylineAway)).filter((value): value is number => value !== null));

  if (firstHome === null || lastHome === null || firstAway === null || lastAway === null) {
    return {
      status: 'insufficient_snapshots',
      signalScore: null,
      stability: 'WORKING_ON_IT',
      lean: null,
      signals: ['CONTEXT'],
      explanation: 'Working on it. Price fields are missing or incomplete for this market.',
      whatToWatch: ['complete source values', 'book agreement', 'later capture']
    };
  }

  const homeMove = lastHome - firstHome;
  const awayMove = lastAway - firstAway;
  const strongestMove = Math.max(Math.abs(homeMove), Math.abs(awayMove));
  const leadSide = lastHome >= lastAway ? sorted[sorted.length - 1].home || 'Home' : sorted[sorted.length - 1].away || 'Away';
  const sourceQuality = Math.min(20, sources.size * 6);
  const movementQuality = Math.min(30, strongestMove * 300);
  const score = clamp(45 + sourceQuality + movementQuality);
  const signals: InqsiSignalName[] = ['STABILITY'];

  if (strongestMove >= 0.018) signals.push('STEAM');
  if (strongestMove < 0.006) signals.push('COMPRESSION');
  if (homeMove * awayMove > 0) signals.push('CHAOS');
  if (Math.abs(homeMove - awayMove) > 0.035) signals.push('MOMENTUM');

  const stability = score >= 75 ? 'HIGH' : score >= 58 ? 'MODERATE' : 'FRAGILE';

  return {
    status: 'ready',
    signalScore: score,
    stability,
    lean: leadSide,
    signals,
    explanation: `InQsi leans ${leadSide} based on verified multi-source movement. This is a market signal, not a guarantee.`,
    whatToWatch: ['late source agreement', 'movement reversal', 'context changes']
  };
}

export function buildThreeSelectionRanking(results: InqsiSignalResult[]) {
  if (results.length !== 3 || results.some((result) => result.status !== 'ready' || result.signalScore === null)) {
    return {
      status: 'working_on_it' as const,
      message: 'Working on it. Three verified signals are required before ranking all 8 combinations.',
      rankings: [] as Array<{ rank: number; label: string; score: number }>
    };
  }

  const baseScores = results.map((result) => result.signalScore || 0);
  const combos = Array.from({ length: 8 }, (_, index) => {
    const bits = index.toString(2).padStart(3, '0').split('').map(Number);
    const score = baseScores.reduce((sum, value, legIndex) => sum + (bits[legIndex] ? 100 - value : value), 0) / 3;
    return { label: `Combination ${index + 1}`, score: clamp(score), rank: 0 };
  }).sort((a, b) => b.score - a.score).map((combo, index) => ({ ...combo, rank: index + 1 }));

  return {
    status: 'ready' as const,
    message: 'Ranking complete from verified signal scores.',
    rankings: combos
  };
}
