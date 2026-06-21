export type Signal = 'STEAM' | 'RESISTANCE' | 'TRAP' | 'REVERSAL' | 'COIN_FLIP' | 'CHAOS' | 'DAC' | 'MARKET_ANOMALY';

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
  marketNote?: string;
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

export type LineMovementPoint = {
  time: string;
  bufMoneyline: number;
  miaMoneyline: number;
  milestone?: 'Baseline' | 'Review' | 'Confirm';
  signal?: Signal;
};

export const lineMovement: LineMovementPoint[] = [
  { time: '1:00 AM', bufMoneyline: -115, miaMoneyline: -105, milestone: 'Baseline' },
  { time: '1:15 AM', bufMoneyline: -116, miaMoneyline: -104 },
  { time: '1:30 AM', bufMoneyline: -118, miaMoneyline: -102 },
  { time: '1:45 AM', bufMoneyline: -119, miaMoneyline: -101 },
  { time: '2:00 AM', bufMoneyline: -121, miaMoneyline: 102 },
  { time: '2:15 AM', bufMoneyline: -122, miaMoneyline: 103 },
  { time: '2:30 AM', bufMoneyline: -124, miaMoneyline: 104 },
  { time: '2:45 AM', bufMoneyline: -123, miaMoneyline: 103, signal: 'RESISTANCE' },
  { time: '3:00 AM', bufMoneyline: -125, miaMoneyline: 105 },
  { time: '3:15 AM', bufMoneyline: -126, miaMoneyline: 106 },
  { time: '3:30 AM', bufMoneyline: -128, miaMoneyline: 108 },
  { time: '3:45 AM', bufMoneyline: -129, miaMoneyline: 109 },
  { time: '4:00 AM', bufMoneyline: -130, miaMoneyline: 110 },
  { time: '4:15 AM', bufMoneyline: -131, miaMoneyline: 111 },
  { time: '4:30 AM', bufMoneyline: -130, miaMoneyline: 110, signal: 'RESISTANCE' },
  { time: '4:45 AM', bufMoneyline: -132, miaMoneyline: 112 },
  { time: '5:00 AM', bufMoneyline: -133, miaMoneyline: 113 },
  { time: '5:15 AM', bufMoneyline: -134, miaMoneyline: 114 },
  { time: '5:30 AM', bufMoneyline: -135, miaMoneyline: 115 },
  { time: '5:45 AM', bufMoneyline: -136, miaMoneyline: 116 },
  { time: '6:00 AM', bufMoneyline: -137, miaMoneyline: 117 },
  { time: '6:15 AM', bufMoneyline: -138, miaMoneyline: 118 },
  { time: '6:30 AM', bufMoneyline: -137, miaMoneyline: 117, signal: 'RESISTANCE' },
  { time: '6:45 AM', bufMoneyline: -139, miaMoneyline: 119 },
  { time: '7:00 AM', bufMoneyline: -140, miaMoneyline: 120 },
  { time: '7:15 AM', bufMoneyline: -141, miaMoneyline: 121 },
  { time: '7:30 AM', bufMoneyline: -142, miaMoneyline: 122 },
  { time: '7:45 AM', bufMoneyline: -143, miaMoneyline: 123 },
  { time: '8:00 AM', bufMoneyline: -144, miaMoneyline: 124 },
  { time: '8:15 AM', bufMoneyline: -143, miaMoneyline: 123, signal: 'RESISTANCE' },
  { time: '8:30 AM', bufMoneyline: -144, miaMoneyline: 124 },
  { time: '8:45 AM', bufMoneyline: -145, miaMoneyline: 125 },
  { time: '9:00 AM', bufMoneyline: -146, miaMoneyline: 126, milestone: 'Review', signal: 'STEAM' },
  { time: '9:15 AM', bufMoneyline: -145, miaMoneyline: 125 },
  { time: '9:30 AM', bufMoneyline: -146, miaMoneyline: 126 },
  { time: '9:45 AM', bufMoneyline: -147, miaMoneyline: 127 },
  { time: '10:00 AM', bufMoneyline: -148, miaMoneyline: 128 },
  { time: '10:15 AM', bufMoneyline: -147, miaMoneyline: 127, signal: 'RESISTANCE' },
  { time: '10:30 AM', bufMoneyline: -149, miaMoneyline: 129 },
  { time: '10:45 AM', bufMoneyline: -150, miaMoneyline: 130 },
  { time: '11:00 AM', bufMoneyline: -149, miaMoneyline: 129 },
  { time: '11:15 AM', bufMoneyline: -150, miaMoneyline: 130 },
  { time: '11:30 AM', bufMoneyline: -151, miaMoneyline: 131 },
  { time: '11:45 AM', bufMoneyline: -152, miaMoneyline: 132 },
  { time: '12:00 PM', bufMoneyline: -151, miaMoneyline: 131, signal: 'RESISTANCE' },
  { time: '12:15 PM', bufMoneyline: -153, miaMoneyline: 133 },
  { time: '12:30 PM', bufMoneyline: -154, miaMoneyline: 134, milestone: 'Confirm', signal: 'DAC' }
];

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
    movement: 'Favorite strengthened across multiple sportsbook markets with verified movement tracked',
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
    movement: 'Underdog hold; favorite still shows anchor-level market support',
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
  },
  {
    id: 'anomaly-001',
    league: 'NCAAM',
    start: '9:10 PM',
    matchup: 'Example State @ Coastal Tech',
    favorite: 'Coastal Tech',
    underdog: 'Example State',
    favoriteMl: -108,
    underdogMl: -104,
    total: 141.5,
    movement: 'Abnormal cross-book divergence and sudden late reversal detected',
    confidence: 'Fragile',
    risk: 'HIGH',
    signals: ['MARKET_ANOMALY', 'CHAOS', 'REVERSAL'],
    dataStatus: 'Collected',
    marketNote: 'Market Anomaly flags unusual price behavior only. It is not a claim about teams, players, officials, or intent.'
  },
  {
    id: 'darts-001',
    league: 'DARTS',
    start: '3:40 PM',
    matchup: 'Price vs Smith',
    favorite: 'Price',
    underdog: 'Smith',
    favoriteMl: -128,
    underdogMl: 110,
    total: 8.5,
    movement: 'Favorite hold with short-format volatility; checkout-pressure market still compressed',
    confidence: 'Moderate',
    risk: 'MED',
    signals: ['STEAM', 'COIN_FLIP'],
    dataStatus: 'Collected'
  },
  {
    id: 'lacrosse-001',
    league: 'LACROSSE',
    start: '6:05 PM',
    matchup: 'Maryland @ Duke',
    favorite: 'Duke',
    underdog: 'Maryland',
    favoriteMl: -145,
    underdogMl: 124,
    total: 24.5,
    movement: 'Total firmed while favorite ML held; goalie/news sensitivity remains medium',
    confidence: 'Moderate',
    risk: 'MED',
    signals: ['STEAM', 'RESISTANCE'],
    dataStatus: 'Collected'
  },
  {
    id: 'table-tennis-001',
    league: 'TABLE TENNIS',
    start: '11:15 AM',
    matchup: 'Chen vs Novak',
    favorite: 'Chen',
    underdog: 'Novak',
    favoriteMl: -162,
    underdogMl: 138,
    total: 74.5,
    movement: 'Fast-cycle market; favorite strengthened but short-match variance remains high',
    confidence: 'Fragile',
    risk: 'HIGH',
    signals: ['STEAM', 'CHAOS'],
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
  { label: 'Latest Market Check', value: 'Confirmed', detail: 'Multi-book sportsbook comparison' },
  { label: 'Line Graph', value: 'Market movement', detail: 'Verified changes plus signal markers' },
  { label: 'Market Anomaly', value: 'Abnormal behavior', detail: 'Flags unusual markets without accusation' },
  { label: 'Build Rule', value: '2 Solid + 1 CF', detail: 'Natural structure, no forced risk' }
];
