import { NextRequest, NextResponse } from 'next/server';
import { GENERATED_ARB_API_URL } from '@/lib/generatedArbApi';

export const dynamic = 'force-dynamic';

function apiBase() {
  return (
    process.env.INQSI_ARB_API_URL ||
    GENERATED_ARB_API_URL ||
    process.env.INQSI_API_URL ||
    process.env.API_URL ||
    process.env.NEXT_PUBLIC_INQSI_API_URL ||
    process.env.NEXT_PUBLIC_INQSI_API_BASE_URL ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    ''
  ).trim().replace(/\/$/, '');
}

export async function GET(request: NextRequest) {
  const base = apiBase();
  if (!base) return NextResponse.json({ok:false,mode:'unavailable',error:'ARB_API_UNCONFIGURED'}, {status:503});
  const incoming = request.nextUrl.searchParams;
  const sport = String(incoming.get('sport') || '').trim();
  const params = new URLSearchParams({
    markets: incoming.get('markets') || 'h2h,spreads,totals',
    bankroll: incoming.get('bankroll') || '1000',
    // The production collector already maintains the multi-sport snapshot store.
    // Read that snapshot instead of forcing the request path to fall through to
    // a synchronous provider-wide live scan when one sport is not selected.
    source: 'store',
  });
  params.set('sport', sport && sport.toLowerCase() !== 'all' ? sport : 'all');
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
      status:body?.status||null, history:[{created_at_ms:Date.now(),payload:{sport:sport || 'store',hits:Array.isArray(body?.hits)?body.hits:[],detected_unverified:Array.isArray(body?.detected_unverified)?body.detected_unverified:[]}}],
    }, {headers:{'cache-control':'no-store'}});
  } catch {
    return NextResponse.json({ok:false,mode:'unavailable',error:'ARB_SCAN_UNAVAILABLE'}, {status:502});
  }
}
