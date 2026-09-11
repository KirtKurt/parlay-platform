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
  if (receipt.state === 'merge_conflict') {
    job.status = 'blocked';
    job.cancelRequested = true;
    job.error = receipt.reason;
  } else if (receipt.state === 'merged') {
    job.status = 'completed';
    job.error = null;
  } else if (receipt.state === 'cancelled' || receipt.state === 'cancellation_pending' || publicationDecision(dataDir, receipt.jobId) === 'cancelled') {
    job.status = 'cancelled';
    job.cancelRequested = true;
    job.error = null;
  } else if (receipt.state === 'checks_failed' || receipt.state === 'publisher_failed') {
    job.status = 'failed';
    job.error = receipt.reason || receipt.state;
  } else if (receipt.pullRequest) {
    job.status = 'published';
  }
  store.save(job);
}
