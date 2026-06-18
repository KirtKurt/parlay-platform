export type Signal = 'STEAM' | 'RESISTANCE' | 'TRAP' | 'REVERSAL' | 'COIN_FLIP' | 'CHAOS' | 'DAC';

export type Game = {
  id: string;
  league: string;
  start: string;
  matchup: string;
  favorite: string;
  underdog: string;
  favoriteMl: number;
  underdogMl: number;
  total: number;
  movement: string;
  confidence: 'High' | 'Moderate' | 'Fragile';
  risk: 'LOW' | 'MED' | 'HIGH';
  signals: Signal[];
  dataStatus: 'Collected' | 'Pending' | 'Failed';
};

export type Ranking = {
  rank: number;
  topZone: boolean;
  legs: string[];
  american: string;
  implied: string;
  structure: 'CLEAN 3-SOLID' | 'MIXED 2-SOLID-1-CF';
  note: string;
  risk: 'LOW' | 'MED' | 'HIGH';
};

export const games: Game[] = [
  {
    id: 'nfl-001',
    league: 'NFL',
    start: '8:20 PM',
    matchup: 'Buffalo Bills @ Miami Dolphins',
    favorite: 'Buffalo Bills',
    underdog: 'Miami Dolphins',
    favoriteMl: -142,
    underdogMl: 120,
    total: 48.5,
    movement: 'Favorite strengthened from T1 → T3 across 2 books',
    confidence: 'High',
    risk: 'LOW',
    signals: ['STEAM', 'DAC'],
    dataStatus: 'Collected'
  },
  {
    id: 'nfl-002',
    league: 'NFL',
    start: '4:25 PM',
    matchup: 'Dallas Cowboys @ Philadelphia Eagles',
    favorite: 'Philadelphia Eagles',
    underdog: 'Dallas Cowboys',
    favoriteMl: -118,
    underdogMl: 104,
    total: 45.5,
    movement: 'Compressed market with late resistance',
    confidence: 'Moderate',
    risk: 'MED',
    signals: ['RESISTANCE', 'COIN_FLIP'],
    dataStatus: 'Collected'
  },
  {
    id: 'cfb-001',
    league: 'CFB',
    start: '7:30 PM',
    matchup: 'Georgia @ Alabama',
    favorite: 'Georgia',
    underdog: 'Alabama',
    favoriteMl: -130,
    underdogMl: 112,
    total: 52.5,
    movement: 'Underdog hold; favorite still T3 anchor',
    confidence: 'Moderate',
    risk: 'MED',
    signals: ['STEAM', 'RESISTANCE'],
    dataStatus: 'Collected'
  },
  {
    id: 'nba-001',
    league: 'NBA',
    start: '10:00 PM',
    matchup: 'Boston Celtics @ Los Angeles Lakers',
    favorite: 'Boston Celtics',
    underdog: 'Los Angeles Lakers',
    favoriteMl: -156,
    underdogMl: 132,
    total: 226.5,
    movement: 'Dual-book confirmation, no major reversal',
    confidence: 'High',
    risk: 'LOW',
    signals: ['STEAM', 'DAC'],
    dataStatus: 'Collected'
  }
];

export const rankings: Ranking[] = [
  {
    rank: 1,
    topZone: true,
    legs: ['Buffalo Bills', 'Philadelphia Eagles', 'Boston Celtics'],
    american: '+584',
    implied: '14.6%',
    structure: 'MIXED 2-SOLID-1-CF',
    note: 'Two confirmed anchors; Eagles leg is the controlled variable.',
    risk: 'MED'
  },
  {
    rank: 2,
    topZone: true,
    legs: ['Buffalo Bills', 'Dallas Cowboys', 'Boston Celtics'],
    american: '+742',
    implied: '11.9%',
    structure: 'MIXED 2-SOLID-1-CF',
    note: 'Coin-flip hedge replaces weakest favorite while preserving both anchors.',
    risk: 'MED'
  },
  {
    rank: 3,
    topZone: true,
    legs: ['Buffalo Bills', 'Philadelphia Eagles', 'Los Angeles Lakers'],
    american: '+910',
    implied: '9.9%',
    structure: 'MIXED 2-SOLID-1-CF',
    note: 'Weak-leg hedge promoted into Top-3 because Lakers market shows compression.',
    risk: 'HIGH'
  },
  {
    rank: 4,
    topZone: false,
    legs: ['Miami Dolphins', 'Philadelphia Eagles', 'Boston Celtics'],
    american: '+1025',
    implied: '8.9%',
    structure: 'MIXED 2-SOLID-1-CF',
    note: 'Underdog exposure increases; outside containment zone.',
    risk: 'HIGH'
  }
];

export const statusCards = [
  { label: 'Last Snapshot', value: 'T3 · 12:31 PM', detail: 'Fanatics canonical + FanDuel comparator' },
  { label: 'Slate Status', value: 'Ready', detail: '4 eligible games in this demo shell' },
  { label: 'Build Rule', value: '2 Solid + 1 CF', detail: 'Natural structure, no forced risk' },
  { label: 'Safety', value: 'Human Gate', detail: 'Cancel-only T5 protection later' }
];
