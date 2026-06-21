'use client';

import { useEffect } from 'react';

const KEY = 'inqsi_partner_code_v1';

function getCode(search: URLSearchParams) {
  return search.get('ref') || search.get('creator') || search.get('promo') || search.get('code');
}

export function PartnerCapture() {
  useEffect(() => {
    const url = new URL(window.location.href);
    const code = getCode(url.searchParams);
    if (!code) return;
    const payload = {
      code,
      visitorId: `visitor_${Date.now()}_${Math.random().toString(36).slice(2)}`,
      landingPage: `${url.pathname}${url.search}`,
      firstSeenAt: new Date().toISOString(),
      utm_source: url.searchParams.get('utm_source'),
      utm_medium: url.searchParams.get('utm_medium'),
      utm_campaign: url.searchParams.get('utm_campaign')
    };
    localStorage.setItem(KEY, JSON.stringify(payload));
    const apiBase = process.env.NEXT_PUBLIC_INQSI_API_URL;
    if (!apiBase) return;
    void fetch(`${apiBase}/v1/attribution/capture`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ referralCode: code, visitorId: payload.visitorId, landingPage: payload.landingPage, utm_source: payload.utm_source, utm_medium: payload.utm_medium, utm_campaign: payload.utm_campaign })
    }).catch(() => undefined);
  }, []);
  return null;
}
