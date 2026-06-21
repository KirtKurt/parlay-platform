import Link from 'next/link';

export function PromoBanner() {
  return (
    <div
      role="note"
      aria-label="Launch promotion"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 14,
        padding: '10px 16px',
        marginBottom: 12,
        borderRadius: 999,
        border: '1px solid rgba(32,242,159,0.34)',
        background: 'linear-gradient(90deg, rgba(32,242,159,0.16), rgba(57,167,255,0.12))',
        color: '#eef4ff',
        boxShadow: '0 14px 42px rgba(0,0,0,0.24)',
        flexWrap: 'wrap'
      }}
    >
      <div style={{ lineHeight: 1.45 }}>
        <strong style={{ color: '#20f29f' }}>Launch promo: 5 days free.</strong>
        <span style={{ color: '#b9c5db' }}> Preview the market board, scan your first slip, and see how risk detection works before monthly membership starts.</span>
      </div>
      <Link
        href="/register?promo=5-days"
        style={{
          textDecoration: 'none',
          color: '#04101d',
          background: 'linear-gradient(135deg, #20f29f, #39a7ff)',
          borderRadius: 999,
          padding: '8px 14px',
          fontWeight: 900,
          whiteSpace: 'nowrap'
        }}
      >
        Start 5 days free
      </Link>
    </div>
  );
}
