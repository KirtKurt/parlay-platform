type ContentBlockProps = {
  eyebrow?: string;
  title: string;
  body: string;
  items?: { title: string; detail: string }[];
};

export function ContentBlock({ eyebrow = 'Guide', title, body, items = [] }: ContentBlockProps) {
  return (
    <section className="panel" style={{ marginTop: 20 }}>
      <div className="panel-header compact">
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h3>{title}</h3>
        </div>
      </div>
      <p className="hero-copy" style={{ maxWidth: 980, marginBottom: items.length ? 18 : 0 }}>{body}</p>
      {items.length > 0 && (
        <div className="status-row" style={{ marginBottom: 0 }}>
          {items.map((item) => (
            <article className="status-card" key={item.title}>
              <span>Learn</span>
              <strong>{item.title}</strong>
              <p>{item.detail}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
