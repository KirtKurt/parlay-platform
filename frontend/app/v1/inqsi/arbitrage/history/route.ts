import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

function apiBase() {
  return (
    process.env.INQSI_ARB_API_URL ||
    process.env.INQSI_API_URL ||
    process.env.API_URL ||
    process.env.NEXT_PUBLIC_INQSI_API_URL ||
    process.env.NEXT_PUBLIC_INQSI_API_BASE_URL ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    ''
  ).trim().replace(/\/$/, '');
}

export async function GET() {
  const base = apiBase();
  if (!base) {
    return NextResponse.json(
      { ok: false, mode: 'unavailable', history: [], error: 'ARB_API_UNCONFIGURED' },
      { status: 503 },
    );
  }
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    const upstream = await fetch(base + '/v1/arb/history?limit=25', {
      cache: 'no-store',
      headers: { accept: 'application/json' },
      signal: controller.signal,
    }).finally(() => clearTimeout(timeout));
    const body = await upstream.json().catch(() => ({}));
    if (!upstream.ok || body?.ok === false) {
      return NextResponse.json(
        { ok: false, mode: 'unavailable', history: [], error: body?.error || 'ARB_HISTORY_UNAVAILABLE' },
        { status: 502 },
      );
    }
    return NextResponse.json({
      ok: true,
      mode: 'live',
      history: Array.isArray(body?.history) ? body.history : [],
    });
  } catch {
    return NextResponse.json(
      { ok: false, mode: 'unavailable', history: [], error: 'ARB_HISTORY_UNAVAILABLE' },
      { status: 502 },
    );
  }
}
