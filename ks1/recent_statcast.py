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
    # Stage receipts and coverage together; unprovable observation times must
    # leave the existing capture intact, including its shared receipt list.
    receipt_count = len(history['source_receipts'])
    bundle['source_receipts'] = list(history['source_receipts'])
    report = load_training_statcast(bundle, s3, bucket, requested_dates=requested)
    added = bundle['source_receipts'][receipt_count:]
    # Every requested date produces a new coverage decision. Empty verified
    # dates have no pitch receipt, but still depend on the official-history
    # receipt, so validate the complete time chain even when ``added`` is empty.
    times = [history.get('statcast_observed_at'), prior_receipt.get('stored_at')]
    times.extend(receipt.get('stored_at') for receipt in added)
    try:
        if any(value is None for value in times):
            raise ValueError('source observation time unavailable')
        observed_at = max(utc(value) for value in times).isoformat()
    except (TypeError, ValueError):
        return {'provider_requests': 0, 'source_writes': 0,
                'requested_dates': sorted(requested),
                'status': 'restored_observation_time_unavailable',
                'restored_evidence_applied': False}
    for key in HISTORY_KEYS:
        history[key] = bundle[key]
    history['source_receipts'].extend(added)
    history['statcast_observed_at'] = observed_at
    return {**report, 'source_writes': 0, 'requested_dates': sorted(requested),
            'status': 'verified_retained_windows_loaded',
            'restored_evidence_applied': True}
