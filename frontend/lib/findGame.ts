import { slugPart } from '@/lib/kickoff';

const NICK: Record<string, string> = {
  cubs: 'cubs',
  padres: 'padres',
  yankees: 'yankees',
  sox: 'sox',
  jays: 'jays',
  phillies: 'phillies',
  braves: 'braves',
  astros: 'astros',
  dodgers: 'dodgers',
  giants: 'giants',
  mets: 'mets',
};

const CITY_ONLY = new Set(['chicago', 'new', 'york', 'boston', 'san', 'diego', 'los', 'angeles', 'la', 'ny']);

function tokens(value?: string) {
  return slugPart(value).split('-').filter(Boolean);
}

function teamKeys(value?: string) {
  const parts = tokens(value);
  const last = [...parts].reverse().find((part) => part.length > 2 && !CITY_ONLY.has(part)) || parts[parts.length - 1] || '';
  const nick = NICK[last] || last;
  return new Set([slugPart(value), last, nick].filter(Boolean));
}

function slugHasTeam(id: string, team?: string) {
  const keys = teamKeys(team);
  const padded = `-${id}-`;
  return Array.from(keys).some((key) => key.length > 2 && !CITY_ONLY.has(key) && padded.includes(`-${key}-`));
}

export function findGame<T extends { id?: string; game_id?: string; league?: string; sport_key?: string; away_team?: string; home_team?: string; matchup?: string }>(games: T[], gameId: string) {
  const id = slugPart(decodeURIComponent(gameId || '').split('|')[0]);
  if (!id) return undefined;

  const exact = games.find((game) => {
    const away = slugPart(game.away_team);
    const home = slugPart(game.home_team);
    const keys = [
      slugPart(game.id),
      slugPart(String(game.id || '').split('|')[0]),
      slugPart(game.game_id),
      slugPart(`${game.league}-${game.away_team}-${game.home_team}`),
      slugPart(`${game.sport_key}-${game.away_team}-${game.home_team}`),
    ].filter(Boolean);
    return keys.includes(id);
  });
  if (exact) return exact;

  return games.find((game) => {
    if (!game.away_team || !game.home_team) return false;
    return slugHasTeam(id, game.away_team) && slugHasTeam(id, game.home_team);
  });
}
