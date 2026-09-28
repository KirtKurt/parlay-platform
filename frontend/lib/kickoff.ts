export function formatKickoff(value?: string | null) {
  if (!value || value === 'TBD' || value === 'Waiting') return 'Waiting';
  const stamp = Date.parse(value);
  if (!Number.isFinite(stamp)) return 'Waiting';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(new Date(stamp));
}

export function impliedPercent(value?: number | string | null) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return null;
  const decimal = n > 0 ? 1 + n / 100 : 1 + 100 / Math.abs(n);
  return Math.round(100 / decimal);
}

export function formatAmericanOdds(value?: number | string | null) {
  if (value === undefined || value === null || value === '') return '';
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n > 0 ? `+${n}` : String(n);
}
