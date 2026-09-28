export type ArbStatus = 'VERIFIED' | 'HELD';
export type ArbLeg = {book: string; bet: string; odds: number; lastUpdate?: string; limit?: number};
export type ArbRow = {
  id: string;
  sport: string;
  event: string;
  market: string;
  roi: number;
  age: number;
  legs: ArbLeg[];
  status: ArbStatus;
  reason?: string;
};

const dec = (a: number) => Math.abs(a) >= 100 ? (a > 0 ? 1 + a / 100 : 1 + 100 / Math.abs(a)) : 0;

export function roiFromLegs(legs: ArbLeg[]) {
  const s = legs.reduce((n, l) => {
    const d = dec(l.odds);
    return n + (d ? 1 / d : 99);
  }, 0);
  return s > 0 ? 100 * (1 / s - 1) : 0;
}

function ageFrom(legs: any[], fallbackMs?: number) {
  const now = Date.now();
  const ages = legs
    .map((l) => Date.parse(String(l?.last_update || l?.lastUpdate || '')))
    .filter(Number.isFinite)
    .map((t) => Math.max(0, Math.floor((now - t) / 1000)));
  if (ages.length) return Math.max(...ages);
  return fallbackMs ? Math.max(0, Math.floor((now - fallbackMs) / 1000)) : 0;
}

function toLegs(row: any): ArbLeg[] {
  const raw = Array.isArray(row?.legs) ? row.legs : Array.isArray(row?.quotes) ? row.quotes : [];
  return raw
    .map((x: any) => ({
      book: String(x?.book || x?.bookmaker || 'Book'),
      bet: String(x?.outcome || x?.name || 'Outcome'),
      odds: Number(x?.american ?? x?.american_odds ?? x?.price ?? x?.odds ?? 0),
      lastUpdate: x?.last_update ? String(x.last_update) : undefined,
      limit: Number.isFinite(Number(x?.limit)) ? Number(x.limit) : undefined,
    }))
    .filter((x: ArbLeg) => x.odds !== 0);
}

function pushRows(found: ArbRow[], rows: any[], status: ArbStatus, sportFallback: string, createdAt?: number) {
  for (const row of rows || []) {
    const legs = toLegs(row);
    if (legs.length < 2) continue;
    found.push({
      id: String(row.market_id || row.event_id || row.event || found.length),
      sport: String(row.sport || sportFallback || 'Sport').toUpperCase(),
      event: String(row.event || row.event_id || 'Market opportunity'),
      market: String(row.market || 'Market'),
      roi: Number(row.margin_pct ?? roiFromLegs(legs)),
      age: ageFrom(Array.isArray(row.legs) ? row.legs : row.quotes || [], createdAt),
      legs,
      status,
      reason: row?.validation?.settlement_reason || row?.reason,
    });
  }
}

export function parseArbOpportunities(data: any): ArbRow[] {
  const found: ArbRow[] = [];
  pushRows(found, data?.hits, 'VERIFIED', data?.source || 'SPORT', Date.now());
  pushRows(found, data?.held || data?.detected_unverified, 'HELD', data?.source || 'SPORT', Date.now());
  for (const scan of Array.isArray(data?.history) ? data.history : []) {
    const p = scan?.payload || scan?.data || scan || {};
    pushRows(found, p.hits, 'VERIFIED', p.sport || 'SPORT', Number(scan?.created_at_ms) || Date.now());
    pushRows(found, p.detected_unverified || p.held, 'HELD', p.sport || 'SPORT', Number(scan?.created_at_ms) || Date.now());
  }
  const seen = new Set<string>();
  return found
    .filter((row) => {
      if (seen.has(row.id)) return false;
      seen.add(row.id);
      return true;
    })
    .sort((a, b) => b.roi - a.roi)
    .slice(0, 100);
}
