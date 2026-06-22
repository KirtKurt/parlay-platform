'use client';

import { FormEvent, useMemo, useState } from 'react';

type ScanState = {
  loading: boolean;
  result?: unknown;
  error?: string;
};

const sports = ['NFL', 'CFB', 'NBA', 'NCAAM', 'NHL', 'MLB', 'WNBA', 'Soccer', 'Tennis'];

export function SlipScannerClient() {
  const [memberId, setMemberId] = useState('');
  const [state, setState] = useState<ScanState>({ loading: false });
  const apiBase = useMemo(() => (process.env.NEXT_PUBLIC_INQSI_API_URL || '').replace(/\/$/, ''), []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState({ loading: true });
    const form = new FormData(event.currentTarget);
    const legs = [1, 2, 3].map((index) => ({
      sport: String(form.get(`sport_${index}`) || '').toUpperCase(),
      marketType: String(form.get(`market_${index}`) || ''),
      selection: String(form.get(`selection_${index}`) || ''),
      book: String(form.get(`book_${index}`) || ''),
      oddsAmerican: String(form.get(`odds_${index}`) || ''),
      line: String(form.get(`line_${index}`) || '')
    })).filter((leg) => leg.sport && leg.marketType && leg.selection);

    if (!apiBase) {
      setState({ loading: false, error: 'NEXT_PUBLIC_INQSI_API_URL is not configured for this frontend build.' });
      return;
    }
    if (!memberId.trim()) {
      setState({ loading: false, error: 'Member ID is required until full auth is connected.' });
      return;
    }

    try {
      const response = await fetch(`${apiBase}/v1/scanner/scan`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-inqsi-member-id': memberId.trim()
        },
        body: JSON.stringify({ legs, save: true })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'scan_failed');
      setState({ loading: false, result: payload });
    } catch (error) {
      setState({ loading: false, error: error instanceof Error ? error.message : 'scan_failed' });
    }
  }

  return (
    <section className="inqsi-panel">
      <div className="inqsi-section-head"><h2>Live scanner input</h2><span>Backend connected</span></div>
      <p className="inqsi-empty">This calls the real InQsi backend. Without verified market snapshots, the backend returns MARKET_DATA_REQUIRED instead of a fake grade.</p>
      <form onSubmit={onSubmit} className="inqsi-game-list" style={{ marginTop: 14 }}>
        <label className="inqsi-mini-card">
          <b>Member ID</b>
          <input value={memberId} onChange={(event) => setMemberId(event.target.value)} placeholder="mem_..." style={{ width: '100%', marginTop: 8 }} />
        </label>
        {[1, 2, 3].map((index) => (
          <article className="inqsi-game-card" key={index}>
            <div className="inqsi-game-row"><b>Leg {index}</b><span className="inqsi-score-chip">Input</span></div>
            <div className="inqsi-market-grid">
              <label><span>Sport</span><select name={`sport_${index}`} defaultValue=""><option value="">Select</option>{sports.map((sport) => <option key={sport} value={sport}>{sport}</option>)}</select></label>
              <label><span>Market</span><select name={`market_${index}`} defaultValue="moneyline"><option value="moneyline">Moneyline</option><option value="spread">Spread</option><option value="total">Total</option></select></label>
              <label><span>Selection</span><input name={`selection_${index}`} placeholder="Team / side" /></label>
              <label><span>Book</span><input name={`book_${index}`} placeholder="Fanatics / DK / FD" /></label>
              <label><span>Odds</span><input name={`odds_${index}`} placeholder="-110" /></label>
              <label><span>Line</span><input name={`line_${index}`} placeholder="optional" /></label>
            </div>
          </article>
        ))}
        <button className="inqsi-primary" type="submit" disabled={state.loading}>{state.loading ? 'Scanning...' : 'Scan and save slip'}</button>
      </form>
      {state.error ? <p className="inqsi-empty" style={{ marginTop: 12 }}>Error: {state.error}</p> : null}
      {state.result ? <pre className="inqsi-empty" style={{ marginTop: 12, overflowX: 'auto' }}>{JSON.stringify(state.result, null, 2)}</pre> : null}
    </section>
  );
}
