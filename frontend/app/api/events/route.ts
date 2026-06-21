import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

type EventPayload = Record<string, unknown>;

const sensitiveKeys = ['password', 'passcode', 'card', 'cvv', 'ssn', 'token', 'authorization', 'secret'];

function scrub(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(scrub);
  if (value && typeof value === 'object') {
    return Object.entries(value as EventPayload).reduce<EventPayload>((acc, [key, entry]) => {
      if (sensitiveKeys.some((sensitive) => key.toLowerCase().includes(sensitive))) {
        acc[key] = '[redacted]';
      } else if (key.toLowerCase().includes('email')) {
        acc[key] = '[redacted-email]';
      } else {
        acc[key] = scrub(entry);
      }
      return acc;
    }, {});
  }
  return value;
}

export async function POST(request: NextRequest) {
  const consent = request.headers.get('x-inqsi-consent');
  if (consent !== 'analytics' && consent !== 'marketing' && consent !== 'replay') {
    return NextResponse.json({ status: 'rejected', reason: 'missing_consent' }, { status: 403 });
  }

  const body = await request.json().catch(() => null);
  if (!body) return NextResponse.json({ status: 'rejected', reason: 'invalid_body' }, { status: 400 });

  const event = scrub({
    ...body,
    receivedAt: new Date().toISOString(),
    source: 'inqsi-web'
  });

  // Production target: forward this event to AWS Firehose/S3 or another event warehouse.
  // Current behavior avoids silent failure and returns the sanitized payload for validation.
  return NextResponse.json({ status: 'accepted', event });
}
