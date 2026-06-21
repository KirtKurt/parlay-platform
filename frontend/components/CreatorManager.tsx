'use client';

import { FormEvent, useState } from 'react';

type CreatorResult = {
  creator_id?: string;
  creator_name?: string;
  referral_code?: string;
  active?: boolean;
};

export function CreatorManager() {
  const [name, setName] = useState('');
  const [handle, setHandle] = useState('');
  const [code, setCode] = useState('');
  const [result, setResult] = useState<CreatorResult | null>(null);
  const [message, setMessage] = useState('');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage('Working on it...');
    const apiBase = process.env.NEXT_PUBLIC_INQSI_API_URL;
    if (!apiBase) {
      setMessage('Working on it. API URL is not configured yet.');
      return;
    }
    const response = await fetch(`${apiBase}/v1/creators`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ creatorName: name, handle, referralCode: code })
    });
    const data = await response.json();
    if (!response.ok) {
      setMessage(data.error || 'Creator could not be created.');
      return;
    }
    setResult(data.creator);
    setMessage('Creator code created.');
  }

  const shareCode = result?.referral_code || code;
  const shareLink = shareCode ? `/c/${shareCode}` : '';

  return (
    <section className="inqsi-hero">
      <form className="inqsi-hero-card" onSubmit={submit}>
        <p className="inqsi-promo">Create creator code</p>
        <h2>Add a creator or campaign.</h2>
        <label>Creator name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Creator name" required /></label>
        <label>Handle<input value={handle} onChange={(event) => setHandle(event.target.value)} placeholder="@handle" /></label>
        <label>Referral code<input value={code} onChange={(event) => setCode(event.target.value)} placeholder="mike25" required /></label>
        <button type="submit">Create creator code</button>
        <p>{message}</p>
      </form>
      <aside className="inqsi-signup-card">
        <h2>Share link</h2>
        {shareLink ? <p>{shareLink}</p> : <p>Create or enter a code to preview the share link.</p>}
        <p>Use the final domain when live: inqsi.app/c/code</p>
        {result && <p>Creator ID: {result.creator_id}</p>}
      </aside>
    </section>
  );
}
