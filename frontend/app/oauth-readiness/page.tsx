import type { Metadata } from 'next';
import { OAuthButtons } from '@/components/OAuthButtons';

export const metadata: Metadata = {
  title: 'OAuth Readiness',
  description: 'Google and Apple OAuth readiness for InQsi member access.',
  alternates: { canonical: '/oauth-readiness' }
};

export default function OAuthReadinessPage() {
  return (
    <main className="inqsi-shell">
      <header className="inqsi-topbar"><a className="inqsi-brand" href="/"><span className="inqsi-logo-mark">Q</span><span><b>InQsi</b><small>OAuth readiness</small></span></a></header>
      <section className="inqsi-hero">
        <div className="inqsi-hero-card"><p className="inqsi-promo">Member access</p><h1>Google and Apple sign-in package.</h1><p>Start routes are prepared. Callback token exchange stays reserved until provider secrets are connected and token verification is enabled.</p><OAuthButtons /></div>
        <aside className="inqsi-signup-card"><h2>Required keys</h2><p>Google: client ID, client secret, redirect URI.</p><p>Apple: client ID, team ID, key ID, private key, redirect URI.</p></aside>
      </section>
    </main>
  );
}
