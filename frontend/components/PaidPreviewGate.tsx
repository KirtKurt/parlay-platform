import Link from 'next/link';
import type { ReactNode } from 'react';

type PaidPreviewGateProps = {
  children: ReactNode;
  title?: string;
  message?: string;
  teaser?: string;
};

export function PaidPreviewGate({
  children,
  title = 'Members see the full board',
  message = 'Create an account or log in to unlock the complete line movement, rankings, parlay build logic, and game-level signal detail.',
  teaser = 'Free preview: slate count, high-level signals, and sample movement are visible. Full odds movement and ranked combinations unlock after registration.'
}: PaidPreviewGateProps) {
  return (
    <section style={{ position: 'relative', marginTop: 20 }}>
      <div
        aria-hidden="true"
        style={{
          filter: 'blur(8px)',
          opacity: 0.42,
          pointerEvents: 'none',
          userSelect: 'none',
          maxHeight: 680,
          overflow: 'hidden'
        }}
      >
        {children}
      </div>

      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'grid',
          placeItems: 'center',
          padding: 18,
          background: 'linear-gradient(180deg, rgba(5,8,20,0.1), rgba(5,8,20,0.76))',
          borderRadius: 28
        }}
      >
        <div
          className="glass-card"
          style={{
            width: 'min(620px, 100%)',
            padding: 26,
            textAlign: 'center',
            borderColor: 'rgba(32,242,159,0.32)'
          }}
        >
          <p className="eyebrow blue">Free preview</p>
          <h3 style={{ fontSize: '1.85rem', marginBottom: 12 }}>{title}</h3>
          <p className="movement" style={{ margin: '0 auto 12px', maxWidth: 500 }}>{message}</p>
          <p className="movement" style={{ margin: '0 auto 22px', maxWidth: 520, color: '#ffd166' }}>{teaser}</p>
          <div className="hero-actions" style={{ justifyContent: 'center' }}>
            <Link className="primary-button large" href="/register" style={{ textDecoration: 'none' }}>Create Account</Link>
            <Link className="ghost-button large" href="/login" style={{ textDecoration: 'none' }}>Log In</Link>
            <Link className="ghost-button large" href="/pricing" style={{ textDecoration: 'none' }}>View Plans</Link>
          </div>
        </div>
      </div>
    </section>
  );
}
