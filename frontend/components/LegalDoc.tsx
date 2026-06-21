import Link from 'next/link';

export type LegalSection = {
  title: string;
  body: string[];
};

export function LegalDoc({
  title,
  updated,
  intro,
  sections
}: {
  title: string;
  updated: string;
  intro: string;
  sections: LegalSection[];
}) {
  return (
    <main className="inqsi-shell legal-shell">
      <header className="inqsi-topbar">
        <Link className="inqsi-brand" href="/" aria-label="InQsi home"><span className="inqsi-logo-mark" aria-hidden="true">Q</span><span><b>InQsi</b><small>Legal</small></span></Link>
        <nav className="inqsi-nav-actions" aria-label="Legal navigation"><Link href="/legal/privacy">Privacy</Link><Link href="/legal/cookies">Cookies</Link><Link href="/privacy-choices">Choices</Link></nav>
      </header>
      <section className="inqsi-hero inqsi-seo-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">InQsi legal</p>
          <h1>{title}</h1>
          <p>{intro}</p>
          <small>Last updated: {updated}</small>
        </div>
        <aside className="inqsi-signup-card">
          <h2>Plain-English summary</h2>
          <p>InQsi is a sports market intelligence and review product. It is not a sportsbook, does not take wagers, does not guarantee outcomes, and is not affiliated with any league, team, sportsbook, data provider, or governing body.</p>
          <a href="/data-deletion">Request deletion</a>
          <a href="/data-export">Request export</a>
          <small>Final launch language should be reviewed by qualified counsel before paid production traffic opens.</small>
        </aside>
      </section>

      <section className="inqsi-panel legal-doc">
        {sections.map((section) => (
          <section key={section.title}>
            <h2>{section.title}</h2>
            {section.body.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
          </section>
        ))}
      </section>
    </main>
  );
}
