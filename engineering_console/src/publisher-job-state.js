import { JobStore } from './store.js';

export function updateJobFromPublisher(dataDir, receipt) {
  const store = new JobStore(dataDir);
  const job = store.get(receipt.jobId);
  if (!job) return;
  if (receipt.pullRequest) job.pullRequest = receipt.pullRequest;
  if (receipt.commit) job.commit = receipt.commit;
  if (receipt.mergeCommit) job.mergeCommit = receipt.mergeCommit;
  job.publicationState = receipt.state;
  if (receipt.state === 'merged') {
    job.status = 'completed';
    job.error = null;
  } else if (receipt.state === 'checks_failed' || receipt.state === 'publisher_failed') {
    job.status = 'failed';
    job.error = receipt.reason || receipt.state;
  } else if (receipt.pullRequest) {
    job.status = 'published';
  }
  store.save(job);
}
