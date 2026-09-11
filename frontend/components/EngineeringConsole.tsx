'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';

type Job = {
  id: string;
  status: string;
  instruction: string;
  authorizedScope: string[];
  repository: string;
  startingRevision: string;
  branch?: string;
  changedFiles: string[];
  diff?: string;
  logs: string[];
  testResults: unknown[];
  commit?: string;
  pullRequest?: string;
  error?: string;
  updatedAt: string;
};

type AuthState = 'checking' | 'verified' | 'unavailable' | 'denied';

export function EngineeringConsole() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<Job | null>(null);
  const [instruction, setInstruction] = useState('');
  const [scope, setScope] = useState('');
  const [message, setMessage] = useState('');
  const [authState, setAuthState] = useState<AuthState>('checking');

  const refresh = useCallback(async () => {
    try {
      const response = await fetch('/api/engineering', { cache: 'no-store' });
      if (response.status === 503) {
        setAuthState('unavailable');
        setMessage('Secure administrator authentication or the engineering service is not configured.');
        return;
      }
      if (response.status === 401 || response.status === 403) {
        setAuthState('denied');
        setMessage('Administrator access was not verified.');
        return;
      }
      if (!response.ok) {
        setAuthState('unavailable');
        setMessage('Engineering service request failed.');
        return;
      }
      const data = await response.json();
      setAuthState('verified');
      setMessage('');
      setJobs(data.jobs || []);
      if (selected) {
        const current = (data.jobs || []).find((job: Job) => job.id === selected.id);
        if (current) setSelected(current);
      }
    } catch {
      setAuthState('unavailable');
      setMessage('Engineering service is unreachable.');
    }
  }, [selected?.id]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const response = await fetch('/api/engineering', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        instruction,
        authorizedScope: scope.split(',').map((value) => value.trim()).filter(Boolean),
        startingRevision: 'HEAD'
      })
    });
    const data = await response.json();
    if (response.ok) {
      setInstruction('');
      setSelected(data.job);
      setMessage('');
      refresh();
    } else {
      setMessage(data.error || 'Submission failed');
    }
  }

  async function action(name: 'cancel' | 'continue') {
    const body = name === 'continue' ? JSON.stringify({ instruction }) : undefined;
    const response = await fetch(`/api/engineering/${selected?.id}/${name}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body
    });
    if (!response.ok) setMessage((await response.json()).error || 'Action failed');
    else refresh();
  }

  const badge = authState === 'verified'
    ? 'Admin · server verified'
    : authState === 'checking'
      ? 'Verifying administrator…'
      : authState === 'denied'
        ? 'Admin · access denied'
        : 'Engineering service unavailable';

  return <main className="shell engineering-console">
    <header className="topbar">
      <div className="brand-block">
        <span className="brand-mark">Q</span>
        <span><p className="eyebrow">InQsi private</p><h1>Engineering Console</h1></span>
      </div>
      <span className={`api-badge ${authState}`}>{badge}</span>
    </header>

    <section className="engineering-grid">
      <section className="panel">
        <p className="eyebrow blue">New authorized task</p>
        <h2>Direct the worker</h2>
        <form onSubmit={submit}>
          <label>Instructions
            <textarea required value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="Describe the engineering change and acceptance checks…" />
          </label>
          <label>Authorized paths (comma-separated)
            <input required placeholder="Choose an approved scope, such as engineering_console_publication_proof" value={scope} onChange={(event) => setScope(event.target.value)} />
          </label>
          <div className="scope-card">
            <b>KirtKurt/parlay-platform</b>
            <span>Starting revision: repository HEAD</span>
            <span>Worker credentials: repository-scoped only</span>
          </div>
          <button className="primary-button" disabled={authState !== 'verified' || !scope.trim()}>Submit job</button>
        </form>
        {message && <p role="alert">{message}</p>}

        <h3>Durable history</h3>
        <div className="job-list">
          {jobs.map((job) => <button key={job.id} onClick={() => setSelected(job)}>
            <span className={`job-status ${job.status}`}>{job.status.replace('_', ' ')}</span>
            <b>{job.instruction}</b>
            <small>{new Date(job.updatedAt).toLocaleString()}</small>
          </button>)}
        </div>
      </section>

      <section className="panel result-panel">
        {selected ? <>
          <div className="inqsi-section-head">
            <div><p className="eyebrow blue">Job {selected.id.slice(0, 8)}</p><h2>{selected.status.replace('_', ' ')}</h2></div>
            <span className={`job-status ${selected.status}`}>{selected.status}</span>
          </div>
          <div className="scope-card">
            <span>Repo: {selected.repository}</span>
            <span>Base: <code>{selected.startingRevision}</code></span>
            <span>Branch: {selected.branch || 'pending'}</span>
          </div>
          <div className="job-actions">
            <button onClick={() => action('cancel')} disabled={!['queued', 'running'].includes(selected.status)}>Cancel worker</button>
            <button onClick={() => action('continue')} disabled={!['completed', 'failed', 'blocked', 'awaiting_approval'].includes(selected.status) || !instruction}>Continue</button>
          </div>
          {selected.error && <p className="error">{selected.error}</p>}
          <h3>Progress</h3><pre>{selected.logs.join('\n') || 'Waiting for real worker events…'}</pre>
          <h3>Changed files</h3><ul>{selected.changedFiles.map((file) => <li key={file}>{file}</li>)}</ul>
          <h3>Diff</h3><pre>{selected.diff || 'No recorded diff.'}</pre>
          <h3>Tests</h3><pre>{selected.testResults.length ? JSON.stringify(selected.testResults, null, 2) : 'No test result reported.'}</pre>
          {selected.commit && <p>Commit: <code>{selected.commit}</code></p>}
          {selected.pullRequest && <p>Pull request: <a href={selected.pullRequest} target="_blank" rel="noreferrer">{selected.pullRequest}</a></p>}
        </> : <div className="empty-state"><h2>Select a job</h2><p>Progress, sanitized logs, actual changes, tests, commit, and pull-request references appear here.</p></div>}
      </section>
    </section>
  </main>;
}
