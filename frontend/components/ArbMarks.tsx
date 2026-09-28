'use client';

import {useState} from 'react';

const LEAGUES: Record<string, string> = {
  mlb: 'https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png',
  nba: 'https://a.espncdn.com/i/teamlogos/leagues/500/nba.png',
  nfl: 'https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png',
  nhl: 'https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png',
  ncaab: 'https://a.espncdn.com/i/teamlogos/leagues/500/ncaa-mb.png',
  ncaaf: 'https://a.espncdn.com/i/teamlogos/leagues/500/ncaa-fb.png',
  wnba: 'https://a.espncdn.com/i/teamlogos/leagues/500/wnba.png',
  mls: 'https://a.espncdn.com/i/teamlogos/leagues/500/mls.png',
};

const TEAMS: Record<string, string> = {
  cubs: 'https://a.espncdn.com/i/teamlogos/mlb/500/chc.png',
  padres: 'https://a.espncdn.com/i/teamlogos/mlb/500/sd.png',
  'red sox': 'https://a.espncdn.com/i/teamlogos/mlb/500/bos.png',
  yankees: 'https://a.espncdn.com/i/teamlogos/mlb/500/nyy.png',
  dodgers: 'https://a.espncdn.com/i/teamlogos/mlb/500/lad.png',
  giants: 'https://a.espncdn.com/i/teamlogos/mlb/500/sf.png',
  phillies: 'https://a.espncdn.com/i/teamlogos/mlb/500/phi.png',
  braves: 'https://a.espncdn.com/i/teamlogos/mlb/500/atl.png',
  mets: 'https://a.espncdn.com/i/teamlogos/mlb/500/nym.png',
  twins: 'https://a.espncdn.com/i/teamlogos/mlb/500/min.png',
  astros: 'https://a.espncdn.com/i/teamlogos/mlb/500/hou.png',
  rangers: 'https://a.espncdn.com/i/teamlogos/mlb/500/tex.png',
  mariners: 'https://a.espncdn.com/i/teamlogos/mlb/500/sea.png',
  angels: 'https://a.espncdn.com/i/teamlogos/mlb/500/laa.png',
  athletics: 'https://a.espncdn.com/i/teamlogos/mlb/500/oak.png',
  royals: 'https://a.espncdn.com/i/teamlogos/mlb/500/kc.png',
  tigers: 'https://a.espncdn.com/i/teamlogos/mlb/500/det.png',
  'white sox': 'https://a.espncdn.com/i/teamlogos/mlb/500/chw.png',
  guardians: 'https://a.espncdn.com/i/teamlogos/mlb/500/cle.png',
  orioles: 'https://a.espncdn.com/i/teamlogos/mlb/500/bal.png',
  rays: 'https://a.espncdn.com/i/teamlogos/mlb/500/tb.png',
  'blue jays': 'https://a.espncdn.com/i/teamlogos/mlb/500/tor.png',
  nationals: 'https://a.espncdn.com/i/teamlogos/mlb/500/wsh.png',
  marlins: 'https://a.espncdn.com/i/teamlogos/mlb/500/mia.png',
  rockies: 'https://a.espncdn.com/i/teamlogos/mlb/500/col.png',
  diamondbacks: 'https://a.espncdn.com/i/teamlogos/mlb/500/ari.png',
  brewers: 'https://a.espncdn.com/i/teamlogos/mlb/500/mil.png',
  cardinals: 'https://a.espncdn.com/i/teamlogos/mlb/500/stl.png',
  pirates: 'https://a.espncdn.com/i/teamlogos/mlb/500/pit.png',
  reds: 'https://a.espncdn.com/i/teamlogos/mlb/500/cin.png',
  nuggets: 'https://a.espncdn.com/i/teamlogos/nba/500/den.png',
  lakers: 'https://a.espncdn.com/i/teamlogos/nba/500/lal.png',
  warriors: 'https://a.espncdn.com/i/teamlogos/nba/500/gs.png',
  celtics: 'https://a.espncdn.com/i/teamlogos/nba/500/bos.png',
  knicks: 'https://a.espncdn.com/i/teamlogos/nba/500/ny.png',
  heat: 'https://a.espncdn.com/i/teamlogos/nba/500/mia.png',
  chiefs: 'https://a.espncdn.com/i/teamlogos/nfl/500/kc.png',
  bills: 'https://a.espncdn.com/i/teamlogos/nfl/500/buf.png',
  ravens: 'https://a.espncdn.com/i/teamlogos/nfl/500/bal.png',
  steelers: 'https://a.espncdn.com/i/teamlogos/nfl/500/pit.png',
};

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase() || '').join('') || '?';
}

function leagueKey(sport?: string) {
  const key = String(sport || '').toLowerCase();
  if (key.includes('ncaa') && (key.includes('fb') || key.includes('cfb') || key.includes('football'))) return 'ncaaf';
  if (key.includes('ncaa') || key.includes('ncaab') || key.includes('ncaam')) return 'ncaab';
  if (key.includes('wnba')) return 'wnba';
  if (key.includes('nba')) return 'nba';
  if (key.includes('nfl')) return 'nfl';
  if (key.includes('nhl')) return 'nhl';
  if (key.includes('mlb') || key.includes('baseball')) return 'mlb';
  if (key.includes('mls') || key.includes('soccer')) return 'mls';
  return '';
}

export function LeagueMark({sport}: {sport?: string}) {
  const key = leagueKey(sport);
  const src = key ? LEAGUES[key] : '';
  const [ok, setOk] = useState(Boolean(src));
  if (src && ok) return <img className="arb-mark league" alt="" src={src} onError={() => setOk(false)} />;
  return <span className="arb-mark fallback">{initials(sport || 'IN')}</span>;
}

export function TeamMark({name, sport}: {name: string; sport?: string}) {
  const key = name.toLowerCase();
  const hit = Object.keys(TEAMS).find((k) => key.includes(k));
  const [ok, setOk] = useState(Boolean(hit));
  if (hit && ok) return <img className="arb-mark" alt="" src={TEAMS[hit]} onError={() => setOk(false)} />;
  return <span className="arb-mark fallback" data-sport={sport}>{initials(name)}</span>;
}

export function BookMark({name}: {name: string}) {
  return <span className="book-mark">{name}</span>;
}
