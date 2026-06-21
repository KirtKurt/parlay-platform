import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

type RequestType = 'delete' | 'export' | 'opt_out' | 'correct';

function isRequestType(value: unknown): value is RequestType {
  return value === 'delete' || value === 'export' || value === 'opt_out' || value === 'correct';
}

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => null) as { type?: unknown; email?: unknown; notes?: unknown } | null;

  if (!body || !isRequestType(body.type) || typeof body.email !== 'string') {
    return NextResponse.json({ status: 'rejected', reason: 'type_and_email_required' }, { status: 400 });
  }

  const intake = {
    type: body.type,
    email: '[redacted-email]',
    notesReceived: typeof body.notes === 'string' && body.notes.length > 0,
    receivedAt: new Date().toISOString(),
    status: 'working_on_it'
  };

  // Production target: send this to the support/privacy queue after mailbox or ticketing integration is connected.
  return NextResponse.json({ status: 'accepted', intake, message: 'Request received. Connect support queue for production processing.' });
}
