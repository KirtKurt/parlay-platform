import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

function apiBase() {
  return (process.env.INQSI_API_URL || process.env.API_URL || process.env.NEXT_PUBLIC_INQSI_API_URL || process.env.NEXT_PUBLIC_INQSI_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || '').trim().replace(/\/$/, '');
}

export async function POST(request: NextRequest) {
  const base = apiBase();
  if (!base) return NextResponse.json({ error: 'SCANNER_API_UNCONFIGURED' }, { status: 503 });
  try {
    const body = await request.json();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const headers: Record<string,string> = { accept: 'application/json', 'content-type': 'application/json' };
    for (const name of ['authorization','x-member-id']) {
      const value = request.headers.get(name);
      if (value) headers[name] = value;
    }
    const upstream = await fetch(base + '/v1/scanner/scan', { method: 'POST', cache: 'no-store', headers, body: JSON.stringify(body), signal: controller.signal }).finally(() => clearTimeout(timeout));
    const payload = await upstream.json().catch(() => ({ error: 'SCANNER_BAD_RESPONSE' }));
    return NextResponse.json(payload, { status: upstream.status, headers: { 'cache-control': 'no-store' } });
  } catch {
    return NextResponse.json({ error: 'SCANNER_UNAVAILABLE' }, { status: 502 });
  }
}
