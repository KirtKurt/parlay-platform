"""Version-bound date evidence issued only after the retained-history loader.

Run-relative download flags cannot certify a historical window. This scope
keeps the loader's physical and PA date proofs separate and binds them to its
official-history version and retained source receipt inventory.
"""
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import re


def binding(receipt):
    if not isinstance(receipt, dict):
        return None
    values = tuple(receipt.get(key) for key in ('bucket', 'key', 'versionId', 'sha256'))
    if (any(not isinstance(value, str) or not value for value in values)
            or values[2] == 'null' or not re.fullmatch('[0-9a-f]{64}', values[3])):
        return None
    return values


@dataclass(frozen=True)
class HistoricalPitchScope:
    official: tuple
    receipts: frozenset
    physical: frozenset
    outcomes: frozenset

    def covers(self, target, window, *, outcomes=False):
        dates = self.outcomes if outcomes else self.physical
        return all((target-timedelta(days=age)).isoformat() in dates
                   for age in range(1, window+1))

    def report(self):
        encoded = json.dumps(sorted(self.receipts), separators=(',', ':')).encode()
        return {'method': 'version_bound_historical_pitch_windows_v1',
                'official_receipt': dict(zip(('bucket', 'key', 'versionId', 'sha256'), self.official)),
                'receipt_inventory_sha256': hashlib.sha256(encoded).hexdigest(),
                'physical_dates': len(self.physical), 'outcome_dates': len(self.outcomes)}


def issue_scope(bundle, physical, outcomes, receipts):
    """Called after every admitted day has passed retained-source validation."""
    official = binding(bundle.get('official_history_source'))
    inventory = {binding(value) for value in bundle.get('source_receipts', [])}
    sources = frozenset(binding(value) for value in receipts)
    physical, outcomes = frozenset(physical), frozenset(outcomes)
    years = set(bundle.get('official_history_source', {}).get('complete_years', []))
    if (official is None or official not in inventory or None in sources
            or not sources <= inventory or not outcomes <= physical
            or any(date.fromisoformat(value).year not in years for value in physical)):
        return None
    return HistoricalPitchScope(official, sources | {official}, physical, outcomes)


def matching_scope(bundle):
    scope = bundle.get('historical_pitch_scope')
    if not isinstance(scope, HistoricalPitchScope):
        return None
    inventory = {binding(value) for value in bundle.get('source_receipts', [])}
    if (scope.official != binding(bundle.get('official_history_source'))
            or not scope.receipts <= inventory
            or not scope.physical <= set(bundle.get('statcast_physical_dates', []))
            or not scope.outcomes <= set(bundle.get('statcast_retained_dates', []))):
        return None
    return scope
