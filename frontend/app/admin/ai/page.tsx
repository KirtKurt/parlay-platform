import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { AppHeader } from '@/components/AppHeader';
import { hasInternalSession, isInternalPortalEnabled } from '@/lib/internal-access';

const tools = [
  {
    title: 'Review InQsi code',
    value: 'code_review',
    model: 'gpt-5-mini · medium reasoning',
    note: 'Reviews files, diffs, architecture, security, and missing tests.'
  },
  {
    title: 'Diagnose GitHub/AWS deployment errors',
    value: 'deployment_diagnosis',
    model: 'gpt-5-mini · medium reasoning',
    note: 'Turns failed workflow or CloudFormation output into a direct fix plan.'
  },
  {
    title: 'Summarize failed logs',
    value: 'failed_log_summary',
    model: 'gpt-5-mini · medium reasoning',
    note: 'Condenses raw logs into cause, impact, and next action.'
  },
  {
    title: 'Build internal admin AI tools',
    value: 'admin_tool_plan',
    model: 'gpt-5-mini · medium reasoning',
    note: 'Plans owner-only tools without exposing secrets in the browser.'
  },
  {
    title: 'Sports API algorithm lab',
    value: 'sports_api_algorithm_lab',
    model: 'gpt-5-pro · high reasoning',
    note: 'Analyzes real sports API/market samples for stronger scoring logic. No fake data, no guarantees.'
  }
];

export const metadata: Metadata = {
  title: 'Internal AI Tools | InQsi',
  robots: { index: false, follow: false }
};

export default function Page() {
  if (!isInternalPortalEnabled()) notFound();
  if (!hasInternalSession()) redirect('/admin/login');

  return (
    <main className="shell">
      <AppHeader eyebrow="InQsi" title="Internal AI Tools" />

      <section className="hero-card glass-card" style={{ minHeight: 0, marginBottom: 20 }}>
        <p className="eyebrow blue">Owner-only AI layer</p>
        <h2>Controlled AI tools for engineering, deployment, and algorithm research.</h2>
        <p className="hero-copy">These tools run through AWS Lambda, Secrets Manager, and OpenAI. The member-facing app stays protected; no OpenAI key or admin token is placed in browser code.</p>
        <Link className="primary-button" href="/admin" style={{ display: 'inline-block', marginTop: 16, textDecoration: 'none' }}>Back to admin</Link>
      </section>

      <section className="content-grid">
        {tools.map((tool) => (
          <article className="panel" key={tool.value}>
            <p className="eyebrow blue">{tool.value}</p>
            <h3>{tool.title}</h3>
            <p className="movement">{tool.note}</p>
            <p className="movement"><strong>Default:</strong> {tool.model}</p>
          </article>
        ))}
      </section>

      <section className="panel" style={{ marginTop: 20 }}>
        <p className="eyebrow blue">How to run</p>
        <h3>Use GitHub Actions for now</h3>
        <p className="movement">Open GitHub → Actions → InQsi AI Tools → Run workflow. Choose the tool, paste the prompt/context, and run. This keeps the workflow private and avoids exposing secrets in the frontend.</p>
      </section>
    </main>
  );
}
