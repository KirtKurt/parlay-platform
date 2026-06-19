import { AppHeader } from '@/components/AppHeader';

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
    <main className="shell legal-shell">
      <AppHeader title={title} />
      <section className="legal-hero panel">
        <p className="eyebrow blue">Silvers Syndicate legal</p>
        <h2>{title}</h2>
        <p>{intro}</p>
        <span>Last updated: {updated}</span>
      </section>

      <section className="legal-doc-grid">
        <aside className="panel legal-note">
          <h3>Plain-English summary</h3>
          <p>
            Silvers Syndicate is a sports market intelligence and research product. It is not a sportsbook,
            does not take wagers, does not guarantee outcomes, and is not affiliated with any league, team,
            sportsbook, data provider, or governing body.
          </p>
          <p>
            These pages are a strong launch baseline. Final launch language should be reviewed by qualified counsel
            before paid production traffic is opened.
          </p>
        </aside>
        <article className="panel legal-doc">
          {sections.map((section) => (
            <section key={section.title}>
              <h3>{section.title}</h3>
              {section.body.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
            </section>
          ))}
        </article>
      </section>
    </main>
  );
}
