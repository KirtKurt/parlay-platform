type Faq = { question: string; answer: string };

type InqsiSeoPageProps = {
  eyebrow: string;
  title: string;
  intro: string;
  sections: { title: string; copy: string }[];
  faqs?: Faq[];
  path: string;
};

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://inqsi.app';

export function InqsiSeoPage({ eyebrow, title, intro, sections, faqs = [], path }: InqsiSeoPageProps) {
  const url = `${siteUrl}${path}`;
  const breadcrumbJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: 'InQsi', item: siteUrl },
      { '@type': 'ListItem', position: 2, name: title, item: url }
    ]
  };
  const faqJsonLd = faqs.length
    ? {
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        mainEntity: faqs.map((faq) => ({
          '@type': 'Question',
          name: faq.question,
          acceptedAnswer: { '@type': 'Answer', text: faq.answer }
        }))
      }
    : null;

  return (
    <main className="inqsi-shell">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumbJsonLd) }} />
      {faqJsonLd && <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(faqJsonLd) }} />}

      <section className="inqsi-hero inqsi-seo-hero">
        <div className="inqsi-hero-card">
          <p className="inqsi-promo">{eyebrow}</p>
          <h1>{title}</h1>
          <p>{intro}</p>
        </div>
        <aside className="inqsi-signup-card">
          <h2>Start with 5 days free</h2>
          <p>Use InQsi to review market movement, risk signals, game leans, and account dashboards in one clean interface.</p>
          <a href="/register">Create account</a>
          <a href="/picks-audit">Review selections</a>
          <small>No fake data. If a verified feed is unavailable, InQsi shows Working on it.</small>
        </aside>
      </section>

      <section className="inqsi-feature-grid">
        {sections.map((section) => (
          <article key={section.title}>
            <b>{section.title}</b>
            <span>{section.copy}</span>
          </article>
        ))}
      </section>

      {faqs.length > 0 && (
        <section className="inqsi-panel">
          <div className="inqsi-section-head"><h2>Common questions</h2><span>FAQ</span></div>
          <div className="inqsi-game-list">
            {faqs.map((faq) => (
              <article className="inqsi-mini-card" key={faq.question}>
                <b>{faq.question}</b>
                <small>{faq.answer}</small>
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
