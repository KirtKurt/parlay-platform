const message = document.querySelector('#message');
const jobs = document.querySelector('#jobs');
async function api(path = '', body) {
  const response = await fetch(`/v1/engineering${path}`, { method: body ? 'POST' : 'GET', headers: { 'content-type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
  if (response.status === 401) { location.assign('/auth/login'); throw new Error('Sign in required'); }
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Request failed'); return result;
}
async function refresh() {
  try {
    const result = await api(); jobs.replaceChildren();
    for (const job of result.jobs) {
      const item = document.createElement('article'); const title = document.createElement('h3'); title.textContent = job.instruction; item.append(title);
      const status = document.createElement('p'); status.textContent = `${job.status} · ${job.publicationState || 'execution'}${job.error ? ` · ${job.error}` : ''}`; item.append(status);
      if (job.pullRequest && /^https:\/\/github\.com\/KirtKurt\/parlay-platform\/pull\/\d+$/.test(job.pullRequest)) { const link = document.createElement('a'); link.href = job.pullRequest; link.textContent = 'View pull request'; item.append(link); }
      if (['queued', 'running', 'awaiting_publication', 'published'].includes(job.status)) { const cancel = document.createElement('button'); cancel.textContent = job.cancelRequested ? 'Cancellation pending' : 'Cancel'; cancel.disabled = Boolean(job.cancelRequested); cancel.onclick = async () => { try { await api(`/${job.id}/cancel`, {}); await refresh(); } catch (e) { message.textContent = e.message; } }; item.append(cancel); }
      const details = document.createElement('details'); const summary = document.createElement('summary'); summary.textContent = 'Execution details'; const log = document.createElement('pre'); log.textContent = (job.logs || []).join('\n'); details.append(summary, log); item.append(details); jobs.append(item);
    }
  } catch (error) { message.textContent = error.message; }
}
document.querySelector('#task').onsubmit = async event => { event.preventDefault(); const button = document.querySelector('#submit'); button.disabled = true; try { await api('', { instruction: document.querySelector('#instruction').value, authorizedScope: ['engineering_console_publication_proof'], startingRevision: 'HEAD' }); message.textContent = 'Task queued'; await refresh(); } catch (error) { message.textContent = error.message; } finally { button.disabled = false; } };
refresh(); setInterval(refresh, 5000);
