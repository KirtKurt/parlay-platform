import { slugPart } from '@/lib/kickoff';

const CITY_ONLY = new Set(['chicago', 'new', 'york', 'boston', 'san', 'diego', 'los', 'angeles', 'la', 'ny']);

function tokens(value?: string) {
  return slugPart(value).split('-').filter(Boolean);
}

function nick(value?: string) {
  const parts = tokens(value);
  return [...parts].reverse().find((part) => part.length > 2 && !CITY_ONLY.has(part)) || '';
}

function constructedSlug(game: { id?: string; game_id?: string; league?: string; sport_key?: string; away_team?: string; home_team?: string }) {
  return slugPart(`${game.league || game.sport_key || ''}-${game.away_team || ''}-${game.home_team || ''}`);
}

export function findGame<T extends { id?: string; game_id?: string; league?: string; sport_key?: string; away_team?: string; home_team?: string; matchup?: string }>(games: T[], gameId: string) {
  const id = slugPart(decodeURIComponent(gameId || '').split('|')[0]);
  if (!id) return undefined;

  const exact = games.find((game) => {
    const keys = [
      slugPart(game.id),
      slugPart(String(game.id || '').split('|')[0]),
      slugPart(game.game_id),
      constructedSlug(game),
    ].filter(Boolean);
    return keys.includes(id);
  });
  if (exact) return exact;

  return games.find((game) => {
    if (!game.away_team || !game.home_team) return false;
    const away = nick(game.away_team);
    const home = nick(game.home_team);
    if (!away || !home || away === home) return false;
    const padded = `-${id}-`;
    return padded.includes(`-${away}-`) && padded.includes(`-${home}-`);
  });
}
