export const sports = [
  { slug: 'nfl', label: 'NFL', title: 'NFL Market Board', description: 'Professional football slate intelligence, T-snapshots, anchor grades, and parlay containment.' },
  { slug: 'cfb', label: 'CFB', title: 'College Football Board', description: 'College football market movement, public fade checks, and no-overlap parlay construction.' },
  { slug: 'nba', label: 'NBA', title: 'NBA Market Board', description: 'Basketball line movement, dual-book confirmation, late steam, and weak-leg hedge logic.' },
  { slug: 'ncaam', label: 'NCAAM', title: 'NCAAM Board', description: 'Compressed college basketball markets with Top-4 hedge visibility when variance is high.' },
  { slug: 'nhl', label: 'NHL', title: 'NHL Market Board', description: 'Hockey market compression, weather/travel notes, and Top-5 compressed containment rules.' },
  { slug: 'mlb', label: 'MLB', title: 'MLB Market Board', description: 'Baseball moneyline, run-line, total movement, and pitcher/news sensitivity tracking.' },
  { slug: 'tennis', label: 'TENNIS', title: 'Tennis Match Board', description: 'Singles and doubles match intelligence with anchor plus coin-flip slate construction.' },
  { slug: 'soccer', label: 'SOCCER', title: 'Soccer 3-Way Board', description: '3-way market intelligence across win, draw, and loss outcomes with 27-combo framing.' }
] as const;

export type SportSlug = typeof sports[number]['slug'];

export function getSportBySlug(slug: string) {
  return sports.find((sport) => sport.slug === slug);
}

export function getSportSlugForLeague(league: string) {
  return league.toLowerCase();
}
