const TERMINAL_PUBLICATION = new Set(['cancelled', 'merged', 'merge_conflict']);
const RECONCILE_WRITE = new Set(['ESTALE', 'EWRITEUNKNOWN']);

// Called only after the owner-authorized route has accepted the durable
// cancellation decision. That decision is not evidence of a remote PR close.
export function markPublicationCancellationPending(store, id, owner) {
  let lastError;
  for (let attempt = 0; attempt < 3; attempt++) {
    const pending = store.owned(id, owner);
    if (!pending) throw Object.assign(new Error('job_not_found'), { status: 404 });
    // The trusted publisher may have finished between queue.cancel(), this
    // refresh, or a previous conflicting/unknown write. Never downgrade it.
    if (TERMINAL_PUBLICATION.has(pending.publicationState)) return pending;
    if (pending.publicationState === 'cancellation_pending' && pending.cancelRequested) return pending;
    pending.status = pending.pullRequest ? 'published' : 'awaiting_publication';
    pending.publicationState = 'cancellation_pending';
    pending.error = 'publication_cancellation_pending_confirmation';
    pending.cancelRequested = true;
    try {
      store.save(pending);
      return pending;
    } catch (error) {
      if (!RECONCILE_WRITE.has(error?.code)) throw error;
      lastError = error;
    }
  }
  // One final read can confirm the third write without another mutation.
  const latest = store.owned(id, owner);
  if (latest && (TERMINAL_PUBLICATION.has(latest.publicationState) ||
      (latest.publicationState === 'cancellation_pending' && latest.cancelRequested))) return latest;
  // No confirmed result: retain the uncertain outcome instead of claiming
  // either terminal cancellation or a successful pending-state write.
  throw lastError;
}
