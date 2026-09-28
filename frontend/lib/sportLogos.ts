const ESPN = 'https://a.espncdn.com/i/teamlogos';

export const SPORT_LOGO: Record<string, string> = {
  MLB: `${ESPN}/leagues/500/mlb.png`,
  NBA: `${ESPN}/leagues/500/nba.png`,
  NFL: `${ESPN}/leagues/500/nfl.png`,
  NHL: `${ESPN}/leagues/500/nhl.png`,
  NCAAB: `${ESPN}/leagues/500/ncaa.png`,
  NCAAM: `${ESPN}/leagues/500/ncaa.png`,
  NCAAF: `${ESPN}/leagues/500/ncaa.png`,
  CFB: `${ESPN}/leagues/500/ncaa.png`,
  WNBA: `${ESPN}/leagues/500/wnba.png`,
  MLS: `${ESPN}/leagues/500/mls.png`,
  SOCCER: `${ESPN}/leagues/500/fifa.wwc.png`,
  UFC: `${ESPN}/leagues/500/ufc.png`,
  MMA: `${ESPN}/leagues/500/ufc.png`,
  TENNIS: `${ESPN}/leagues/500/atp.png`,
  GOLF: `${ESPN}/leagues/500/pga.png`,
};

const TEAMS: Record<string, Record<string, string>> = {
  mlb: {
    yankees: 'nyy', 'new york yankees': 'nyy', 'red sox': 'bos', 'boston red sox': 'bos', sox: 'bos',
    dodgers: 'lad', 'los angeles dodgers': 'lad', padres: 'sd', 'san diego padres': 'sd',
    cubs: 'chc', 'chicago cubs': 'chc', 'white sox': 'chw', 'chicago white sox': 'chw',
    mets: 'nym', 'new york mets': 'nym', phillies: 'phi', 'philadelphia phillies': 'phi',
    braves: 'atl', 'atlanta braves': 'atl', astros: 'hou', 'houston astros': 'hou',
    rangers: 'tex', 'texas rangers': 'tex', mariners: 'sea', 'seattle mariners': 'sea',
    twins: 'min', 'minnesota twins': 'min', guardians: 'cle', 'cleveland guardians': 'cle',
    tigers: 'det', 'detroit tigers': 'det', royals: 'kc', 'kansas city royals': 'kc',
    angels: 'laa', 'los angeles angels': 'laa', athletics: 'oak', as: 'oak',
    giants: 'sf', 'san francisco giants': 'sf', rockies: 'col', 'colorado rockies': 'col',
    diamondbacks: 'ari', 'arizona diamondbacks': 'ari', dbacks: 'ari',
    nationals: 'wsh', 'washington nationals': 'wsh', marlins: 'mia', 'miami marlins': 'mia',
    pirates: 'pit', 'pittsburgh pirates': 'pit', brewers: 'mil', 'milwaukee brewers': 'mil',
    cardinals: 'stl', 'st louis cardinals': 'stl', 'st. louis cardinals': 'stl',
    orioles: 'bal', 'baltimore orioles': 'bal', rays: 'tb', 'tampa bay rays': 'tb',
    bluejays: 'tor', 'blue jays': 'tor', 'toronto blue jays': 'tor',
    reds: 'cin', 'cincinnati reds': 'cin',
  },
  nba: {
    lakers: 'lal', 'los angeles lakers': 'lal', warriors: 'gs', 'golden state warriors': 'gs',
    celtics: 'bos', 'boston celtics': 'bos', knicks: 'ny', 'new york knicks': 'ny',
    heat: 'mia', 'miami heat': 'mia', bucks: 'mil', 'milwaukee bucks': 'mil',
    nuggets: 'den', 'denver nuggets': 'den', thunder: 'okc', 'oklahoma city thunder': 'okc',
    mavericks: 'dal', mavs: 'dal', 'dallas mavericks': 'dal',
    suns: 'phx', 'phoenix suns': 'phx', clippers: 'lac', 'los angeles clippers': 'lac',
    kings: 'sac', 'sacramento kings': 'sac', blazers: 'por', 'trail blazers': 'por', 'portland trail blazers': 'por',
    wolves: 'min', 'timberwolves': 'min', 'minnesota timberwolves': 'min',
    pelicans: 'no', 'new orleans pelicans': 'no', rockets: 'hou', 'houston rockets': 'hou',
    spurs: 'sa', 'san antonio spurs': 'sa', grizzlies: 'mem', 'memphis grizzlies': 'mem',
    hawks: 'atl', 'atlanta hawks': 'atl', hornets: 'cha', 'charlotte hornets': 'cha',
    wizards: 'wsh', 'washington wizards': 'wsh', sixers: 'phi', '76ers': 'phi', 'philadelphia 76ers': 'phi',
    nets: 'bkn', 'brooklyn nets': 'bkn', raptors: 'tor', 'toronto raptors': 'tor',
    bulls: 'chi', 'chicago bulls': 'chi', pistons: 'det', 'detroit pistons': 'det',
    pacers: 'ind', 'indiana pacers': 'ind', cavs: 'cle', cavaliers: 'cle', 'cleveland cavaliers': 'cle',
    magic: 'orl', 'orlando magic': 'orl', jazz: 'utah', 'utah jazz': 'utah',
  },
  nfl: {
    chiefs: 'kc', 'kansas city chiefs': 'kc', bills: 'buf', 'buffalo bills': 'buf',
    ravens: 'bal', 'baltimore ravens': 'bal', steelers: 'pit', 'pittsburgh steelers': 'pit',
    bengals: 'cin', 'cincinnati bengals': 'cin', browns: 'cle', 'cleveland browns': 'cle',
    patriots: 'ne', 'new england patriots': 'ne', jets: 'nyj', 'new york jets': 'nyj',
    dolphins: 'mia', 'miami dolphins': 'mia', eagles: 'phi', 'philadelphia eagles': 'phi',
    cowboys: 'dal', 'dallas cowboys': 'dal', giants: 'nyg', 'new york giants': 'nyg',
    commanders: 'wsh', 'washington commanders': 'wsh', packers: 'gb', 'green bay packers': 'gb',
    bears: 'chi', 'chicago bears': 'chi', vikings: 'min', 'minnesota vikings': 'min',
    lions: 'det', 'detroit lions': 'det', '49ers': 'sf', 'san francisco 49ers': 'sf', niners: 'sf',
    rams: 'lar', 'los angeles rams': 'lar', seahawks: 'sea', 'seattle seahawks': 'sea',
    cardinals: 'ari', 'arizona cardinals': 'ari', saints: 'no', 'new orleans saints': 'no',
    buccaneers: 'tb', bucs: 'tb', 'tampa bay buccaneers': 'tb',
    falcons: 'atl', 'atlanta falcons': 'atl', panthers: 'car', 'carolina panthers': 'car',
    texans: 'hou', 'houston texans': 'hou', colts: 'ind', 'indianapolis colts': 'ind',
    jaguars: 'jax', jags: 'jax', 'jacksonville jaguars': 'jax',
    titans: 'ten', 'tennessee titans': 'ten', broncos: 'den', 'denver broncos': 'den',
    raiders: 'lv', 'las vegas raiders': 'lv', chargers: 'lac', 'los angeles chargers': 'lac',
  },
  nhl: {
    oilers: 'edm', 'edmonton oilers': 'edm', canucks: 'van', 'vancouver canucks': 'van',
    flames: 'cgy', 'calgary flames': 'cgy', bruins: 'bos', 'boston bruins': 'bos',
    rangers: 'nyr', 'new york rangers': 'nyr', islanders: 'nyi', 'new york islanders': 'nyi',
    devils: 'nj', 'new jersey devils': 'nj', flyers: 'phi', 'philadelphia flyers': 'phi',
    penguins: 'pit', 'pittsburgh penguins': 'pit', capitals: 'wsh', 'washington capitals': 'wsh',
    hurricanes: 'car', 'carolina hurricanes': 'car', lightning: 'tb', 'tampa bay lightning': 'tb',
    panthers: 'fla', 'florida panthers': 'fla', mapleleafs: 'tor', 'maple leafs': 'tor', 'toronto maple leafs': 'tor',
    canadiens: 'mtl', 'montreal canadiens': 'mtl', senators: 'ott', 'ottawa senators': 'ott',
    redwings: 'det', 'red wings': 'det', 'detroit red wings': 'det',
    blackhawks: 'chi', 'chicago blackhawks': 'chi', wild: 'min', 'minnesota wild': 'min',
    jets: 'wpg', 'winnipeg jets': 'wpg', avalanche: 'col', 'colorado avalanche': 'col',
    stars: 'dal', 'dallas stars': 'dal', predators: 'nsh', 'nashville predators': 'nsh',
    blues: 'stl', 'st louis blues': 'stl', 'st. louis blues': 'stl',
    coyotes: 'ari', 'utah hockey club': 'utah', mammoth: 'utah',
    knights: 'vgk', 'golden knights': 'vgk', 'vegas golden knights': 'vgk',
    kings: 'la', 'los angeles kings': 'la', ducks: 'ana', 'anaheim ducks': 'ana',
    sharks: 'sj', 'san jose sharks': 'sj', kraken: 'sea', 'seattle kraken': 'sea',
    bluejackets: 'cbj', 'blue jackets': 'cbj', 'columbus blue jackets': 'cbj',
    sabres: 'buf', 'buffalo sabres': 'buf',
  },
};

function leagueKey(sport: string) {
  const s = sport.toLowerCase();
  if (s.includes('baseball') || s === 'mlb') return 'mlb';
  if (s.includes('wnba')) return 'wnba';
  if (s.includes('nba') || s.includes('basketball')) return 'nba';
  if (s.includes('ncaaf') || s === 'cfb' || s.includes('football_ncaaf')) return 'college-football';
  if (s.includes('nfl') || s.includes('football')) return 'nfl';
  if (s.includes('nhl') || s.includes('hockey')) return 'nhl';
  if (s.includes('ncaab') || s.includes('ncaam')) return 'ncaa';
  return '';
}

function cleanName(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9.\s]/g, ' ').replace(/\s+/g, ' ').trim();
}

export function sportLogo(sport: string) {
  const key = Object.keys(SPORT_LOGO).find((k) => sport.toUpperCase().includes(k));
  return key ? SPORT_LOGO[key] : '';
}

export function teamLogo(sport: string, rawName: string) {
  const league = leagueKey(sport);
  const name = cleanName(rawName.replace(/[+-]?\d+(\.\d+)?/g, '').replace(/\b(over|under|ml|spread|total)\b/gi, ''));
  if (!name || !league || !TEAMS[league]) return '';
  const table = TEAMS[league];
  if (table[name]) return `${ESPN}/${league}/500/${table[name]}.png`;
  const hit = Object.keys(table).find((k) => name.includes(k) || k.includes(name));
  return hit ? `${ESPN}/${league}/500/${table[hit]}.png` : '';
}

export function eventTeams(event: string) {
  const parts = String(event || '').split(/\s+vs\.?\s+|\s+@\s+|\s+-\s+/i);
  if (parts.length >= 2) return [parts[0].trim(), parts[1].trim()];
  return [event, ''];
}
