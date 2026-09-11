import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

async function proxy(request: NextRequest, context: { params: { path?: string[] } }) {
  const base = process.env.INQSI_ENGINEERING_API_URL;
  const token = cookies().get('inqsi_engineering_access')?.value;
  if (!base || !token) return NextResponse.json({ error: 'engineering_console_unavailable' }, { status: 503 });
  const target = `${base.replace(/\/$/, '')}/v1/engineering/${(context.params.path || []).join('/')}`;
  const upstream = await fetch(target, { method: request.method, headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' }, body: ['GET','HEAD'].includes(request.method) ? undefined : await request.text(), cache: 'no-store' });
  return new NextResponse(upstream.body, { status: upstream.status, headers: { 'content-type': upstream.headers.get('content-type') || 'application/json', 'cache-control': 'no-store' } });
}
export const GET = proxy; export const POST = proxy;
