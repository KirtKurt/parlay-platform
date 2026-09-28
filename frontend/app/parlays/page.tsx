import type { Metadata } from 'next';
import Link from 'next/link';
import { getApiSnapshot } from '@/lib/api';
import { AppHeader } from '@/components/AppHeader';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: 'Official Hourly Parlays',
  description: 'Official InQsi 3-leg parlays with a per-leg breakdown. Combined odds stay hidden until the hourly builder publishes a live price.',
  alternates: { canonical: '/parlays' }
};

function isSampleParlay(row: any) {
  const blob = JSON.stringify(row || {}).toLowerCase();
  return (
    blob.includes('+342') ||
    blob.includes('"342"') ||
    /confidence"?:\s*(70|76|82)/.test(blob) ||
    blob.includes('celtics') ||
    blob.includes('dodgers') ||
    blob.includes('thunder') ||
    blob.includes('sample') ||
    blob.includes('preview slip')
  );
}

function parlayLegs(row: any) {
  const raw = Array.isArray(row?.legs) ? row.legs : Array.isArray(row?.picks) ? row.picks : [];
  return raw.map((leg: any, index: number) => {
    if (typeof leg === 'string') return { label: leg, why: '' };
    return {
      label: String(leg?.team || leg?.pick || leg?.selection || leg?.name || `Leg ${index + 1}`),
      why: String(leg?.note || leg?.explanation || leg?.why || ''),
    };
  }).filter((leg: { label: string }) => leg.label);
}

export default async function ParlaysPage() {
  const { rankings, apiStatus, apiDetail } = await getApiSnapshot();
  const official = (Array.isArray(rankings) ? rankings : []).filter((row) => !isSampleParlay(row)).slice(0, 6);

  return (
    <main className="inqsi-shell tool-shell">
      <AppHeader title="Official Hourly Parlays" apiStatus={apiStatus} apiDetail={apiDetail} />
      <nav className="tool-tabs" aria-label="InQsi tools">
        <Link className="tool-tab" href="/">Games</Link>
        <Link className="tool-tab" href="/arbitrage-v2">ARB</Link>
        <Link className="tool-tab on" href="/parlays">3-Leg</Link>
        <Link className="tool-tab" href="/game-leans">Leans</Link>
        <Link className="tool-tab" href="/parlay-scanner">Scan</Link>
      </nav>
      <section className="tool-feed">
        <div className="tool-feed-head">
          <div>
            <p className="eyebrow">Official 3-leg</p>
            <h2>Breakdown before the price</h2>
          </div>
          <span className="data-status">{official.length ? `${official.length} published` : 'Waiting'}</span>
        </div>
        <p className="movement">This page only shows hourly builder output. Combined odds stay hidden until a live price exists. InQsi does not fill the list with sportsbook favorites.</p>
        {official.length ? official.map((row: any, index: number) => {
          const legs = parlayLegs(row);
          return (
            <article className="tool-row" key={row.id || index}>
              <div>
                <small>{row.structure || '3-LEG'}</small>
                <strong>{row.title || row.structure || 'Official hourly structure'}</strong>
                <p>{row.note || row.explanation || 'Published from the hourly builder. Review each leg before lock-in.'}</p>
                {legs.length > 0 && (
                  <ol className="tool-legs">
                    {legs.slice(0, 3).map((leg: { label: string; why: string }) => (
                      <li key={leg.label}>{leg.label}{leg.why ? ` — ${leg.why}` : ''}</li>
                    ))}
                  </ol>
                )}
              </div>
              <b className="tool-edge">{row.american || row.combined_odds || 'Waiting'}</b>
            </article>
          );
        }) : (
          <article className="tool-row">
            <div>
              <small>WAITING</small>
              <strong>No official 3-leg price yet</strong>
              <p>When the hourly builder publishes, each slip shows three legs and why they were grouped. Until then this stays empty instead of copying a sportsbook board.</p>
            </div>
            <span className="tool-badges">
              <Link href="/parlay-scanner">Scan</Link>
              <Link href="/game-leans">Leans</Link>
            </span>
          </article>
        )}
      </section>
    </main>
  );
}
