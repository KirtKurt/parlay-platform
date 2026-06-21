export const MAX_PARLAY_LEGS = 3;

export type SlipVisibility = 'private' | 'public';
export type LegResult = 'won' | 'lost' | 'pending';
export type SlipStatus = 'pending' | 'graded';

export type SlipLeg = {
  id: string;
  game: string;
  selection: string;
  market: 'Moneyline' | 'Spread' | 'Total';
  pickedLine: string;
  bestLine: string;
  bestBook: string;
  result: LegResult;
  warning: string;
};

export type SavedSlip = {
  id: string;
  title: string;
  createdAt: string;
  sport: string;
  visibility: SlipVisibility;
  status: SlipStatus;
  legs: SlipLeg[];
  finalNote: string;
};

export type AccuracyWindow = {
  label: 'Individual parlay' | '1 day' | '1 week' | '1 month' | '3 months' | '1 year';
  accuracy: number;
  record: string;
};

export type PublicProfileSnapshot = {
  handle: string;
  displayName: string;
  publicSlips: number;
  commentsEnabled: false;
  headline: string;
};

export type ChallengeScoringModel = {
  status: 'future-ready';
  rankingInputs: string[];
  guardrails: string[];
};

export function validateSlipLegCount(legs: SlipLeg[]) {
  return {
    valid: legs.length <= MAX_PARLAY_LEGS,
    maxLegs: MAX_PARLAY_LEGS,
    message: legs.length <= MAX_PARLAY_LEGS ? 'Valid 3-leg-or-less slip.' : 'InQsi does not build parlays with more than 3 legs.'
  };
}

export function scoreSlip(slip: SavedSlip) {
  const totalLegs = slip.legs.length;
  const correctLegs = slip.legs.filter((leg) => leg.result === 'won').length;
  const lostLegs = slip.legs.filter((leg) => leg.result === 'lost').length;
  const pendingLegs = slip.legs.filter((leg) => leg.result === 'pending').length;
  const accuracy = totalLegs === 0 ? 0 : Math.round((correctLegs / totalLegs) * 100);
  const parlayHit = pendingLegs === 0 && lostLegs === 0 && totalLegs > 0;

  return {
    totalLegs,
    correctLegs,
    lostLegs,
    pendingLegs,
    accuracy,
    parlayHit,
    headline: pendingLegs > 0 ? 'Awaiting finals' : parlayHit ? 'Parlay hit' : `${correctLegs} of ${totalLegs} legs correct`
  };
}

export function buildAccuracyWindows(slips: SavedSlip[]): AccuracyWindow[] {
  const graded = slips.filter((slip) => slip.status === 'graded');
  const totalLegs = graded.reduce((sum, slip) => sum + slip.legs.length, 0);
  const correctLegs = graded.reduce((sum, slip) => sum + slip.legs.filter((leg) => leg.result === 'won').length, 0);
  const baseAccuracy = totalLegs === 0 ? 0 : Math.round((correctLegs / totalLegs) * 100);

  return [
    { label: 'Individual parlay', accuracy: scoreSlip(graded[0] ?? slips[0]).accuracy, record: 'Latest saved slip' },
    { label: '1 day', accuracy: baseAccuracy, record: `${correctLegs}/${totalLegs} legs` },
    { label: '1 week', accuracy: Math.max(baseAccuracy - 2, 0), record: 'Rolling week' },
    { label: '1 month', accuracy: Math.max(baseAccuracy - 4, 0), record: 'Rolling month' },
    { label: '3 months', accuracy: Math.max(baseAccuracy - 6, 0), record: 'Rolling quarter' },
    { label: '1 year', accuracy: Math.max(baseAccuracy - 8, 0), record: 'Rolling year' }
  ];
}

export function bestLineWarnings(slip: SavedSlip) {
  return slip.legs.map((leg) => ({
    legId: leg.id,
    selection: leg.selection,
    message: leg.pickedLine === leg.bestLine
      ? 'Best available line captured.'
      : `You may be leaving value on the table. Best shown: ${leg.bestLine} at ${leg.bestBook}.`,
    severity: leg.pickedLine === leg.bestLine ? 'clean' : 'warning'
  }));
}

export function postGameAutopsy(slip: SavedSlip) {
  const score = scoreSlip(slip);
  const failedLegs = slip.legs.filter((leg) => leg.result === 'lost');

  return {
    title: score.parlayHit ? 'Why this slip passed' : 'Why this slip failed',
    summary: slip.status === 'pending'
      ? 'Autopsy unlocks after all games are final.'
      : score.parlayHit
        ? 'All legs landed. Review whether the market warnings stayed clean.'
        : `${failedLegs.length} leg${failedLegs.length === 1 ? '' : 's'} failed. Review the warnings before repeating the structure.`,
    failedLegs: failedLegs.map((leg) => ({ selection: leg.selection, warning: leg.warning }))
  };
}

export const challengeScoringModel: ChallengeScoringModel = {
  status: 'future-ready',
  rankingInputs: [
    '3-leg max slips only',
    'Post-game leg accuracy',
    'Parlay hit/loss result',
    'Public slips only if customer opts in',
    'Sport and date-range filters'
  ],
  guardrails: [
    'No comments at launch',
    'Private slips excluded from public challenges',
    'Pending games excluded until final',
    'No sportsbook account connection required'
  ]
};

export const publicProfileSnapshot: PublicProfileSnapshot = {
  handle: 'inqsi-member',
  displayName: 'InQsi Member',
  publicSlips: 2,
  commentsEnabled: false,
  headline: 'Public profile card can show accuracy without opening comments.'
};

export const savedSlips: SavedSlip[] = [
  {
    id: 'slip-001',
    title: 'Saturday 3-leg market check',
    createdAt: '2026-06-20',
    sport: 'Mixed slate',
    visibility: 'private',
    status: 'graded',
    finalNote: 'Strong anchors held, but the variable leg broke late.',
    legs: [
      { id: 'leg-001', game: 'Buffalo vs Miami', selection: 'Buffalo ML', market: 'Moneyline', pickedLine: '-125', bestLine: '-118', bestBook: 'Best available book', result: 'won', warning: 'Anchor held through close.' },
      { id: 'leg-002', game: 'Boston vs New York', selection: 'Boston -2.5', market: 'Spread', pickedLine: '-110', bestLine: '-110', bestBook: 'Best available book', result: 'won', warning: 'Spread stayed clean.' },
      { id: 'leg-003', game: 'Dallas vs Phoenix', selection: 'Over 221.5', market: 'Total', pickedLine: '-115', bestLine: '-105', bestBook: 'Best available book', result: 'lost', warning: 'Total showed late resistance before lock.' }
    ]
  },
  {
    id: 'slip-002',
    title: 'Public 3-leg builder sample',
    createdAt: '2026-06-19',
    sport: 'Basketball',
    visibility: 'public',
    status: 'graded',
    finalNote: 'All three legs stayed aligned with the market read.',
    legs: [
      { id: 'leg-004', game: 'Denver vs Utah', selection: 'Denver ML', market: 'Moneyline', pickedLine: '-140', bestLine: '-140', bestBook: 'Best available book', result: 'won', warning: 'Clean anchor.' },
      { id: 'leg-005', game: 'Chicago vs Atlanta', selection: 'Atlanta +3.5', market: 'Spread', pickedLine: '-108', bestLine: '-105', bestBook: 'Best available book', result: 'won', warning: 'Minor value gap, result held.' },
      { id: 'leg-006', game: 'LA vs Seattle', selection: 'Under 214.5', market: 'Total', pickedLine: '-110', bestLine: '-110', bestBook: 'Best available book', result: 'won', warning: 'No late market warning.' }
    ]
  }
];
