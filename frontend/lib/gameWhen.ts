export function formatKickoff(iso?: string | null) {
  if (!iso) return '';
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return '';
  return new Intl.DateTimeFormat('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZone: 'America/New_York',
  }).format(d) + ' ET';
}

export function american(n: number) {
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? `+${n}` : String(n);
}

export function marketFavorite(home?: string, away?: string, homeMl?: number, awayMl?: number) {
  if (!Number.isFinite(homeMl) || !Number.isFinite(awayMl) || !home || !away) return null;
  const homeFav = Number(homeMl) <= Number(awayMl);
  return {
    name: homeFav ? home : away,
    odds: homeFav ? Number(homeMl) : Number(awayMl),
    label: homeFav ? 'Home favorite' : 'Away favorite',
  };
}
