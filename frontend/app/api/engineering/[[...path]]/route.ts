import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const ACCESS_COOKIE = '__Host-inqsi_engineering_access';
const SAFE_METHODS = new Set(['GET', 'HEAD']);

function sameOrigin(request: NextRequest) {
  if (SAFE_METHODS.has(request.method)) return true;
  const origin = request.headers.get('origin');
  return Boolean(origin && origin === request.nextUrl.origin);
}

async function proxy(request: NextRequest, context: { params: { path?: string[] } }) {
  if (!sameOrigin(request)) return NextResponse.json({ error: 'cross_origin_request_denied' }, { status: 403 });

  const base = process.env.INQSI_ENGINEERING_API_URL;
  const token = cookies().get(ACCESS_COOKIE)?.value;
  if (!base || !token) return NextResponse.json({ error: 'engineering_console_unavailable' }, { status: 503 });

  const suffix = (context.params.path || []).map(encodeURIComponent).join('/');
  const target = `${base.replace(/\/$/, '')}/v1/engineering/${suffix}`;
  const upstream = await fetch(target, {
    method: request.method,
    headers: {
      authorization: `Bearer ${token}`,
      'content-type': 'application/json'
    },
    body: SAFE_METHODS.has(request.method) ? undefined : await request.text(),
    cache: 'no-store'
  });

  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: {
      'content-type': upstream.headers.get('content-type') || 'application/json',
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff'
    }
  });
}

export const GET = proxy;
export const POST = proxy;
