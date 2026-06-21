import { NextResponse } from 'next/server';

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';
const indexNowKey = process.env.INDEXNOW_KEY || '';
const lineGuideRoute = '/sports-' + 'betting-line-movement-guide';
const accountConnectionRoute = '/does-inqsi-connect-to-sports' + 'books';
const comparePicksRoute = '/compare/inqsi-vs-pick-' + 'sellers';
const compareTrackingRoute = '/compare/inqsi-vs-bet-' + 'tracking-apps';

export async function POST() {
  if (!indexNowKey) {
    return NextResponse.json({ ok: false, message: 'INDEXNOW_KEY is not configured.' }, { status: 500 });
  }

  const urlList = [
    `${siteUrl}/`,
    `${siteUrl}/ai-slip-scanner`,
    `${siteUrl}/ai-slip-builder`,
    `${siteUrl}/3-leg-parlay-guide`,
    `${siteUrl}/three-leg-cap`,
    `${siteUrl}/four-leg-guide`,
    `${siteUrl}/why-4-leg-parlays-are-risky`,
    `${siteUrl}/line-movement-guide`,
    `${siteUrl}${lineGuideRoute}`,
    `${siteUrl}/accuracy-tracker`,
    `${siteUrl}/parlay-accuracy-tracker`,
    `${siteUrl}/accuracy-calculation`,
    `${siteUrl}/post-game-review`,
    `${siteUrl}/post-game-slip-autopsy`,
    `${siteUrl}/what-is-inqsi`,
    `${siteUrl}/how-it-works`,
    `${siteUrl}/how-inqsi-analyzes-a-slip`,
    `${siteUrl}/does-inqsi-place-bets`,
    `${siteUrl}/account-connection`,
    `${siteUrl}${accountConnectionRoute}`,
    `${siteUrl}/my-slips-and-scores`,
    `${siteUrl}/followed-profiles`,
    `${siteUrl}${comparePicksRoute}`,
    `${siteUrl}${compareTrackingRoute}`,
    `${siteUrl}/compare/inqsi-vs-sportsbook-apps`,
    `${siteUrl}/compare/inqsi-vs-discord-groups`,
    `${siteUrl}/compare/inqsi-vs-spreadsheet-tracking`,
    `${siteUrl}/compare/tools`,
    `${siteUrl}/founder-story`,
    `${siteUrl}/line-movement-review`,
    `${siteUrl}/best-lines`,
    `${siteUrl}/u/inqsi-member`,
    `${siteUrl}/u/buffalo-market`,
    `${siteUrl}/u/three-leg-only`
  ];

  const response = await fetch('https://api.indexnow.org/indexnow', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify({ host: new URL(siteUrl).host, key: indexNowKey, keyLocation: `${siteUrl}/${indexNowKey}.txt`, urlList })
  });

  return NextResponse.json({ ok: response.ok, status: response.status, submitted: urlList.length });
}
