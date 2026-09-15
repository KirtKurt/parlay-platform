"""Read-only alignment of live pitch windows with verified training evidence."""
from datetime import date, timedelta

from ks1.features import utc
from ks1.statcast_history import load_training_statcast


HISTORY_KEYS = ('statcast', 'statcast_retained_dates', 'statcast_verified_games',
                'statcast_physical_dates', 'statcast_physical_games')


def restore_recent_history(history, prior, prior_receipt, s3, bucket, target_date):
    """Enrich the captured history in place, retaining every exact source receipt.

    Only the 30 completed calendar dates before the requested slate are examined.
    The shared loader independently verifies schedule, physical/PA counts, raw
    and official source versions, and all reconciliation derivations. It never
    calls a provider or writes to AWS. Existing global completeness flags remain
    authoritative, and records outside this bounded window retain their proof.
    """
    target = date.fromisoformat(target_date)
    prior_year = prior.get('priorYear')
    years = []
    if isinstance(prior_year, int) and not isinstance(prior_year, bool):
        years = [year for year, complete in (
            (prior_year, prior.get('priorYearCoverageComplete')),
            (prior_year + 1, prior.get('currentYearCoverageComplete')))
                 if complete is True]
    if (not years or not prior.get('games') or not prior.get('schedule')
            or prior_receipt.get('versionId') in (None, '', 'null')
            or not prior_receipt.get('sha256')):
        # Do not turn incomplete official history into permission to derive
        # values, or interrupt an otherwise valid compact-source capture.
        return {'provider_requests': 0, 'source_writes': 0,
                'status': 'complete_versioned_official_history_unavailable'}
    requested = [(target - timedelta(days=age)).isoformat() for age in range(1, 31)]
    bundle = {**history, 'full': prior['games'], 'schedule': prior['schedule'],
              'official_history_source': {**prior_receipt, 'complete_years': years}}
    # source_receipts is intentionally the capture Reader's existing list:
    # both the compressed history and the capture manifest retain the chain.
    receipt_count = len(history['source_receipts'])
    report = load_training_statcast(bundle, s3, bucket, requested_dates=requested)
    for key in HISTORY_KEYS:
        history[key] = bundle[key]
    added = history['source_receipts'][receipt_count:]
    if added:
        # Never backdate newly restored evidence to the compact artifact's old
        # observation time. The existing T-10 profile validator consumes this.
        times = [history.get('statcast_observed_at'), prior_receipt.get('stored_at')]
        times.extend(receipt.get('stored_at') for receipt in added)
        try:
            if any(value is None for value in times):
                raise ValueError('source observation time unavailable')
            history['statcast_observed_at'] = max(utc(value) for value in times).isoformat()
        except (TypeError, ValueError):
            history['statcast_observed_at'] = None
    return {**report, 'source_writes': 0, 'requested_dates': sorted(requested),
            'status': 'verified_retained_windows_loaded'}
