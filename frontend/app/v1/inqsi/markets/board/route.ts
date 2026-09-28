import { NextRequest, NextResponse } from 'next/server';
import { flagsForRequest } from '@/lib/edgeFlags';

export const dynamic = 'force-dynamic';

function apiBase() {
  const value = process.env.INQSI_API_URL || process.env.API_URL || process.env.NEXT_PUBLIC_INQSI_API_URL || process.env.NEXT_PUBLIC_INQSI_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || '';
  const cleaned = value.trim().replace(/\/$/, '');
  if (!cleaned) return '';
  if (/inqsi\.app/i.test(cleaned) || /vercel\.app/i.test(cleaned)) return '';
  return cleaned;
}

function emptyLive(error: string) {
  return {
    ok: false,
    mode: 'unavailable',
    source: 'LIVE_REQUIRED',
    warning: 'NO SAMPLE BOARD. Waiting on live market-board API.',
    error,
    board: 'empty',
    sportsChecked: [],
    sportsWithGames: 0,
    memberSlipsIncluded: false,
    boards: [],
  };
}

function isSample(payload: any) {
  const source = String(payload?.source || payload?.mode || '').toUpperCase();
  if (source.includes('SAMPLE') || payload?.mode === 'sample') return true;
  const games = (payload?.boards || []).flatMap((b: any) => b?.games || []);
  return games.some((g: any) => String(g?.books?.[0]?.book || '').toLowerCase() === 'market board');
}

export async function GET(request: NextRequest) {
  const flags = await flagsForRequest(request.headers);
  if (!flags.boardLive) {
    return NextResponse.json(emptyLive('BOARD_FLAG_OFF'), { status: 503, headers: { 'cache-control': 'no-store' } });
  }
  const base = apiBase();
  if (!base) {
    return NextResponse.json(emptyLive('BOARD_API_UNCONFIGURED'), { status: 503, headers: { 'cache-control': 'no-store' } });
  }
  try {
    const res = await fetch(`${base}/v1/inqsi/markets/board`, { cache: 'no-store' });
    const body = await res.json().catch(() => null);
    if (!res.ok || !body || isSample(body)) {
      return NextResponse.json(emptyLive(body?.error || 'BOARD_UPSTREAM_NOT_LIVE'), { status: 502, headers: { 'cache-control': 'no-store' } });
    }
    return NextResponse.json({ ...body, mode: body.mode || 'live', source: body.source || 'live' }, { headers: { 'cache-control': 'no-store' } });
  } catch {
    return NextResponse.json(emptyLive('BOARD_SCAN_UNAVAILABLE'), { status: 502, headers: { 'cache-control': 'no-store' } });
  }
}
