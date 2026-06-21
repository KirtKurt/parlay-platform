import { NextRequest, NextResponse } from 'next/server';
import { listSnapshots, saveSnapshot } from '@/lib/inqsi-snapshot-store';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  const eventId = request.nextUrl.searchParams.get('eventId') || undefined;
  return NextResponse.json(await listSnapshots(eventId));
}

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => null);
  if (!body || typeof body !== 'object') {
    return NextResponse.json({ status: 'rejected', reason: 'invalid_body' }, { status: 400 });
  }

  const record = {
    sport: String((body as Record<string, unknown>).sport || 'unknown'),
    eventId: String((body as Record<string, unknown>).eventId || 'unknown'),
    source: String((body as Record<string, unknown>).source || 'unknown'),
    cadence: 'manual_review' as const,
    capturedAt: new Date().toISOString(),
    payload: body as Record<string, unknown>
  };

  return NextResponse.json(await saveSnapshot(record));
}
