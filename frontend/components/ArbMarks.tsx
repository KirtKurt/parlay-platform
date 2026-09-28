'use client';

import {useState} from 'react';

const TEAMS: Record<string, string> = {
  'nuggets': 'https://a.espncdn.com/i/teamlogos/nba/500/den.png',
  'lakers': 'https://a.espncdn.com/i/teamlogos/nba/500/lal.png',
  'yankees': 'https://a.espncdn.com/i/teamlogos/mlb/500/nyy.png',
  'red sox': 'https://a.espncdn.com/i/teamlogos/mlb/500/bos.png',
  'cubs': 'https://a.espncdn.com/i/teamlogos/mlb/500/chc.png',
  'padres': 'https://a.espncdn.com/i/teamlogos/mlb/500/sd.png',
  'dodgers': 'https://a.espncdn.com/i/teamlogos/mlb/500/lad.png',
  'giants': 'https://a.espncdn.com/i/teamlogos/mlb/500/sf.png',
  'phillies': 'https://a.espncdn.com/i/teamlogos/mlb/500/phi.png',
  'braves': 'https://a.espncdn.com/i/teamlogos/mlb/500/atl.png',
  'warriors': 'https://a.espncdn.com/i/teamlogos/nba/500/gs.png',
  'celtics': 'https://a.espncdn.com/i/teamlogos/nba/500/bos.png',
  'knicks': 'https://a.espncdn.com/i/teamlogos/nba/500/ny.png',
  'heat': 'https://a.espncdn.com/i/teamlogos/nba/500/mia.png',
  'chiefs': 'https://a.espncdn.com/i/teamlogos/nfl/500/kc.png',
  'bills': 'https://a.espncdn.com/i/teamlogos/nfl/500/buf.png',
  'ravens': 'https://a.espncdn.com/i/teamlogos/nfl/500/bal.png',
  'steelers': 'https://a.espncdn.com/i/teamlogos/nfl/500/pit.png',
};

const BOOKS: Record<string, string> = {
  draftkings: 'https://a.espncdn.com/i/teamlogos/leagues/500/nba.png',
  fanduel: 'https://a.espncdn.com/i/teamlogos/leagues/500/nba.png',
};

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase() || '').join('') || '?';
}

export function TeamMark({name, sport}: {name: string; sport?: string}) {
  const key = name.toLowerCase();
  const hit = Object.keys(TEAMS).find((k) => key.includes(k));
  const [ok, setOk] = useState(Boolean(hit));
  if (hit && ok) {
    return <img className="arb-mark" alt="" src={TEAMS[hit]} onError={() => setOk(false)} />;
  }
  return <span className="arb-mark fallback" data-sport={sport}>{initials(name)}</span>;
}

export function BookMark({name}: {name: string}) {
  return <span className="book-mark">{name}</span>;
}
