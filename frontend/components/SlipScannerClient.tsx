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

function numberOrUndef(value: unknown) {
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function formatAmerican(value?: number) {
  if (!Number.isFinite(value)) return 'Waiting';
  return Number(value) > 0 ? `+${value}` : String(value);
}

function normSport(value?: string) {
  const raw = String(value || '').toLowerCase();
  if (raw.includes('baseball') || raw === 'mlb') return 'MLB';
  if (raw.includes('ncaaf') || raw === 'cfb') return 'CFB';
  if (raw.includes('nfl') || raw.includes('americanfootball_nfl')) return 'NFL';
  if (raw.includes('wnba')) return 'WNBA';
  if (raw.includes('ncaab') || raw === 'ncaam') return 'NCAAM';
  if (raw.includes('nba') || raw.includes('basketball_nba')) return 'NBA';
  if (raw.includes('nhl') || raw.includes('hockey')) return 'NHL';
  if (raw.includes('soccer') || raw.includes('epl') || raw.includes('mls')) return 'Soccer';
  if (raw.includes('tennis')) return 'Tennis';
  return raw ? raw.toUpperCase() : '';
}

function parseBoard(payload: any): BoardGame[] {
  const boards = Array.isArray(payload?.boards) ? payload.boards : [];
  const games: BoardGame[] = [];
  for (const board of boards) {
    const sport = normSport(board?.sport || board?.providerSportKey);
    for (const game of board?.games || []) {
      const away = String(game.awayTeam || game.away_team || '').trim();
      const home = String(game.homeTeam || game.home_team || '').trim();
      if (!away || !home) continue;
      const books: BookQuote[] = [];
      const seen = new Set<string>();
      for (const book of game.books || []) {
        const name = String(book.book || book.bookmaker || '').trim();
        if (!name || seen.has(name)) continue;
        seen.add(name);
        books.push({
          book: name,
          homeMl: numberOrUndef(book?.moneyline?.home),
          awayMl: numberOrUndef(book?.moneyline?.away),
          homeSpread: numberOrUndef(book?.spread?.home_point),
          awaySpread: numberOrUndef(book?.spread?.away_point),
          homeSpreadPrice: numberOrUndef(book?.spread?.home_price),
          awaySpreadPrice: numberOrUndef(book?.spread?.away_price),
          totalPoint: numberOrUndef(book?.total?.over_point ?? book?.total?.point),
          overPrice: numberOrUndef(book?.total?.over_price),
          underPrice: numberOrUndef(book?.total?.under_price),
        });
      }
      games.push({
        key: `${sport}|${away}|${home}`,
        sport,
        away,
        home,
        matchup: `${away} @ ${home}`,
        books,
      });
    }
  }
  const unique = new Map<string, BoardGame>();
  for (const game of games) {
    const prior = unique.get(game.key);
    if (!prior) {
      unique.set(game.key, game);
      continue;
    }
    const seen = new Set(prior.books.map((book) => book.book));
    for (const book of game.books) {
      if (!seen.has(book.book)) prior.books.push(book);
    }
  }
  return Array.from(unique.values());
}

function oddsFor(book: BookQuote, game: BoardGame, draft: LegDraft) {
  if (draft.market === 'total') return draft.selection === 'Under' ? book.underPrice : book.overPrice;
  const away = draft.selection === game.away;
  if (draft.market === 'spread') return away ? book.awaySpreadPrice : book.homeSpreadPrice;
  return away ? book.awayMl : book.homeMl;
}

function bestBookFor(game: BoardGame | undefined, draft: LegDraft) {
  if (!game || !draft.selection) return '';
  return game.books.reduce<{ book: string; odds: number } | null>((best, book) => {
    const odds = oddsFor(book, game, draft);
    if (!Number.isFinite(odds)) return best;
    return !best || Number(odds) > best.odds ? { book: book.book, odds: Number(odds) } : best;
  }, null)?.book || '';
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

  const sports = useMemo(() => Array.from(new Set(games.map((game) => game.sport))), [games]);

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
        const game = games.find((item) => item.key === patch.gameKey);
        next.selection = '';
        next.book = '';
        next.sport = game?.sport || next.sport;
      }
      if (patch.market === 'total' && !['Over', 'Under'].includes(next.selection)) next.selection = '';
      const selectedGame = games.find((item) => item.key === next.gameKey);
      if ((patch.selection || patch.market) && selectedGame && next.selection) {
        next.book = bestBookFor(selectedGame, next) || next.book;
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

  const completed = legs.filter((leg) => leg.gameKey && leg.selection && leg.book).length;

  return (
    <section className="inqsi-panel slip-builder">
      <div className="slip-builder-head">
        <div><p className="eyebrow blue">Build My Slip</p><h2>{completed}/3 legs selected</h2></div>
        <span className="data-status">{mode === 'live' ? 'Live board' : mode === 'loading' ? 'Connecting' : 'Board syncing'}</span>
      </div>
      {!games.length && <div className="slip-sync"><b>Live board is syncing</b><span>Selections unlock as verified sportsbook markets arrive. InQsi never fills a slip with invented odds.</span></div>}
      <form onSubmit={onSubmit} className="slip-builder-form">
        {legs.map((leg, index) => {
          const sportGames = leg.sport ? games.filter((game) => game.sport === leg.sport) : [];
          const game = games.find((item) => item.key === leg.gameKey);
          const bookNames = game?.books.map((book) => book.book) || [];
          const selections = !game ? [] : leg.market === 'total' ? ['Over', 'Under'] : [game.away, game.home];
          const quote = quoteFor(game, leg);
          const complete = Boolean(game && leg.selection && leg.book && quote.odds !== 'Waiting');
          return (
            <article className={`slip-leg-card ${complete ? 'complete' : ''}`} key={index}>
              <div className="slip-leg-head">
                <div><small>LEG {index + 1}</small><strong>{complete ? quote.selection : game?.matchup || 'Choose a matchup'}</strong>{complete && <span>{game?.matchup}</span>}</div>
                <b>{complete ? quote.odds : '—'}</b>
              </div>
              <div className="slip-fields">
                <label><span>Sport</span><select value={leg.sport} onChange={(e) => updateLeg(index,{sport:e.target.value})}><option value="">{sports.length?'Sport':'Waiting'}</option>{sports.map(s=><option key={s}>{s}</option>)}</select></label>
                <label className="wide"><span>Game</span><select value={leg.gameKey} onChange={(e)=>updateLeg(index,{gameKey:e.target.value})} disabled={!leg.sport}><option value="">{!leg.sport?'Choose sport':sportGames.length?'Choose game':'Waiting'}</option>{sportGames.map(g=><option key={g.key} value={g.key}>{g.matchup}</option>)}</select></label>
                <label><span>Market</span><select value={leg.market} onChange={(e)=>updateLeg(index,{market:e.target.value})}>{markets.map(m=><option key={m.value} value={m.value}>{m.label}</option>)}</select></label>
                <label className="wide"><span>Pick</span><select value={leg.selection} onChange={(e)=>updateLeg(index,{selection:e.target.value})} disabled={!game}><option value="">{game?'Choose side':'Choose game'}</option>{selections.map(s=><option key={s}>{s}</option>)}</select></label>
              </div>
              {game && leg.selection && <div className="best-price-row"><div><small>BEST LIVE PRICE</small><strong>{quote.selection} · {quote.odds}</strong><span>{leg.book || 'Waiting for quoted book'}</span></div>{bookNames.length>1&&<label><span>Change book</span><select value={leg.book} onChange={(e)=>updateLeg(index,{book:e.target.value})}>{bookNames.map(b=><option key={b}>{b}</option>)}</select></label>}</div>}
            </article>
          );
        })}
        <div className="slip-action-bar"><div><small>3-LEG SLIP</small><strong>{completed}/3 ready</strong></div><button className="inqsi-primary" type="submit" disabled={state.loading||!games.length||completed===0}>{state.loading?'Analyzing…':'Analyze slip'}</button></div>
      </form>
      {state.error && <p className="movement slip-message">{state.error}</p>}
    </section>
  );
}
