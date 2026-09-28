export type RadarItem = {
  id: string;
  label: string;
  tone: 'live' | 'held' | 'wait';
};

export function radarFromArb(row: any): RadarItem[] {
  const validation = row?.validation || {};
  const settlement = validation.settlement_states || {};
  const items: RadarItem[] = [];

  if (validation.outcome_coverage === 'complete') {
    items.push({ id: 'quoted', label: 'Both sides quoted', tone: 'live' });
  }
  if (settlement.strict_arbitrage) {
    items.push({ id: 'two-way', label: 'Two-way payout path', tone: 'live' });
  }
  if (validation.executable) {
    items.push({ id: 'executable', label: 'Quotes executable', tone: 'live' });
  }
  if (settlement.includes_push) {
    items.push({ id: 'push', label: 'Push state on radar', tone: 'held' });
  }
  if (validation.rules_compatible === false || validation.qualification_reason === 'SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE') {
    items.push({ id: 'rules', label: 'Settlement rules unverified', tone: 'held' });
  }
  if (validation.settlement_reason === 'EVENT_OR_MARKET_SCOPE_UNREVIEWED') {
    items.push({ id: 'scope', label: 'Market scope unreviewed', tone: 'held' });
  }
  if (Array.isArray(validation.missing_books) && validation.missing_books.length) {
    items.push({ id: 'coverage', label: 'Book coverage gap', tone: 'held' });
  }
  if (!items.length) {
    items.push({ id: 'wait', label: 'Waiting on signal radar', tone: 'wait' });
  }
  return items;
}

export function radarFromGame(game: any): RadarItem[] {
  const items: RadarItem[] = [];
  const signals = Array.isArray(game?.signals) ? game.signals.map((value: string) => String(value).toUpperCase()) : [];
  if (signals.includes('ACTIVE_SLATE') || game?.status_label === 'Live') {
    items.push({ id: 'slate', label: 'Active slate', tone: 'live' });
  }
  if (signals.includes('MARKET_BOARD') || game?.bookCount) {
    items.push({ id: 'board', label: 'Live board quotes', tone: 'live' });
  }
  if (game?.favorite && (game?.favoriteMl ?? game?.favorite_ml)) {
    items.push({ id: 'favorite', label: 'Market favorite posted', tone: 'live' });
  }
  if (game?.predicted_winner || game?.predicted_side) {
    items.push({ id: 'lean', label: 'InQsi lean posted', tone: 'live' });
  }
  if (game?.start || game?.commence_time) {
    items.push({ id: 'kickoff', label: 'Kickoff posted', tone: 'live' });
  }
  if (signals.includes('STEAM')) items.push({ id: 'steam', label: 'Steam on radar', tone: 'live' });
  if (signals.includes('RESISTANCE')) items.push({ id: 'resistance', label: 'Resistance on radar', tone: 'held' });
  if (signals.includes('TRAP')) items.push({ id: 'trap', label: 'Trap on radar', tone: 'held' });
  if (signals.includes('REVERSAL')) items.push({ id: 'reversal', label: 'Reversal on radar', tone: 'held' });
  if (signals.includes('COIN_FLIP')) items.push({ id: 'coinflip', label: 'Coin-flip on radar', tone: 'held' });
  if (signals.includes('MARKET_ANOMALY')) items.push({ id: 'anomaly', label: 'Market anomaly on radar', tone: 'held' });
  if (!items.length) items.push({ id: 'wait', label: 'Waiting on signal radar', tone: 'wait' });
  return items;
}
