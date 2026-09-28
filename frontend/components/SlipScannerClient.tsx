'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { formatAmericanOdds, formatKickoff } from '@/lib/kickoff';

type BookQuote = {
  book: string;
  moneyline?: { home?: number; away?: number };
  spread?: { home_point?: number; home_price?: number; away_point?: number; away_price?: number };
  total?: { over_point?: number; over_price?: number; under_point?: number; under_price?: number };
};

type BoardGame = {
  id: string;
  sport: string;
  away: string;
  home: string;
  start?: string;
  books: BookQuote[];
};

type LegDraft = {
  sport: string;
  gameId: string;
  market: 'moneyline' | 'spread' | 'total' | '';
  side: string;
  book: string;
};

const emptyLeg = (): LegDraft => ({ sport: '', gameId: '', market: '', side: '', book: '' });

function unique(values: string[]) {
  return Array.from(new Set(values.filter(Boolean)));
}

function parseBoard(payload: any): BoardGame[] {
  const boards = Array.isArray(payload?.boards) ? payload.boards : [];
  const games: BoardGame[] = [];
  for (const board of boards) {
    const sport = String(board?.sport || '').toUpperCase();
    for (const raw of board?.games || []) {
      const away = String(raw.awayTeam || raw.away_team || '');
      const home = String(raw.homeTeam || raw.home_team || '');
      if (!away || !home) continue;
      games.push({
        id: `${sport}-${away}-${home}`.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
        sport,
        away,
        home,
        start: raw.commenceTime || raw.commence_time,
        books: Array.isArray(raw.books) ? raw.books : [],
      });
    }
  }
  const seen = new Map<string, BoardGame>();
  for (const game of games) {
    const prior = seen.get(game.id);
    if (!prior || game.books.length > prior.books.length) seen.set(game.id, game);
  }
  return Array.from(seen.values());
}

function quoteFor(game: BoardGame | undefined, bookName: string) {
  return game?.books.find((book) => book.book === bookName) || game?.books[0];
}

function sidesFor(game: BoardGame | undefined, market: LegDraft['market']) {
  if (!game || !market) return [];
  if (market === 'moneyline' || market === 'spread') return [game.away, game.home];
  if (market === 'total') return ['Over', 'Under'];
  return [];
}

function booksFor(game: BoardGame | undefined, market: LegDraft['market']) {
  if (!game || !market) return [];
  return unique(game.books.filter((book) => {
    if (market === 'moneyline') return book.moneyline?.home != null || book.moneyline?.away != null;
    if (market === 'spread') return book.spread?.home_point != null || book.spread?.away_point != null;
    return book.total?.over_point != null || book.total?.under_point != null;
  }).map((book) => book.book));
}

function pricedLeg(game: BoardGame | undefined, draft: LegDraft) {
  const quote = quoteFor(game, draft.book);
  if (!game || !quote || !draft.market || !draft.side) return { odds: '', line: '', label: '' };
  const homeSide = draft.side === game.home;
  if (draft.market === 'moneyline') {
    const odds = homeSide ? quote.moneyline?.home : quote.moneyline?.away;
    return { odds: formatAmericanOdds(odds), line: '', label: draft.side };
  }
  if (draft.market === 'spread') {
    const point = homeSide ? quote.spread?.home_point : quote.spread?.away_point;
    const odds = homeSide ? quote.spread?.home_price : quote.spread?.away_price;
    return { odds: formatAmericanOdds(odds), line: point == null ? '' : String(point), label: `${draft.side} ${point ?? ''}` };
  }
  const over = draft.side === 'Over';
  const point = over ? quote.total?.over_point : quote.total?.under_point;
  const odds = over ? quote.total?.over_price : quote.total?.under_price;
  return { odds: formatAmericanOdds(odds), line: point == null ? '' : String(point), label: `${draft.side} ${point ?? ''}` };
}

export function SlipScannerClient() {
  const [games, setGames] = useState<BoardGame[]>([]);
  const [mode, setMode] = useState<'loading' | 'live' | 'waiting'>('loading');
  const [legs, setLegs] = useState<LegDraft[]>([emptyLeg(), emptyLeg(), emptyLeg()]);
  const [state, setState] = useState<{ loading: boolean; error?: string; result?: unknown }>({ loading: false });

  useEffect(() => {
    let active = true;
    fetch('/v1/inqsi/markets/board', { cache: 'no-store' })
      .then((response) => response.json())
      .then((payload) => {
        if (!active) return;
        const found = parseBoard(payload);
        setGames(found);
        setMode(found.length ? 'live' : 'waiting');
      })
      .catch(() => {
        if (active) {
          setGames([]);
          setMode('waiting');
        }
      });
    return () => { active = false; };
  }, []);

  const sports = useMemo(() => unique(games.map((game) => game.sport)), [games]);

  function update(index: number, patch: Partial<LegDraft>) {
    setLegs((current) => current.map((leg, i) => {
      if (i !== index) return leg;
      const next = { ...leg, ...patch };
      if (patch.sport && patch.sport !== leg.sport) {
        next.gameId = '';
        next.market = '';
        next.side = '';
        next.book = '';
      }
      if (patch.gameId && patch.gameId !== leg.gameId) {
        next.market = '';
        next.side = '';
        next.book = '';
      }
      if (patch.market && patch.market !== leg.market) {
        next.side = '';
        next.book = '';
      }
      return next;
    }));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = legs.map((draft) => {
      const game = games.find((item) => item.id === draft.gameId);
      const priced = pricedLeg(game, draft);
      return {
        sport: draft.sport,
        marketType: draft.market,
        selection: priced.label || draft.side,
        book: draft.book,
        oddsAmerican: priced.odds,
        line: priced.line,
        event: game ? `${game.away} @ ${game.home}` : '',
        start: game?.start || '',
      };
    }).filter((leg) => leg.sport && leg.marketType && leg.selection);
    if (payload.length < 1) {
      setState({ loading: false, error: 'Pick at least one live game from the dropdowns.' });
      return;
    }
    setState({ loading: true });
    try {
      const response = await fetch('/v1/scanner/scan', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ legs: payload, save: false }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || body.reason || 'MARKET_DATA_REQUIRED');
      setState({ loading: false, result: body });
    } catch (error) {
      setState({ loading: false, error: error instanceof Error ? error.message : 'scan_failed' });
    }
  }

  return (
    <section className="inqsi-panel">
      <div className="inqsi-section-head">
        <h2>Live scanner input</h2>
        <span className="data-status">{mode === 'live' ? 'Live board' : mode === 'loading' ? 'Connecting' : 'Waiting'}</span>
      </div>
      <p className="inqsi-empty">Choose sport, game, market, side, and book from the live board. Team names are not typed.</p>
      <form onSubmit={onSubmit} className="inqsi-game-list" style={{ marginTop: 14 }}>
        {legs.map((draft, index) => {
          const sportGames = games.filter((game) => !draft.sport || game.sport === draft.sport);
          const game = sportGames.find((item) => item.id === draft.gameId);
          const sideOptions = sidesFor(game, draft.market);
          const bookOptions = booksFor(game, draft.market);
          const priced = pricedLeg(game, draft);
          return (
            <article className="inqsi-game-card" key={index}>
              <div className="inqsi-game-row"><b>Leg {index + 1}</b><span className="inqsi-score-chip">{priced.odds || 'Waiting'}</span></div>
              <div className="inqsi-market-grid">
                <label><span>Sport</span>
                  <select value={draft.sport} onChange={(event) => update(index, { sport: event.target.value })}>
                    <option value="">{sports.length ? 'Select sport' : 'Waiting on live sports'}</option>
                    {sports.map((sport) => <option key={sport} value={sport}>{sport}</option>)}
                  </select>
                </label>
                <label><span>Game</span>
                  <select value={draft.gameId} onChange={(event) => update(index, { gameId: event.target.value })}>
                    <option value="">{sportGames.length ? 'Select game' : 'Waiting on live games'}</option>
                    {sportGames.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.away} @ {item.home}{item.start ? ` · ${formatKickoff(item.start)}` : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <label><span>Market</span>
                  <select value={draft.market} onChange={(event) => update(index, { market: event.target.value as LegDraft['market'] })}>
                    <option value="">Select market</option>
                    <option value="moneyline">Moneyline</option>
                    <option value="spread">Spread</option>
                    <option value="total">Total</option>
                  </select>
                </label>
                <label><span>Side</span>
                  <select value={draft.side} onChange={(event) => update(index, { side: event.target.value })}>
                    <option value="">{sideOptions.length ? 'Select side' : 'Waiting on game'}</option>
                    {sideOptions.map((side) => <option key={side} value={side}>{side}</option>)}
                  </select>
                </label>
                <label><span>Book</span>
                  <select value={draft.book} onChange={(event) => update(index, { book: event.target.value })}>
                    <option value="">{bookOptions.length ? 'Select book' : 'Waiting on books'}</option>
                    {bookOptions.map((book) => <option key={book} value={book}>{book}</option>)}
                  </select>
                </label>
                <label><span>Live number</span>
                  <select value={priced.odds ? `${priced.label} ${priced.odds}` : ''} disabled>
                    <option value="">{priced.odds ? `${priced.label} ${priced.odds}` : 'Fills after side and book'}</option>
                  </select>
                </label>
              </div>
            </article>
          );
        })}
        <button className="inqsi-primary" type="submit" disabled={state.loading || mode !== 'live'}>
          {state.loading ? 'Scanning...' : 'Scan slip'}
        </button>
      </form>
      {state.error ? <p className="inqsi-empty" style={{ marginTop: 12 }}>{state.error}</p> : null}
      {state.result ? <pre className="inqsi-empty" style={{ marginTop: 12, overflowX: 'auto' }}>{JSON.stringify(state.result, null, 2)}</pre> : null}
    </section>
  );
}
