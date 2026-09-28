import { NextRequest, NextResponse } from 'next/server';
import { GENERATED_ARB_API_URL } from '@/lib/generatedArbApi';

export const dynamic = 'force-dynamic';

function apiBase() {
  return (
    process.env.INQSI_ARB_API_URL ||
    process.env.INQSI_API_URL ||
    process.env.API_URL ||
    process.env.NEXT_PUBLIC_INQSI_API_URL ||
    process.env.NEXT_PUBLIC_INQSI_API_BASE_URL ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    GENERATED_ARB_API_URL ||
    ''
  ).trim().replace(/\/$/, '');
}

export async function GET(request: NextRequest) {
  const base = apiBase();
  if (!base) return NextResponse.json({ok:false,mode:'unavailable',error:'ARB_API_UNCONFIGURED'}, {status:503});
  const incoming = request.nextUrl.searchParams;
  const params = new URLSearchParams({
    sport: incoming.get('sport') || 'all',
    markets: incoming.get('markets') || 'h2h,spreads,totals',
    bankroll: incoming.get('bankroll') || '1000',
    source: 'auto',
  });
  const books = incoming.get('books');
  if (books) params.set('books', books);
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    const upstream = await fetch(base + '/v1/arb/scan?' + params.toString(), {
      cache:'no-store', headers:{accept:'application/json'}, signal:controller.signal,
    }).finally(()=>clearTimeout(timeout));
    const body = await upstream.json().catch(()=>({}));
    if (!upstream.ok || body?.ok === false) {
      return NextResponse.json({ok:false,mode:'unavailable',error:body?.error||'ARB_SCAN_UNAVAILABLE'}, {status:502});
    }
    return NextResponse.json({
      ok:true, mode:'live', source:body?.source || body?.status?.source || 'live',
      fetchedAt:new Date().toISOString(), nArbs:Number(body?.n_arbs||0),
      hits:Array.isArray(body?.hits)?body.hits:[],
      held:Array.isArray(body?.detected_unverified)?body.detected_unverified:[],
      status:body?.status||null,
    }, {headers:{'cache-control':'no-store'}});
  } catch {
    return NextResponse.json({ok:false,mode:'unavailable',error:'ARB_SCAN_UNAVAILABLE'}, {status:502});
  }
}
