import { JobStore } from './store.js';
import { publicationDecision } from './publication-decision.js';

export function updateJobFromPublisher(dataDir, receipt) {
  const store = new JobStore(dataDir);
  const job = store.get(receipt.jobId);
  if (!job || job.publicationState === 'merged' || job.publicationState === 'merge_conflict') return;
  if (receipt.pullRequest) job.pullRequest = receipt.pullRequest;
  if (receipt.commit) job.commit = receipt.commit;
  if (receipt.mergeCommit) job.mergeCommit = receipt.mergeCommit;
  job.publicationState = receipt.state;
  if (receipt.observedGitHubState) job.observedGitHubState = receipt.observedGitHubState;
  if (receipt.completionMode) job.completionMode = receipt.completionMode;
  const cancellationAccepted = publicationDecision(dataDir, receipt.jobId) === 'cancelled';
  if (receipt.state === 'merge_conflict') {
    job.status = 'blocked';
    job.cancelRequested = true;
    job.error = receipt.reason;
  } else if (receipt.state === 'merged') {
    job.status = 'completed';
    job.error = null;
  } else if (receipt.state === 'cancelled' && ['closed', 'no_pull_request'].includes(receipt.observedGitHubState)) {
    job.status = 'cancelled';
    job.cancelRequested = true;
    job.error = null;
  } else if (receipt.state === 'cancellation_pending' || receipt.state === 'cancelled') {
    job.status = receipt.pullRequest || job.pullRequest ? 'published' : 'awaiting_publication';
    job.publicationState = 'cancellation_pending';
    job.cancelRequested = true;
    job.error = receipt.reason || 'publication_cancellation_pending_confirmation';
  } else if (receipt.state === 'checks_failed' || receipt.state === 'publisher_failed') {
    job.status = 'failed';
    if (cancellationAccepted) job.cancelRequested = true;
    job.error = receipt.reason || receipt.state;
  } else if (receipt.pullRequest) {
    job.status = 'published';
    if (cancellationAccepted) job.cancelRequested = true;
  }
  store.save(job);
}
