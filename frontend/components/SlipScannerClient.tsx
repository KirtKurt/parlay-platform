'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

type BookQuote = {
  book: string;
  homeMl?: number;
  awayMl?: number;
  homeSpread?: number;
  awaySpread?: number;
  homeSpreadPrice?: number;
  awaySpreadPrice?: number;
  totalPoint?: number;
  overPrice?: number;
  underPrice?: number;
};

type BoardGame = {
  key: string;
  sport: string;
  away: string;
  home: string;
  matchup: string;
  books: BookQuote[];
};

type LegDraft = {
  sport: string;
  gameKey: string;
  market: string;
  selection: string;
  book: string;
};

const emptyLeg: LegDraft = { sport: '', gameKey: '', market: 'moneyline', selection: '', book: '' };
const markets = [
  { value: 'moneyline', label: 'Moneyline' },
  { value: 'spread', label: 'Spread' },
  { value: 'total', label: 'Total' },
];

const sportAlias: Record<string, string> = {
  MLB: 'MLB',
  BASEBALL_MLB: 'MLB',
  AMERICANFOOTBALL_NFL: 'NFL',
  NFL: 'NFL',
  NBA: 'NBA',
  BASKETBALL_NBA: 'NBA',
  WNBA: 'WNBA',
  BASKETBALL_WNBA: 'WNBA',
  NHL: 'NHL',
  ICEHOCKEY_NHL: 'NHL',
  NCAAM: 'NCAAM',
  BASKETBALL_NCAAB: 'NCAAM',
  CFB: 'CFB',
  AMERICANFOOTBALL_NCAAF: 'CFB',
  SOCCER: 'SOCCER',
  SOCCER_EPL: 'SOCCER',
  SOCCER_USA_MLS: 'SOCCER',
  TENNIS: 'TENNIS',
};

function normalizeSport(value: unknown) {
  const raw = String(value || '').toUpperCase().replace(/[^A-Z0-9]+/g, '_');
  return sportAlias[raw] || raw.split('_').pop() || raw;
}

function numberOrUndef(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function formatAmerican(value?: number) {
  if (!Number.isFinite(value)) return 'Waiting';
  return Number(value) > 0 ? `+${value}` : String(value);
}

function mergeBook(current: BookQuote | undefined, incoming: BookQuote): BookQuote {
  if (!current) return incoming;
  return {
    book: current.book,
    homeMl: incoming.homeMl ?? current.homeMl,
    awayMl: incoming.awayMl ?? current.awayMl,
    homeSpread: incoming.homeSpread ?? current.homeSpread,
    awaySpread: incoming.awaySpread ?? current.awaySpread,
    homeSpreadPrice: incoming.homeSpreadPrice ?? current.homeSpreadPrice,
    awaySpreadPrice: incoming.awaySpreadPrice ?? current.awaySpreadPrice,
    totalPoint: incoming.totalPoint ?? current.totalPoint,
    overPrice: incoming.overPrice ?? current.overPrice,
    underPrice: incoming.underPrice ?? current.underPrice,
  };
}

function parseBoard(payload: any): BoardGame[] {
  const boards = Array.isArray(payload?.boards) ? payload.boards : [];
  const merged = new Map<string, BoardGame>();
  for (const board of boards) {
    const sport = normalizeSport(board?.sport || board?.providerSportKey);
    for (const game of board?.games || []) {
      const away = String(game.awayTeam || game.away_team || '');
      const home = String(game.homeTeam || game.home_team || '');
      if (!away || !home) continue;
      const key = `${sport}|${away}|${home}`;
      const incomingBooks: BookQuote[] = (game.books || []).map((book: any) => ({
        book: String(book.book || book.bookmaker || '').trim(),
        homeMl: numberOrUndef(book?.moneyline?.home),
        awayMl: numberOrUndef(book?.moneyline?.away),
        homeSpread: numberOrUndef(book?.spread?.home_point),
        awaySpread: numberOrUndef(book?.spread?.away_point),
        homeSpreadPrice: numberOrUndef(book?.spread?.home_price),
        awaySpreadPrice: numberOrUndef(book?.spread?.away_price),
        totalPoint: numberOrUndef(book?.total?.over_point ?? book?.total?.point),
        overPrice: numberOrUndef(book?.total?.over_price),
        underPrice: numberOrUndef(book?.total?.under_price),
      })).filter((book: BookQuote) => book.book);
      const prior = merged.get(key);
      const booksByName = new Map<string, BookQuote>();
      for (const book of [...(prior?.books || []), ...incomingBooks]) {
        booksByName.set(book.book, mergeBook(booksByName.get(book.book), book));
      }
      merged.set(key, {
        key,
        sport,
        away,
        home,
        matchup: `${away} @ ${home}`,
        books: Array.from(booksByName.values()),
      });
    }
  }
  return Array.from(merged.values());
}

function booksForMarket(game: BoardGame | undefined, market: string) {
  if (!game) return [];
  return game.books.filter((book) => {
    if (market === 'spread') return book.homeSpread != null || book.awaySpread != null;
    if (market === 'total') return book.totalPoint != null || book.overPrice != null || book.underPrice != null;
    return book.homeMl != null || book.awayMl != null;
  }).map((book) => book.book);
}

function quoteFor(game: BoardGame | undefined, draft: LegDraft) {
  if (!game) return { selection: '', odds: '', line: '' };
  const book = game.books.find((item) => item.book === draft.book) || game.books[0];
  if (!book) return { selection: draft.selection, odds: 'Waiting', line: '' };
  if (draft.market === 'total') {
    const over = !draft.selection.toLowerCase().startsWith('under');
    return {
      selection: over ? `Over ${book.totalPoint ?? ''}`.trim() : `Under ${book.totalPoint ?? ''}`.trim(),
      odds: formatAmerican(over ? book.overPrice : book.underPrice),
      line: book.totalPoint != null ? String(book.totalPoint) : '',
    };
  }
  const away = draft.selection === game.away;
  if (draft.market === 'spread') {
    const point = away ? book.awaySpread : book.homeSpread;
    const price = away ? book.awaySpreadPrice : book.homeSpreadPrice;
    return {
      selection: `${draft.selection || (away ? game.away : game.home)} ${point ?? ''}`.trim(),
      odds: formatAmerican(price),
      line: point != null ? String(point) : '',
    };
  }
  return {
    selection: draft.selection || game.home,
    odds: formatAmerican(away ? book.awayMl : book.homeMl),
    line: '',
  };
}

export function SlipScannerClient() {
  const [games, setGames] = useState<BoardGame[]>([]);
  const [mode, setMode] = useState<'loading' | 'live' | 'waiting'>('loading');
  const [legs, setLegs] = useState<LegDraft[]>([{ ...emptyLeg }, { ...emptyLeg }, { ...emptyLeg }]);
  const [state, setState] = useState<{ loading: boolean; error?: string; result?: unknown }>({ loading: false });

  useEffect(() => {
    let active = true;
    fetch('/v1/inqsi/markets/board', { cache: 'no-store' })
      .then((res) => res.json())
      .then((payload) => {
        if (!active) return;
        const found = parseBoard(payload);
        setGames(found);
        setMode(found.length ? 'live' : 'waiting');
      })
      .catch(() => {
        if (active) setMode('waiting');
      });
    return () => { active = false; };
  }, []);

  const sports = useMemo(() => Array.from(new Set(games.map((game) => game.sport))).sort(), [games]);

  function updateLeg(index: number, patch: Partial<LegDraft>) {
    setLegs((current) => current.map((leg, i) => {
      if (i !== index) return leg;
      const next = { ...leg, ...patch };
      if (patch.sport && patch.sport !== leg.sport) {
        next.gameKey = '';
        next.selection = '';
        next.book = '';
      }
      if (patch.gameKey && patch.gameKey !== leg.gameKey) {
        next.selection = '';
        next.book = '';
      }
      if (patch.market && patch.market !== leg.market) {
        next.book = '';
        if (patch.market === 'total') next.selection = 'Over';
        else next.selection = '';
      }
      return next;
    }));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = legs.map((leg) => {
      const game = games.find((item) => item.key === leg.gameKey);
      const quote = quoteFor(game, leg);
      return {
        sport: leg.sport,
        marketType: leg.market,
        selection: quote.selection,
        book: leg.book,
        oddsAmerican: quote.odds,
        line: quote.line,
        matchup: game?.matchup || '',
      };
    }).filter((leg) => leg.sport && leg.selection && leg.book);
    if (!payload.length) {
      setState({ loading: false, error: 'Choose sport, game, side, and book from the dropdowns before scanning.' });
      return;
    }
    setState({ loading: true });
    try {
      const response = await fetch('/v1/scanner/scan', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ legs: payload, save: false }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || data.message || 'MARKET_DATA_REQUIRED');
      setState({ loading: false, result: data });
    } catch (error) {
      setState({ loading: false, error: error instanceof Error ? error.message : 'Scan waiting on live market match' });
    }
  }

  return (
    <section className="inqsi-panel">
      <div className="inqsi-section-head">
        <h2>Live scanner input</h2>
        <span className="data-status">{mode === 'live' ? 'Live board' : mode === 'loading' ? 'Connecting' : 'Waiting'}</span>
      </div>
      <p className="movement">Pick sport first. Game lists only that sport. Book lists every sportsbook quoting the selected game.</p>
      {!games.length && <p className="movement">Waiting on live board games before dropdowns can fill.</p>}
      <form onSubmit={onSubmit} className="inqsi-game-list" style={{ marginTop: 14 }}>
        {legs.map((leg, index) => {
          const sportGames = leg.sport ? games.filter((game) => game.sport === leg.sport) : [];
          const game = games.find((item) => item.key === leg.gameKey && (!leg.sport || item.sport === leg.sport));
          const bookNames = booksForMarket(game, leg.market);
          const selections = leg.market === 'total'
            ? ['Over', 'Under']
            : game ? [game.away, game.home] : [];
          const quote = quoteFor(game, { ...leg, selection: leg.selection || selections[0] || '' });
          return (
            <article className="inqsi-game-card" key={index}>
              <div className="inqsi-game-row"><b>Leg {index + 1}</b><span className="inqsi-score-chip">{quote.odds}</span></div>
              <div className="inqsi-market-grid">
                <label><span>Sport</span>
                  <select value={leg.sport} onChange={(event) => updateLeg(index, { sport: event.target.value })}>
                    <option value="">{sports.length ? 'Select sport' : 'Waiting'}</option>
                    {sports.map((sport) => <option key={sport} value={sport}>{sport}</option>)}
                  </select>
                </label>
                <label><span>Game</span>
                  <select value={leg.gameKey} onChange={(event) => updateLeg(index, { gameKey: event.target.value })} disabled={!leg.sport}>
                    <option value="">{!leg.sport ? 'Pick a sport first' : sportGames.length ? 'Select game' : 'Waiting on games'}</option>
                    {sportGames.map((item) => <option key={item.key} value={item.key}>{item.matchup}</option>)}
                  </select>
                </label>
                <label><span>Market</span>
                  <select value={leg.market} onChange={(event) => updateLeg(index, { market: event.target.value })}>
                    {markets.map((market) => <option key={market.value} value={market.value}>{market.label}</option>)}
                  </select>
                </label>
                <label><span>Team / side</span>
                  <select value={leg.selection} onChange={(event) => updateLeg(index, { selection: event.target.value })} disabled={!game}>
                    <option value="">{game ? 'Select side' : 'Pick a game first'}</option>
                    {selections.map((item) => <option key={item} value={item}>{item}</option>)}
                  </select>
                </label>
                <label><span>Book</span>
                  <select value={leg.book} onChange={(event) => updateLeg(index, { book: event.target.value })} disabled={!game}>
                    <option value="">{!game ? 'Pick a game first' : bookNames.length ? `Select book (${bookNames.length})` : 'Waiting on books'}</option>
                    {bookNames.map((book) => <option key={book} value={book}>{book}</option>)}
                  </select>
                </label>
                <label><span>Live quote</span>
                  <select value={quote.odds} disabled>
                    <option>{quote.line ? `${quote.selection} · ${quote.odds}` : quote.odds}</option>
                  </select>
                </label>
              </div>
            </article>
          );
        })}
        <button className="inqsi-primary" type="submit" disabled={state.loading || !games.length}>{state.loading ? 'Scanning...' : 'Scan slip'}</button>
      </form>
      {state.error ? <p className="movement" style={{ marginTop: 12 }}>{state.error}</p> : null}
    </section>
  );
}
