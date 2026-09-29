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
  // Only compare prices across books when the underlying line is identical.
  // Moneyline is intrinsically comparable; spread/total prices are only
  // comparable when books are quoting the same point.
  const reference = game.books.find((book) => Number.isFinite(oddsFor(book, game, draft)));
  if (!reference) return '';
  const referencePoint = draft.market === 'spread'
    ? (draft.selection === game.away ? reference.awaySpread : reference.homeSpread)
    : draft.market === 'total' ? reference.totalPoint : undefined;
  return game.books.reduce<{ book: string; odds: number } | null>((best, book) => {
    const point = draft.market === 'spread'
      ? (draft.selection === game.away ? book.awaySpread : book.homeSpread)
      : draft.market === 'total' ? book.totalPoint : undefined;
    if (draft.market !== 'moneyline' && point !== referencePoint) return best;
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
        marketSnapshots: game ? game.books.map((book) => {
          const pricedDraft = { ...leg, book: book.book };
          const priced = quoteFor(game, pricedDraft);
          return {
            book: book.book,
            oddsAmerican: priced.odds,
            line: priced.line,
            observedAt: new Date().toISOString(),
            source: 'LIVE_MARKET_BOARD',
          };
        }).filter((snapshot) => snapshot.oddsAmerican && snapshot.oddsAmerican !== 'Waiting') : [],
      };
    }).filter((leg) => leg.sport && leg.selection && leg.book);
    if (payload.length !== 3) {
      setState({ loading: false, error: 'Complete all three selections before scanning.' });
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
    <section className="mock-slip">
      <header className="mock-slip-title">
        <h1>Build My Slip</h1>
        <p>Build up to 3 selections to analyze</p>
      </header>
      <div className="mock-slip-steps" aria-label={`${completed} of 3 picks selected`}>
        {[0,1,2].map((index) => <div className={index < completed ? 'done' : index === completed ? 'active' : ''} key={index}><i>{index+1}</i><span>Pick {index+1}</span></div>)}
      </div>
      {!games.length && <div className="mock-sync"><b>{mode === 'loading' ? 'Connecting to live board' : 'Sportsbook feed unavailable'}</b><span>The scanner is wired and ready. Sports, games and prices will populate automatically when the sportsbook feed resumes.</span></div>}
      <form onSubmit={onSubmit} className="mock-slip-form">
        <div className="mock-sport-strip">
          <button type="button" className={!legs[completed]?.sport ? 'active' : ''}>All</button>
          {sports.map((sport)=><button type="button" key={sport} className={legs[completed]?.sport===sport?'active':''} onClick={()=>completed<3&&updateLeg(completed,{sport})}>{sport}</button>)}
        </div>
        <div className="mock-picks">
          {legs.map((leg,index)=>{
            const game=games.find((item)=>item.key===leg.gameKey);
            const sportGames=leg.sport?games.filter((item)=>item.sport===leg.sport):games;
            const selections=!game?[]:leg.market==='total'?['Over','Under']:[game.away,game.home];
            const quote=quoteFor(game,leg);
            const bookNames=game?.books.map((book)=>book.book)||[];
            const complete=Boolean(game&&leg.selection&&leg.book&&quote.odds!=='Waiting');
            return <article className={`mock-pick-card ${complete?'complete':''}`} key={index}>
              <div className="mock-pick-meta"><span className="mock-sport-badge">{leg.sport||`PICK ${index+1}`}</span><button type="button" onClick={()=>setLegs((rows)=>rows.map((row,i)=>i===index?{...emptyLeg}:row))}>×</button></div>
              <div className="mock-pick-main">
                <div><small>{game?game.matchup:'Choose your matchup'}</small><strong>{complete?quote.selection:'Select a live game'}</strong><span>{leg.market==='total'?'Total Points':leg.market==='spread'?'Spread':'Moneyline'}{complete?` · ${leg.book}`:''}</span></div>
                <b>{complete?quote.odds:'—'}</b>
              </div>
              {!complete&&<div className="mock-pick-controls">
                <select value={leg.sport} onChange={(e)=>updateLeg(index,{sport:e.target.value})}><option value="">Sport</option>{sports.map((s)=><option key={s}>{s}</option>)}</select>
                <select value={leg.gameKey} onChange={(e)=>updateLeg(index,{gameKey:e.target.value})}><option value="">Game</option>{sportGames.map((g)=><option key={g.key} value={g.key}>{g.matchup}</option>)}</select>
                <select value={leg.market} onChange={(e)=>updateLeg(index,{market:e.target.value})}>{markets.map((m)=><option key={m.value} value={m.value}>{m.label}</option>)}</select>
                <select value={leg.selection} onChange={(e)=>updateLeg(index,{selection:e.target.value})} disabled={!game}><option value="">Pick</option>{selections.map((s)=><option key={s}>{s}</option>)}</select>
              </div>}
              {complete&&bookNames.length>1&&<select className="mock-book" value={leg.book} onChange={(e)=>updateLeg(index,{book:e.target.value})}>{bookNames.map((book)=><option key={book}>{book}</option>)}</select>}
            </article>
          })}
        </div>
        <button className="mock-analyze" type="submit" disabled={state.loading||completed!==3}>{state.loading?'Scanning…':'Scan My Picks →'}</button>
      </form>
      {state.error&&<div className="mock-error">{state.error}</div>}
      {state.result ? (() => {
        const scan=(state.result as any)?.scan||state.result as any;
        const reads=Array.isArray(scan?.legReads)?scan.legReads:[];
        return <section className="mock-summary">
          <div><b>{scan?.overallScore!=null?`${scan.overallScore}/100`:'Complete'}</b><span>InQsi Score</span></div>
          <div><b>{scan?.overallRead||'Reviewed'}</b><span>Overall Read</span></div>
          <div><b>{reads.length}/3</b><span>Legs Reviewed</span></div>
        </section>
      })():<section className="mock-summary muted"><div><b>—</b><span>Projected Odds</span></div><div><b>—</b><span>Implied Probability</span></div><div><b>—</b><span>Historical Edge</span></div></section>}
      <button className="mock-clear" type="button" onClick={()=>{setLegs([{...emptyLeg},{...emptyLeg},{...emptyLeg}]);setState({loading:false})}}>⌫ Clear Selections</button>
    </section>
  );
}
