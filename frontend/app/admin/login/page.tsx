import type { Metadata } from 'next';
import { notFound, redirect } from 'next/navigation';
import { hasInternalSession, isInternalPortalEnabled } from '@/lib/internal-access';

export const metadata: Metadata = {
  title: 'Internal Access | InQsi',
  robots: { index: false, follow: false }
};

export default function Page() {
  if (!isInternalPortalEnabled()) notFound();
  if (hasInternalSession()) redirect('/admin');

  return (
    <main className="shell">
      <section className="hero-card glass-card" style={{ maxWidth: 560, margin: '60px auto' }}>
        <p className="eyebrow blue">Owner access</p>
        <h2>InQsi internal portal</h2>
        <p className="hero-copy">Enter the internal access PIN to view member, source, score, support, SEO, and settings tools.</p>
        <form action="/api/admin/session" method="post" style={{ display: 'grid', gap: 12, marginTop: 20 }}>
          <input name="pin" type="password" placeholder="Internal access PIN" required style={{ borderRadius: 14, border: '1px solid rgba(255,255,255,.18)', padding: '14px 16px', background: 'rgba(255,255,255,.06)', color: '#eef7ff' }} />
          <button className="primary-button" type="submit">Open internal portal</button>
        </form>
        <p className="movement" style={{ marginTop: 16 }}>This page is noindex and intended only for approved internal access.</p>
      </section>
    </main>
  );
}
