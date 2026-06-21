import { NextResponse } from 'next/server';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';
const indexNowKey = process.env.INDEXNOW_KEY || '';

export async function POST() {
  if (!indexNowKey) {
    return NextResponse.json({ ok: false, message: 'INDEXNOW_KEY is not configured.' }, { status: 500 });
  }

  const urlList = [
    `${siteUrl}/`,
    `${siteUrl}/ai-slip-scanner`,
    `${siteUrl}/3-leg-parlay-guide`,
    `${siteUrl}/line-movement-review`,
    `${siteUrl}/best-lines`,
    `${siteUrl}/u/inqsi-member`
  ];

  const response = await fetch('https://api.indexnow.org/indexnow', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ host: new URL(siteUrl).host, key: indexNowKey, keyLocation: `${siteUrl}/${indexNowKey}.txt`, urlList })
  });

  return NextResponse.json({ ok: response.ok, status: response.status, submitted: urlList.length });
}
