import { NextRequest, NextResponse } from 'next/server';

export function middleware(request: NextRequest) {
  const key = process.env['INDEXNOW' + '_KEY'] || '';
  const pathname = request.nextUrl.pathname;

  if (key && pathname === `/${key}.txt`) {
    return new NextResponse(key, {
      headers: { 'Content-Type': 'text/plain; charset=utf-8' }
    });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)']
};
