import { NextResponse } from 'next/server';
import { GENERATED_ARB_API_URL } from '@/lib/generatedArbApi';

export const dynamic = 'force-dynamic';

const SPORT_SCANS = [
  { sport: 'mlb', provider: 'baseball_mlb' },
  { sport: 'nfl', provider: 'americanfootball_nfl' },
  { sport: 'cfb', provider: 'americanfootball_ncaaf' },
  { sport: 'nba', provider: 'basketball_nba' },
  { sport: 'wnba', provider: 'basketball_wnba' },
  { sport: 'nhl', provider: 'icehockey_nhl' },
  { sport: 'soccer', provider: 'soccer_epl' },
];

function configuredBoardBase() {
  const value = (process.env.INQSI_API_URL || process.env.API_URL || process.env.NEXT_PUBLIC_INQSI_API_URL || process.env.NEXT_PUBLIC_INQSI_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || '').trim().replace(/\/$/, '');
  const host = value.replace(/^https?:\/\//, '').split('/')[0] || '';
  if (!value || /inqsi\.app$/i.test(host) || /vercel\.app$/i.test(host)) return '';
  return value;
}

function arbBase() {
  return (process.env.INQSI_ARB_API_URL || GENERATED_ARB_API_URL || '').trim().replace(/\/$/, '');
}

function isSample(body: any) {
  const blob = `${body?.mode || ''} ${body?.source || ''} ${body?.board || ''} ${body?.warning || ''}`.toUpperCase();
  if (blob.includes('SAMPLE')) return true;
  const books = (body?.boards || []).flatMap((board: any) => (board.games || []).flatMap((game: any) => game.books || []));
  return books.some((book: any) => /sample|market board/i.test(String(book?.book || '')));
}

function emptyBoard(detail: string) {
  return {
    ok: false,
    mode: 'unavailable',
    source: 'LIVE_BOARD_UNAVAILABLE',
    warning: detail,
    board: 'live_only',
    sportsChecked: SPORT_SCANS.map((item) => item.sport),
    sportsWithGames: 0,
    memberSlipsIncluded: false,
    boards: [],
    games: [],
  };
}

function scanRows(scan: any) {
  return [
    ...(scan?.hits || []),
    ...(scan?.detected_unverified || []),
    ...(scan?.rejected || []),
    ...(scan?.near || []),
    ...(scan?.middles || []),
  ];
}

function splitEvent(event: string) {
  const [away, home] = String(event || '').split(' @ ');
  return { awayTeam: (away || 'Away').trim(), homeTeam: (home || away || 'Home').trim() };
}

function american(leg: any) {
  const value = Number(leg?.american);
  return Number.isFinite(value) ? value : undefined;
}

function toGame(sport: string, rows: any[]) {
  const first = rows[0] || {};
  const { awayTeam, homeTeam } = splitEvent(first.event || '');
  const h2h = rows.filter((row) => String(row.market || '').toLowerCase() === 'h2h').flatMap((row) => row.legs || []);
  const spreads = rows.filter((row) => String(row.market || '').includes('spread')).flatMap((row) => row.legs || []);
  const totals = rows.filter((row) => String(row.market || '').includes('total')).flatMap((row) => row.legs || []);
  const homeMl = h2h.find((leg) => String(leg.outcome || '').includes(homeTeam));
  const awayMl = h2h.find((leg) => String(leg.outcome || '').includes(awayTeam));
  const homeSpread = spreads.find((leg) => String(leg.outcome || '').includes(homeTeam));
  const awaySpread = spreads.find((leg) => String(leg.outcome || '').includes(awayTeam));
  const over = totals.find((leg) => /over/i.test(String(leg.outcome || '')));
  const under = totals.find((leg) => /under/i.test(String(leg.outcome || '')));
  const books = Array.from(new Set(rows.flatMap((row) => (row.legs || []).map((leg: any) => leg.book).filter(Boolean))));
  const pulled = rows.flatMap((row) => (row.legs || []).map((leg: any) => leg.last_update).filter(Boolean)).sort().slice(-1)[0];
  const book = books[0] || 'live-book';
  return {
    gameId: first.market_id || `${sport}-${awayTeam}-${homeTeam}`.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
    sport,
    awayTeam,
    homeTeam,
    commenceTime: first.commence_time,
    latestPulledAt: pulled,
    bookCount: books.length,
    books: [{
      book,
      moneyline: { home: american(homeMl), away: american(awayMl) },
      spread: {
        home_point: homeSpread?.point,
        home_price: american(homeSpread),
        away_point: awaySpread?.point,
        away_price: american(awaySpread),
      },
      total: {
        over_point: over?.point,
        over_price: american(over),
        under_point: under?.point,
        under_price: american(under),
      },
    }],
  };
}

async function scanSport(base: string, provider: string) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const res = await fetch(`${base}/v1/arb/scan?sport=${encodeURIComponent(provider)}&markets=h2h,spreads,totals&bankroll=1000&source=auto`, {
      cache: 'no-store',
      headers: { accept: 'application/json' },
      signal: controller.signal,
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export async function GET() {
  const boardBase = configuredBoardBase();
  if (boardBase) {
    try {
      const res = await fetch(`${boardBase}/v1/inqsi/markets/board`, { cache: 'no-store' });
      if (res.ok) {
        const body = await res.json();
        if (body?.ok !== false && !isSample(body) && Array.isArray(body?.boards) && body.boards.some((board: any) => (board.games || []).length)) {
          return NextResponse.json({ ...body, mode: 'live', source: body.source || boardBase }, { headers: { 'cache-control': 'no-store' } });
        }
      }
    } catch {}
  }

  const base = arbBase();
  if (!base) return NextResponse.json(emptyBoard('ARB_API_UNCONFIGURED'), { status: 503 });

  const scans = await Promise.all(SPORT_SCANS.map(async (item) => ({ item, scan: await scanSport(base, item.provider) })));
  const boards = scans.map(({ item, scan }) => {
    const groups = new Map<string, any[]>();
    for (const row of scanRows(scan)) {
      if (!row?.event) continue;
      const key = `${item.sport}|${row.event}|${row.commence_time || ''}`;
      groups.set(key, [...(groups.get(key) || []), row]);
    }
    const games = Array.from(groups.values()).map((rows) => toGame(item.sport, rows));
    return {
      ok: true,
      sport: item.sport,
      providerSportKey: item.provider,
      source: 'inqsi-arb-live-scan',
      mode: 'live',
      latestPulledAt: games.map((game) => game.latestPulledAt).filter(Boolean).sort().slice(-1)[0] || null,
      gameCount: games.length,
      games,
    };
  }).filter((board) => board.gameCount > 0);

  if (!boards.length) {
    return NextResponse.json(emptyBoard('NO_LIVE_QUOTES'), { status: 200, headers: { 'cache-control': 'no-store' } });
  }

  return NextResponse.json({
    ok: true,
    mode: 'live',
    source: 'inqsi-arb-live-scan',
    board: 'live_odds_scan',
    sportsChecked: SPORT_SCANS.map((item) => item.sport),
    sportsWithGames: boards.length,
    memberSlipsIncluded: false,
    boards,
  }, { headers: { 'cache-control': 'no-store' } });
}
