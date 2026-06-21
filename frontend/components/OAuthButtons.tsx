'use client';

function go(provider: 'google' | 'apple') {
  const base = process.env.NEXT_PUBLIC_INQSI_API_URL;
  if (!base) {
    alert('Working on it. OAuth API URL is not configured yet.');
    return;
  }
  window.location.href = `${base}/v1/oauth/${provider}/start`;
}

export function OAuthButtons() {
  return (
    <div className="inqsi-oauth-actions">
      <button type="button" onClick={() => go('google')}>Continue with Google</button>
      <button type="button" onClick={() => go('apple')}>Continue with Apple</button>
    </div>
  );
}
