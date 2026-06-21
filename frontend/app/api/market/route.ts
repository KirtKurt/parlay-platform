import { NextResponse } from 'next/server';
import { getProviderOverallStatus } from '@/lib/inqsi-data-provider';

export const dynamic = 'force-dynamic';

export async function GET() {
  return NextResponse.json({
    product: 'InQsi',
    ...getProviderOverallStatus(),
    dataPolicy: 'Verified providers only. Missing data returns Working on it.'
  });
}
