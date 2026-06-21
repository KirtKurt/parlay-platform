import { NextResponse } from 'next/server';
import { evaluateMarketSignal } from '@/lib/inqsi-signal-engine';

export const dynamic = 'force-dynamic';

export async function GET() {
  const result = evaluateMarketSignal([]);
  return NextResponse.json({
    product: 'InQsi',
    status: result.status,
    result,
    note: 'Connect verified market snapshots to activate full scoring.'
  });
}
