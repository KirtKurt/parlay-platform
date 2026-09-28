export type RadarItem = {
  id: string;
  label: string;
  tone: 'live' | 'held' | 'wait';
};

const SCOPE_REASONS = new Set([
  'EVENT_OR_MARKET_SCOPE_UNREVIEWED',
  'UNREVIEWED_OR_MISSING_RULE',
  'MARKET_SCOPE_UNREVIEWED',
]);

export function radarFromArb(row: any): RadarItem[] {
  const validation = row?.validation || {};
  const settlement = validation.settlement_states || {};
  const reason = String(row?.reason || validation.settlement_reason || '');
  const qualification = String(validation.qualification_reason || '');
  const market = String(row?.market || '').toLowerCase();
  const items: RadarItem[] = [];

  if (row?.start || row?.commence_time) {
    items.push({ id: 'kickoff', label: 'Kickoff on radar', tone: 'live' });
  }
  if (market && market !== 'market') {
    items.push({ id: 'market', label: `${market} market`, tone: 'live' });
  }
  if (validation.outcome_coverage === 'complete') {
    items.push({ id: 'quoted', label: 'Both sides quoted', tone: 'live' });
  }
  if (settlement.strict_arbitrage || row?.mathArb) {
    items.push({ id: 'two-way', label: 'Two-way payout path', tone: 'live' });
  }
  if (row?.mathArb && String(row?.status || '').toUpperCase() !== 'VERIFIED') {
    items.push({ id: 'mismatch', label: 'Cross-book mismatch on radar', tone: 'held' });
  }
  if (validation.executable || row?.executable) {
    items.push({ id: 'executable', label: 'Quotes executable', tone: 'live' });
  }
  if (settlement.includes_push) {
    items.push({ id: 'push', label: 'Push state on radar', tone: 'held' });
  }
  if (String(row?.status || '').toUpperCase() === 'HELD') {
    items.push({ id: 'held', label: 'Held for review', tone: 'held' });
  }
  if (String(row?.status || '').toUpperCase() === 'VERIFIED') {
    items.push({ id: 'verified', label: 'Verified executable', tone: 'live' });
  }
  if (validation.rules_compatible === false || qualification === 'SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE') {
    items.push({ id: 'rules', label: 'House rules under review', tone: 'held' });
  }
  if (SCOPE_REASONS.has(reason) || SCOPE_REASONS.has(String(validation.settlement_reason || ''))) {
    items.push({ id: 'scope', label: 'Settlement rules on radar', tone: 'held' });
  }
  const quoteBooks = Number(row?.books || row?.legs?.length || 0);
  if (quoteBooks > 0) {
    items.push({ id: 'books', label: `${quoteBooks} books on radar`, tone: 'live' });
  }
  if (!items.length) {
    items.push({ id: 'wait', label: 'Waiting on signal radar', tone: 'wait' });
  }
  return items;
}

export function radarFromGame(game: any): RadarItem[] {
  const items: RadarItem[] = [];
  const signals = Array.isArray(game?.signals) ? game.signals.map((value: string) => String(value).toUpperCase()) : [];
  if (game?.start || game?.commence_time) {
    items.push({ id: 'kickoff', label: 'Kickoff on radar', tone: 'live' });
  }
  if (game?.favorite && (game?.favoriteMl ?? game?.favorite_ml)) {
    items.push({ id: 'favorite', label: 'Market favorite on radar', tone: 'live' });
  }
  if (game?.bookCount || signals.includes('MARKET_BOARD') || signals.includes('ACTIVE_SLATE') || game?.status_label === 'Live') {
    items.push({ id: 'board', label: 'Live board on radar', tone: 'live' });
  }
  if (game?.predicted_winner || game?.predicted_side) {
    items.push({ id: 'lean', label: 'InQsi lean on radar', tone: 'live' });
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
