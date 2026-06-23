export const sports = [
  { slug: 'nfl', label: 'NFL', title: 'NFL Market Board', description: 'Live football moneyline, spread, total, and market signal board.' },
  { slug: 'cfb', label: 'CFB', title: 'College Football Market Board', description: 'College football odds, spread, total, and movement board.' },
  { slug: 'nba', label: 'NBA', title: 'NBA Market Board', description: 'Basketball odds, spread, total, and movement board.' },
  { slug: 'ncaam', label: 'NCAAM', title: 'College Basketball Market Board', description: 'College basketball odds, spread, total, and movement board.' },
  { slug: 'nhl', label: 'NHL', title: 'NHL Market Board', description: 'Hockey odds, puck line, total, and movement board.' },
  { slug: 'mlb', label: 'MLB', title: 'MLB Market Board', description: 'Baseball moneyline, run line, total, and movement board.' },
  { slug: 'wnba', label: 'WNBA', title: 'WNBA Market Board', description: 'WNBA odds, spread, total, and movement board.' },
  { slug: 'soccer', label: 'Soccer', title: 'Soccer Market Board', description: 'Soccer market data and live board.' },
  { slug: 'tennis', label: 'Tennis', title: 'Tennis Match Board', description: 'Tennis match odds and market board.' }
] as const;

export type SportSlug = typeof sports[number]['slug'];

const leagueToSportSlug: Record<string, SportSlug> = {
  nfl: 'nfl',
  cfb: 'cfb',
  ncaaf: 'cfb',
  'college football': 'cfb',
  nba: 'nba',
  wnba: 'wnba',
  ncaam: 'ncaam',
  ncaab: 'ncaam',
  nhl: 'nhl',
  mlb: 'mlb',
  tennis: 'tennis',
  soccer: 'soccer'
};

export function getSportBySlug(slug: string) {
  return sports.find((sport) => sport.slug === slug);
}

export function getSportSlugForLeague(league: string): SportSlug | string {
  const normalized = String(league || '').toLowerCase().trim();
  return leagueToSportSlug[normalized] ?? normalized.replace(/\s+/g, '-');
}
