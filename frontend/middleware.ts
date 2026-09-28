import { NextRequest, NextResponse } from 'next/server';
import { flagHeaders, getEdgeFlags } from '@/lib/edgeFlags';

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js|woff2?)$).*)'],
};

export async function middleware(request: NextRequest) {
  const flags = await getEdgeFlags();
  const requestHeaders = new Headers(request.headers);
  for (const [key, value] of Object.entries(flagHeaders(flags))) {
    requestHeaders.set(key, value);
  }

  const incomingZone = request.headers.get('x-vercel-ip-timezone');
  if (incomingZone) requestHeaders.set('x-inqsi-timezone', incomingZone);

  const geo = `${request.geo?.country || ''}-${request.geo?.region || ''}`.replace(/-$/, '');
  const blocked = flags.blockedRegions.some((code) => {
    const needle = code.toUpperCase();
    return geo.toUpperCase() === needle || geo.toUpperCase().startsWith(`${needle}-`) || request.geo?.country?.toUpperCase() === needle;
  });

  if (blocked && !request.nextUrl.pathname.startsWith('/legal')) {
    const url = request.nextUrl.clone();
    url.pathname = '/legal/safe-use';
    const redirect = NextResponse.redirect(url);
    Object.entries(flagHeaders(flags)).forEach(([key, value]) => redirect.headers.set(key, value));
    return redirect;
  }

  if (!flags.calcEnabled && request.nextUrl.pathname.startsWith('/arbitrage-v2/calculator')) {
    const url = request.nextUrl.clone();
    url.pathname = '/arbitrage-v2';
    return NextResponse.redirect(url, { headers: flagHeaders(flags) });
  }

  if (request.nextUrl.pathname === '/calculator') {
    const url = request.nextUrl.clone();
    url.pathname = flags.calcEnabled ? '/arbitrage-v2/calculator' : '/arbitrage-v2';
    return NextResponse.rewrite(url, { request: { headers: requestHeaders } });
  }

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  Object.entries(flagHeaders(flags)).forEach(([key, value]) => response.headers.set(key, value));
  if (incomingZone) response.headers.set('x-inqsi-timezone', incomingZone);
  if (request.nextUrl.pathname.startsWith('/v1/inqsi') || request.nextUrl.pathname.startsWith('/arbitrage-v2')) {
    response.headers.set('cache-control', 'no-store');
  }
  return response;
}
