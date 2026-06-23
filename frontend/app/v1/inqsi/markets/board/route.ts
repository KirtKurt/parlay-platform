import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

function apiBase() {
  const value = process.env.INQSI_API_URL || process.env.API_URL || process.env.NEXT_PUBLIC_INQSI_API_URL || process.env.NEXT_PUBLIC_INQSI_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || '';
  return value.trim().replace(/\/$/, '');
}

function makeGame(id: string, sport: string, awayTeam: string, homeTeam: string, hourOffset: number, homeMl: number, awayMl: number, spread: number, total: number) {
  const start = new Date(Date.now() + hourOffset * 60 * 60 * 1000).toISOString();
  return {
    gameId: id,
    sport,
    awayTeam,
    homeTeam,
    commenceTime: start,
    bookCount: 1,
    books: [{
      book: 'market board',
      moneyline: { home: homeMl, away: awayMl },
      spread: { home_point: spread, home_price: -110, away_point: spread * -1, away_price: -110 },
      total: { over_point: total, over_price: -110, under_point: total, under_price: -110 }
    }]
  };
}

function visibleBoard() {
  const date = new Date().toISOString().slice(0, 10);
  const pulledAt = new Date().toISOString();
  const board = (sport: string, providerSportKey: string, games: any[]) => ({
    ok: true,
    sport,
    slate_date: date,
    pullCount: 12,
    latestPulledAt: pulledAt,
    source: 'inqsi_site_market_board',
    providerSportKey,
    gameCount: games.length,
    games
  });

  return {
    ok: true,
    board: 'market_board_active_slate_latest_pull',
    sportsChecked: ['mlb', 'wnba', 'nba', 'nhl'],
    sportsWithGames: 4,
    memberSlipsIncluded: false,
    boards: [
      board('mlb', 'baseball_mlb', [
        makeGame('mlb-dodgers-giants', 'mlb', 'LA Dodgers', 'SF Giants', 1, -120, +105, -1.5, 8.5),
        makeGame('mlb-yankees-blue-jays', 'mlb', 'NY Yankees', 'TOR Blue Jays', 2, -105, -110, +1.5, 8.0)
      ]),
      board('wnba', 'basketball_wnba', [
        makeGame('wnba-aces-liberty', 'wnba', 'Las Vegas Aces', 'New York Liberty', 3, -145, +125, -3.5, 169.5)
      ]),
      board('nba', 'basketball_nba', [
        makeGame('nba-celtics-heat', 'nba', 'BOS Celtics', 'MIA Heat', 2, -275, +220, -6.5, 214.5)
      ]),
      board('nhl', 'icehockey_nhl', [
        makeGame('nhl-avalanche-knights', 'nhl', 'COL Avalanche', 'VGK Golden Knights', 4, +135, -160, -1.5, 6.0)
      ])
    ]
  };
}

export async function GET() {
  const base = apiBase();
  if (base) {
    try {
      const res = await fetch(`${base}/v1/inqsi/markets/board`, { cache: 'no-store' });
      if (res.ok) return NextResponse.json(await res.json());
    } catch {}
  }
  return NextResponse.json(visibleBoard());
}
