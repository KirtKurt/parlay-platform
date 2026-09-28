export const DEFAULT_TIME_ZONE = 'America/New_York';

export function isValidTimeZone(value?: string | null): value is string {
  const zone = String(value || '').trim();
  if (!zone || zone.length > 64 || /[\s<>"']/.test(zone)) return false;
  try {
    Intl.DateTimeFormat('en-US', { timeZone: zone }).format(0);
    return true;
  } catch {
    return false;
  }
}

export function resolveTimeZone(value?: string | null) {
  return isValidTimeZone(value) ? String(value).trim() : DEFAULT_TIME_ZONE;
}

export function formatKickoff(value?: string | null, timeZone?: string | null) {
  if (!value || value === 'TBD' || value === 'Waiting') return 'Waiting';
  if (/\.\d{3}Z$/.test(value)) return 'Waiting';
  const stamp = Date.parse(value);
  if (!Number.isFinite(stamp)) return 'Waiting';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: resolveTimeZone(timeZone),
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(new Date(stamp));
}

export function formatLocalClock(value: Date | string | number = new Date(), timeZone?: string | null) {
  const stamp = value instanceof Date ? value.getTime() : Date.parse(String(value));
  if (!Number.isFinite(stamp)) return 'Waiting';
  return new Intl.DateTimeFormat('en-US', {
    timeZone: resolveTimeZone(timeZone),
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

export function slugPart(value?: string | null) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

export function gamePath(game: { id?: string; game_id?: string; league?: string; sport_key?: string; away_team?: string; home_team?: string; matchup?: string }) {
  const league = slugPart(game.league || game.sport_key || 'sport');
  const away = slugPart(game.away_team);
  const home = slugPart(game.home_team);
  if (league && away && home) return `/game/${league}-${away}-${home}`;
  const matchup = slugPart(game.matchup);
  if (matchup) return `/game/${matchup}`;
  const raw = String(game.id || game.game_id || '').split('|')[0];
  const id = slugPart(raw);
  return id ? `/game/${id}` : '/sports';
}
