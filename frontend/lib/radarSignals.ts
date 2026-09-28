export type RadarItem = {
  id: string;
  label: string;
  tone: 'live' | 'held' | 'wait';
};

export function radarFromArb(row: any): RadarItem[] {
  const validation = row?.validation || {};
  const settlement = validation.settlement_states || {};
  const items: RadarItem[] = [];

  items.push({ id: 'quotes', label: 'Live quotes', tone: 'live' });
  if (row?.commence_time || row?.start) {
    items.push({ id: 'kickoff', label: 'Kickoff on radar', tone: 'live' });
  }
  if (row?.market) {
    items.push({ id: 'market', label: `${String(row.market).replace(/_/g, ' ')} market`, tone: 'live' });
  }
  if (row?.status === 'HELD' || row?.arb === false) {
    items.push({ id: 'held', label: 'Held for review', tone: 'held' });
  }
  if (validation.outcome_coverage === 'complete') {
    items.push({ id: 'quoted', label: 'Both sides quoted', tone: 'live' });
  }
  if (row?.math_arb && row?.arb === false) {
    items.push({ id: 'mismatch', label: 'Cross-book mismatch on radar', tone: 'held' });
  }
  if (validation.executable) {
    items.push({ id: 'executable', label: 'Quotes executable', tone: 'live' });
  }
  if (validation.rules_compatible === false || String(validation.settlement_reason || '').includes('UNREVIEWED') || String(validation.settlement_reason || '').includes('MISSING_RULE')) {
    items.push({ id: 'rules', label: 'Settlement rules on radar', tone: 'held' });
  }
  const books = Number(row?.n_books || row?.legs?.length || 0);
  if (books > 0) items.push({ id: 'books', label: `${books} books on radar`, tone: 'live' });
  if (!items.length) items.push({ id: 'wait', label: 'Waiting on live signals', tone: 'wait' });
  return items;
}

export function radarFromGame(game: any): RadarItem[] {
  const items: RadarItem[] = [];
  if (game?.start || game?.commence_time) {
    items.push({ id: 'kickoff', label: 'Kickoff on radar', tone: 'live' });
  }
  if (game?.favorite && (game?.favoriteMl ?? game?.favorite_ml)) {
    items.push({ id: 'favorite', label: 'Market favorite on radar', tone: 'live' });
  }
  if (game?.bookCount) {
    items.push({ id: 'books', label: `${game.bookCount} books on radar`, tone: 'live' });
  }
  if (game?.predicted_winner || game?.predicted_side) {
    items.push({ id: 'lean', label: 'InQsi lean on radar', tone: 'live' });
  }
  const signals = Array.isArray(game?.signals) ? game.signals.map((value: string) => String(value).toUpperCase()) : [];
  if (signals.includes('STEAM')) items.push({ id: 'steam', label: 'Steam on radar', tone: 'live' });
  if (signals.includes('RESISTANCE')) items.push({ id: 'resistance', label: 'Resistance on radar', tone: 'held' });
  if (signals.includes('TRAP')) items.push({ id: 'trap', label: 'Trap on radar', tone: 'held' });
  if (!items.length) items.push({ id: 'wait', label: 'Waiting on live signals', tone: 'wait' });
  return items;
}
