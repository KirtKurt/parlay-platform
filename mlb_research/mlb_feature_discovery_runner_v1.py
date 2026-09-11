"""Publish fail-isolated feature-discovery evidence from the canonical dataset."""
from mlb_research_store_v1 import digest, now
from mlb_research_provenance_v1 import implementation_manifest
import mlb_feature_discovery_v1 as discovery

VERSION = 'MLB-FEATURE-DISCOVERY-RUNNER-v1'
MAX_ROWS = 1200
HOLDOUT_FRACTION = .20


def development_slice(rows):
    ordered = sorted(rows, key=lambda r:(r['slateDateEt'], str(r['officialGamePk'])))
    while len(ordered) > MAX_ROWS:
        first = ordered[0]['slateDateEt']
        ordered = [r for r in ordered if r['slateDateEt'] != first]
    dates = sorted({r['slateDateEt'] for r in ordered})
    if len(dates) < 2:
        return ordered, [], dates
    split = max(1, min(len(dates)-1, int(len(dates)*(1-HOLDOUT_FRACTION))))
    development_dates = set(dates[:split])
    holdout_dates = set(dates[split:])
    return ([r for r in ordered if r['slateDateEt'] in development_dates],
            [r for r in ordered if r['slateDateEt'] in holdout_dates], dates)


def publish(store, dataset):
    """Screen development rows only; never expose holdout labels to the screen.

    Identical data under identical source bytes reuses the immutable report.
    Failures are persisted as research evidence and returned instead of
    raising, so canonical dataset publication cannot be blocked by discovery.
    """
    implementation = implementation_manifest()['sha256']
    existing = store.get('feature-discovery.json')
    if (existing and existing.get('datasetRowsHash') == dataset.get('rowsHash')
            and existing.get('implementationSha256') == implementation
            and existing.get('artifact')):
        prior = store.load(existing['artifact'])
        if (prior.get('datasetRowsHash') == dataset.get('rowsHash')
                and prior.get('implementationSha256') == implementation):
            return prior
    at = now().isoformat()
    try:
        rows = dataset['rows']
        development, holdout, dates = development_slice(rows)
        report = discovery.screen(development)
        value = {**report, 'runnerVersion': VERSION, 'implementationSha256': implementation,
                 'updatedAtUtc': at, 'datasetRowsHash': dataset['rowsHash'],
                 'sourceOriginalRows': dataset.get('originalRows', 0),
                 'developmentRows': len(development), 'holdoutRows': len(holdout),
                 'allCappedSlates': len(dates),
                 'developmentFirstDate': development[0]['slateDateEt'] if development else None,
                 'developmentLastDate': development[-1]['slateDateEt'] if development else None,
                 'holdoutFirstDate': holdout[0]['slateDateEt'] if holdout else None,
                 'holdoutLastDate': holdout[-1]['slateDateEt'] if holdout else None,
                 'holdoutLabelsInspectedByScreen': False,
                 'productionAuthorityChanged': False,
                 'researchOnly': True}
    except Exception as exc:
        value = {'runnerVersion': VERSION, 'implementationSha256': implementation,
                 'status': 'FAILED', 'updatedAtUtc': at,
                 'datasetRowsHash': dataset.get('rowsHash'), 'error': type(exc).__name__,
                 'holdoutLabelsInspectedByScreen': False,
                 'productionAuthorityChanged': False, 'researchOnly': True}
    artifact = store.artifact('feature-discovery', value)
    latest = {'artifact': artifact, 'updatedAtUtc': at, 'status': value['status'],
              'runnerVersion': VERSION, 'implementationSha256': implementation,
              'datasetRowsHash': value.get('datasetRowsHash'),
              'candidateCount': len(value.get('candidates', [])),
              'productionAuthorityChanged': False}
    store.latest('feature-discovery.json', latest)
    return value
