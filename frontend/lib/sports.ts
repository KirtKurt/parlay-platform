export const sports = [
  { slug: 'nfl', label: 'NFL', title: 'NFL Market Board', description: 'Professional football slate intelligence, T-snapshots, anchor grades, and parlay containment.' },
  { slug: 'cfb', label: 'CFB', title: 'College Football Board', description: 'College football market movement, public fade checks, and no-overlap parlay construction.' },
  { slug: 'nba', label: 'NBA', title: 'NBA Market Board', description: 'Basketball line movement, dual-book confirmation, late steam, and weak-leg hedge logic.' },
  { slug: 'ncaam', label: 'NCAAM', title: 'NCAAM Board', description: 'Compressed college basketball markets with Top-4 hedge visibility when variance is high.' },
  { slug: 'nhl', label: 'NHL', title: 'NHL Market Board', description: 'Hockey market compression, goalie/travel notes, and Top-5 compressed containment rules.' },
  { slug: 'mlb', label: 'MLB', title: 'MLB Market Board', description: 'Baseball moneyline, run-line, total movement, and pitcher/news sensitivity tracking.' },
  { slug: 'tennis', label: 'TENNIS', title: 'Tennis Match Board', description: 'Singles and doubles match intelligence with anchor plus coin-flip slate construction.' },
  { slug: 'soccer', label: 'SOCCER', title: 'Soccer 3-Way Board', description: '3-way market intelligence across win, draw, and loss outcomes with 27-combo framing.' },
  { slug: 'darts', label: 'DARTS', title: 'Darts Match Board', description: 'Leg/set-format market movement, favorite hold checks, short-match volatility, and checkout-pressure flags.' },
  { slug: 'lacrosse', label: 'LACROSSE', title: 'Lacrosse Market Board', description: 'Moneyline, spread, total, goalie/news sensitivity, and late market compression for college and pro lacrosse.' },
  { slug: 'table-tennis', label: 'TABLE TENNIS', title: 'Table Tennis Match Board', description: 'Fast-cycle match intelligence for ML movement, format variance, short-market volatility, and anomaly alerts.' }
] as const;

export type SportSlug = typeof sports[number]['slug'];

const leagueToSportSlug: Record<string, SportSlug> = {
  nfl: 'nfl',
  cfb: 'cfb',
  nba: 'nba',
  ncaam: 'ncaam',
  nhl: 'nhl',
  mlb: 'mlb',
  tennis: 'tennis',
  soccer: 'soccer',
  darts: 'darts',
  lacrosse: 'lacrosse',
  lax: 'lacrosse',
  'table tennis': 'table-tennis',
  tabletennis: 'table-tennis',
  'table-tennis': 'table-tennis',
  pingpong: 'table-tennis',
  'ping pong': 'table-tennis'
};

export function getSportBySlug(slug: string) {
  return sports.find((sport) => sport.slug === slug);
}

export function getSportSlugForLeague(league: string): SportSlug | string {
  const normalized = league.toLowerCase().trim();
  return leagueToSportSlug[normalized] ?? normalized.replace(/\s+/g, '-');
}
