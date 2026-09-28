'use client';

const REASON: Record<string, string> = {
  EVENT_OR_MARKET_SCOPE_UNREVIEWED: 'Settlement rules on radar',
  SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE: 'House rules under review',
  RULES_UNKNOWN: 'Rules status on radar',
  FRESHNESS: 'Quote freshness on radar',
  MATH_ARB: 'Cross-book mismatch on radar',
};

function humanize(code?: string) {
  if (!code) return '';
  const key = String(code).trim().toUpperCase().replace(/\s+/g, '_');
  if (REASON[key]) return REASON[key];
  const clean = String(code).replace(/_/g, ' ').trim();
  if (!clean) return '';
  return clean.charAt(0).toUpperCase() + clean.slice(1).toLowerCase() + ' on radar';
}

export function RadarChip({ label, tone = 'live' }: { label: string; tone?: 'live' | 'held' | 'wait' }) {
  if (!label) return null;
  return <em className={'radar-chip ' + tone}>{label}</em>;
}

export function radarFromArb(row: {
  status?: string;
  market?: string;
  reason?: string;
  executable?: boolean;
  mathArb?: boolean;
  verifiedArb?: boolean;
  books?: number;
  start?: string;
}) {
  const chips: Array<{ label: string; tone: 'live' | 'held' | 'wait' }> = [];
  chips.push({ label: 'Live quotes', tone: 'live' });
  if (row.start) chips.push({ label: 'Kickoff on radar', tone: 'live' });
  if (row.market) chips.push({ label: `${String(row.market).replace(/_/g, ' ')} market`, tone: 'live' });
  if (row.status === 'HELD') chips.push({ label: 'Held for review', tone: 'held' });
  if (row.status === 'VERIFIED') chips.push({ label: 'Verified executable', tone: 'live' });
  if (row.mathArb && row.verifiedArb === false) chips.push({ label: 'Cross-book mismatch on radar', tone: 'held' });
  if (row.executable === false) chips.push({ label: 'Not executable yet', tone: 'held' });
  const reason = humanize(row.reason);
  if (reason) chips.push({ label: reason, tone: 'held' });
  if (row.books && row.books > 0) chips.push({ label: `${row.books} books on radar`, tone: 'live' });
  return chips;
}

export function RadarStrip({ chips }: { chips: Array<{ label: string; tone?: 'live' | 'held' | 'wait' }> }) {
  if (!chips.length) return <p className="radar-empty">Waiting on live signals.</p>;
  return (
    <div className="radar-strip" aria-label="Signals on radar">
      <small>Signals on radar</small>
      <div>{chips.map((chip) => <RadarChip key={chip.label} label={chip.label} tone={chip.tone || 'live'} />)}</div>
    </div>
  );
}
