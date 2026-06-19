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
    <section style={{ position: 'relative', marginTop: 8 }}>
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
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'flex-start',
          padding: 'clamp(14px, 3.5vh, 42px) 18px 18px',
          background: 'linear-gradient(180deg, rgba(5,8,20,0.34), rgba(5,8,20,0.78))',
          borderRadius: 28
        }}
      >
        <div
          className="glass-card"
          style={{
            width: 'min(720px, 100%)',
            padding: 'clamp(20px, 3vw, 30px)',
            textAlign: 'center',
            borderColor: 'rgba(32,242,159,0.32)'
          }}
        >
          <p className="eyebrow blue">Free preview</p>
          <h3 style={{ fontSize: 'clamp(1.65rem, 3vw, 2.45rem)', marginBottom: 12 }}>{title}</h3>
          <p className="movement" style={{ margin: '0 auto 12px', maxWidth: 560 }}>{message}</p>
          <p className="movement" style={{ margin: '0 auto 22px', maxWidth: 600, color: '#ffd166' }}>{teaser}</p>
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
