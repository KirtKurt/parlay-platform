import { slugPart } from '@/lib/kickoff';

const NICK: Record<string, string> = {
  cubs: 'chicago-cubs',
  padres: 'san-diego-padres',
  yankees: 'new-york-yankees',
  'red-sox': 'boston-red-sox',
  'white-sox': 'chicago-white-sox',
  'blue-jays': 'toronto-blue-jays',
  jays: 'toronto-blue-jays',
  phillies: 'philadelphia-phillies',
  braves: 'atlanta-braves',
  astros: 'houston-astros',
  dodgers: 'los-angeles-dodgers',
  giants: 'san-francisco-giants',
  mets: 'new-york-mets',
};

function lastName(value?: string) {
  const slug = slugPart(value);
  return slug.split('-').filter((part) => part.length > 3).pop() || slug;
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
      slugPart(game.matchup),
      `${away}-${home}`,
      `${home}-${away}`,
    ];
    return keys.includes(id);
  });
  if (exact) return exact;

  return games.find((game) => {
    const away = slugPart(game.away_team);
    const home = slugPart(game.home_team);
    if (!away || !home) return false;
    if (id.includes(away) && id.includes(home)) return true;
    const awayShort = lastName(game.away_team);
    const homeShort = lastName(game.home_team);
    if (!awayShort || !homeShort || awayShort === homeShort) return false;
    const awayFull = NICK[awayShort] || away;
    const homeFull = NICK[homeShort] || home;
    return id.includes(awayShort) && id.includes(homeShort) && (id.includes(awayFull) || id.includes(awayShort)) && (id.includes(homeFull) || id.includes(homeShort));
  });
}
