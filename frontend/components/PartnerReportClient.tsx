'use client';

import { useEffect, useState } from 'react';

type PartnerReport = {
  creator?: {
    creator_id?: string;
    creator_name?: string;
    handle?: string;
    referral_code?: string;
    campaign_name?: string;
    commission_type?: string;
    commission_amount?: number;
  };
  metrics?: {
    activePaidMembers: number;
    trialMembers: number;
    canceledMembers: number;
    pastDueMembers: number;
    liveMrrCents: number;
    payoutDueCents: number;
    commissionType?: string;
    commissionAmount?: number;
  };
  privacy?: string;
};

function dollars(cents?: number) {
  return `$${((cents || 0) / 100).toFixed(2)}`;
}

export function PartnerReportClient({ token }: { token: string }) {
  const [report, setReport] = useState<PartnerReport | null>(null);
  const [message, setMessage] = useState('Loading creator report...');

  useEffect(() => {
    const apiBase = process.env.NEXT_PUBLIC_INQSI_API_URL;
    if (!apiBase) {
      setMessage('Working on it. Report API URL is not configured yet.');
      return;
    }
    fetch(`${apiBase}/v1/creator-reports/${token}`)
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Report unavailable');
        setReport(data);
        setMessage('');
      })
      .catch((error) => setMessage(error.message));
  }, [token]);

  if (message) {
    return (
      <main className="inqsi-shell">
        <section className="inqsi-hero"><div className="inqsi-hero-card"><p className="inqsi-promo">Creator report</p><h1>{message}</h1><p>No customer emails or personal member records are shown here.</p></div></section>
      </main>
    );
  }

  const creator = report?.creator;
  const metrics = report?.metrics;

  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar"><a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>Creator Partner Report</small></span></a></header>
      <section className="inqsi-hero">
        <div className="inqsi-hero-card"><p className="inqsi-promo">Private report</p><h1>{creator?.creator_name || 'Creator partner'} performance.</h1><p>Referral code: {creator?.referral_code}. This report is aggregate-only and does not expose customer emails.</p></div>
        <aside className="inqsi-signup-card"><h2>Payout due</h2><p>{dollars(metrics?.payoutDueCents)}</p><small>Based on the commission terms stored for this creator.</small></aside>
      </section>
      <section className="inqsi-feature-grid">
        <article><b>Active paid members</b><span>{metrics?.activePaidMembers ?? 0}</span></article>
        <article><b>Trial members</b><span>{metrics?.trialMembers ?? 0}</span></article>
        <article><b>Canceled members</b><span>{metrics?.canceledMembers ?? 0}</span></article>
        <article><b>Past-due members</b><span>{metrics?.pastDueMembers ?? 0}</span></article>
        <article><b>MRR tied to creator</b><span>{dollars(metrics?.liveMrrCents)}</span></article>
        <article><b>Commission terms</b><span>{metrics?.commissionType || 'manual'} · {metrics?.commissionAmount ?? 0}</span></article>
      </section>
      <section className="inqsi-panel"><div className="inqsi-section-head"><h2>Privacy rule</h2><span>Aggregate only</span></div><p>{report?.privacy || 'Customer emails are not shown by default.'}</p></section>
    </main>
  );
}
