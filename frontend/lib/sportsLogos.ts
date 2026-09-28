const ESPN = 'https://a.espncdn.com/i/teamlogos';

const SPORT_MARK: Record<string, {league: string; src: string}> = {
  MLB: {league: 'mlb', src: `${ESPN}/leagues/500/mlb.png`},
  NBA: {league: 'nba', src: `${ESPN}/leagues/500/nba.png`},
  NFL: {league: 'nfl', src: `${ESPN}/leagues/500/nfl.png`},
  NHL: {league: 'nhl', src: `${ESPN}/leagues/500/nhl.png`},
  NCAAB: {league: 'ncaa', src: `${ESPN}/ncaa_football/500/ncaa.png`},
  NCAAF: {league: 'ncaa', src: `${ESPN}/ncaa_football/500/ncaa.png`},
  WNBA: {league: 'wnba', src: `${ESPN}/leagues/500/wnba.png`},
  MLS: {league: 'mls', src: `${ESPN}/leagues/500/mls.png`},
  SOCCER: {league: 'fifa', src: `${ESPN}/leagues/500/fifa.png`},
  TENNIS: {league: 'atp', src: `${ESPN}/leagues/500/atp.png`},
  UFC: {league: 'mma', src: `${ESPN}/leagues/500/ufc.png`},
};

const TEAM: Record<string, Record<string, string>> = {
  mlb: {
    diamondbacks: 'ari', arizona: 'ari', braves: 'atl', atlanta: 'atl', orioles: 'bal', baltimore: 'bal',
    'red sox': 'bos', boston: 'bos', cubs: 'chc', 'chicago cubs': 'chc', 'white sox': 'chw', 'chicago white sox': 'chw',
    reds: 'cin', cincinnati: 'cin', guardians: 'cle', cleveland: 'cle', rockies: 'col', colorado: 'col',
    tigers: 'det', detroit: 'det', astros: 'hou', houston: 'hou', royals: 'kc', 'kansas city': 'kc',
    angels: 'laa', dodgers: 'lad', 'los angeles dodgers': 'lad', marlins: 'mia', miami: 'mia',
    brewers: 'mil', milwaukee: 'mil', twins: 'min', minnesota: 'min', mets: 'nym', yankees: 'nyy',
    'new york yankees': 'nyy', athletics: 'oak', oakland: 'oak', phillies: 'phi', philadelphia: 'phi',
    pirates: 'pit', pittsburgh: 'pit', padres: 'sd', 'san diego': 'sd', 'san diego padres': 'sd',
    giants: 'sf', 'san francisco': 'sf', mariners: 'sea', seattle: 'sea', cardinals: 'stl', 'st louis': 'stl',
    rays: 'tb', 'tampa bay': 'tb', rangers: 'tex', texas: 'tex', 'blue jays': 'tor', toronto: 'tor',
    nationals: 'wsh', washington: 'wsh',
  },
  nba: {
    hawks: 'atl', nets: 'bkn', celtics: 'bos', hornets: 'cha', bulls: 'chi', cavaliers: 'cle',
    mavericks: 'dal', nuggets: 'den', pistons: 'det', warriors: 'gs', rockets: 'hou', pacers: 'ind',
    clippers: 'lac', lakers: 'lal', grizzlies: 'mem', heat: 'mia', bucks: 'mil', timberwolves: 'min',
    pelicans: 'no', knicks: 'ny', thunder: 'okc', magic: 'orl', 76ers: 'phi', sixers: 'phi',
    suns: 'phx', trailblazers: 'por', 'trail blazers': 'por', kings: 'sac', spurs: 'sa',
    raptors: 'tor', jazz: 'utah', wizards: 'wsh',
  },
  nfl: {
    cardinals: 'ari', falcons: 'atl', ravens: 'bal', bills: 'buf', panthers: 'car', bears: 'chi',
    bengals: 'cin', browns: 'cle', cowboys: 'dal', broncos: 'den', lions: 'det', packers: 'gb',
    texans: 'hou', colts: 'ind', jaguars: 'jax', chiefs: 'kc', raiders: 'lv', chargers: 'lac',
    rams: 'lar', dolphins: 'mia', vikings: 'min', patriots: 'ne', saints: 'no', giants: 'nyg',
    jets: 'nyj', eagles: 'phi', steelers: 'pit', seahawks: 'sea', '49ers': 'sf', niners: 'sf',
    buccaneers: 'tb', titans: 'ten', commanders: 'wsh',
  },
  nhl: {
    ducks: 'ana', coyotes: 'ari', bruins: 'bos', sabres: 'buf', flames: 'cgy', hurricanes: 'car',
    'black hawks': 'chi', blackhawks: 'chi', avalanche: 'col', 'blue jackets': 'cbj', stars: 'dal',
    'red wings': 'det', oilers: 'edm', panthers: 'fla', kings: 'la', wild: 'min', canadiens: 'mtl',
    predators: 'nsh', devils: 'nj', islanders: 'nyi', rangers: 'nyr', senators: 'ott', flyers: 'phi',
    penguins: 'pit', sharks: 'sj', kraken: 'sea', blues: 'stl', lightning: 'tb', 'maple leafs': 'tor',
    canucks: 'van', 'golden knights': 'vgk', capitals: 'wsh', jets: 'wpg',
  },
};

function norm(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
}

export function sportMark(sport: string) {
  const key = String(sport || '').toUpperCase().replace(/[^A-Z]/g, '');
  const mapped = key.includes('BASEBALL') ? 'MLB'
    : key.includes('BASKETBALL') && key.includes('W') ? 'WNBA'
    : key.includes('BASKETBALL') && key.includes('NCAA') ? 'NCAAB'
    : key.includes('FOOTBALL') && key.includes('NCAA') ? 'NCAAF'
    : key.includes('HOCKEY') ? 'NHL'
    : key.startsWith('SOCCER') || key === 'EPL' ? 'SOCCER'
    : SPORT_MARK[key] ? key : key.slice(0, 5);
  return SPORT_MARK[mapped] || SPORT_MARK[key] || null;
}

export function teamLogo(sport: string, rawName: string) {
  const mark = sportMark(sport);
  if (!mark) return null;
  const league = mark.league;
  const table = TEAM[league];
  if (!table) return null;
  const cleaned = norm(rawName.replace(/\s+@\s+/g, ' ').replace(/\s+vs\.?\s+/g, ' '));
  const keys = Object.keys(table).sort((a, b) => b.length - a.length);
  const hit = keys.find((k) => cleaned.includes(k));
  if (!hit) return null;
  return `${ESPN}/${league}/500/${table[hit]}.png`;
}

export function eventTeams(event: string) {
  const text = String(event || '');
  const parts = text.split(/\s+@\s+|\s+vs\.?\s+/i).map((x) => x.trim()).filter(Boolean);
  if (parts.length >= 2) return [parts[0], parts[1]];
  return [text, ''];
}
