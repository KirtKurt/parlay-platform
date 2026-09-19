"""Read-only reconciliation regressions; storage validation stays in the proof suite."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'passive_retry_proof', ROOT / 'scripts/verify_mlb_team_context.py')
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


def fixture():
    samples = [{'playerId': i, 'battingSlot': i, 'plateAppearances': 100,
                'ops': .8, 'obp': .3, 'slg': .5, 'rateObservationCount': 3,
                'sampleStatus': 'OBSERVED'} for i in range(1, 10)]
    live = {'officialGamePk': 823980, 'gameDate': '2026-09-16T01:38:00Z',
            'lineup': {'home_batting_order': list(range(1, 10)),
                       'home_lineup_season_batting': samples},
            'bullpen': {'home_bullpen_roster_player_ids': [10, 11]}}
    evidence = {'rows': [{
        'officialGamePk': '823980', 'commenceTime': '2026-09-16T01:38:00+00:00',
        'storedPK': 'GAME_WINNERS#mlb#2026-09-15', 'storedSK': 'GAME#fixture',
        'passiveLineupObservation': {
            'valid': True, 'identitySets': {'home': list(range(1, 10))},
            'batterSamples': {'home': SUBJECT._normalized_batter_samples(samples)}},
        'passiveBullpenRosterObservation': {
            'valid': True, 'identitySets': {'home': [10, 11]}}}]}
    return {'rows': [live]}, evidence


def install_reads(monkeypatch, evidence_sequence):
    clock = Clock()
    table = object()
    calls = []

    def read(actual_table, day):
        assert actual_table is table and day == '2026-09-15'
        value = evidence_sequence[min(len(calls), len(evidence_sequence) - 1)]
        calls.append(day)
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    monkeypatch.setattr(SUBJECT, 'persisted_observations', read)
    monkeypatch.setattr(SUBJECT.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(SUBJECT.time, 'sleep', clock.sleep)
    # The retry path is not allowed to change its expected cohort by re-fetching.
    def forbidden(*args, **kwargs):
        raise AssertionError('source collection must not repeat')
    monkeypatch.setattr(SUBJECT.source, 'observe', forbidden)
    return table, calls, clock


def test_first_exact_read_succeeds_without_wait(monkeypatch):
    report, evidence = fixture()
    table, calls, clock = install_reads(monkeypatch, [evidence])
    SUBJECT.read_persistence_with_retry(report, table, '2026-09-15', 300)
    assert report['persistenceCorrelation']['status'] == 'PROVEN'
    assert report['persistenceReadback']['stopReason'] == 'PROVEN'
    assert len(calls) == 1 and not clock.sleeps


def test_collector_catches_up_to_same_source_sample(monkeypatch):
    report, evidence = fixture()
    original_rows = copy.deepcopy(report['rows'])
    old = copy.deepcopy(evidence)
    old['rows'][0]['passiveLineupObservation']['identitySets']['home'] = []
    table, calls, clock = install_reads(monkeypatch, [old, evidence])
    SUBJECT.read_persistence_with_retry(report, table, '2026-09-15', 30)
    assert report['rows'] == original_rows
    assert report['persistenceCorrelation']['status'] == 'PROVEN'
    progress = report['persistenceReadback']
    assert progress['readOnly'] and progress['fixedSourceObservations']
    assert [attempt['status'] for attempt in progress['attempts']] == ['INCONCLUSIVE', 'PROVEN']
    assert progress['attempts'][0]['errors']
    assert len(calls) == 2 and clock.sleeps == [15]


def test_default_wait_keeps_existing_fail_closed_behavior(monkeypatch):
    report, _ = fixture()
    table, calls, clock = install_reads(monkeypatch, [{'rows': []}])
    SUBJECT.read_persistence_with_retry(report, table, '2026-09-15')
    assert report['persistenceCorrelation']['status'] == 'INCONCLUSIVE'
    assert report['persistenceReadback']['stopReason'] == 'WAIT_EXHAUSTED'
    assert len(calls) == 1 and not clock.sleeps


def test_exhausted_wait_never_credits_missing_data(monkeypatch):
    report, _ = fixture()
    table, calls, clock = install_reads(monkeypatch, [{'rows': []}])
    SUBJECT.read_persistence_with_retry(report, table, '2026-09-15', 17)
    assert clock.sleeps == [15, 2] and len(calls) == 3
    assert report['persistenceReadback']['elapsedSeconds'] == 17
    assert report['persistenceReadback']['stopReason'] == 'WAIT_EXHAUSTED'
    assert report['persistenceCorrelation']['status'] == 'INCONCLUSIVE'
    assert report['persistenceCorrelation']['matchedBlocks'] == 0


def test_separate_read_successes_are_not_stitched_into_proof(monkeypatch):
    report, evidence = fixture()
    first, second = copy.deepcopy(evidence), copy.deepcopy(evidence)
    first['rows'][0]['passiveBullpenRosterObservation']['identitySets']['home'] = [12]
    second['rows'][0]['passiveLineupObservation']['identitySets']['home'] = []
    table, _, _ = install_reads(monkeypatch, [first, second])
    SUBJECT.read_persistence_with_retry(report, table, '2026-09-15', 15)
    assert [a['matchedBlocks'] for a in report['persistenceReadback']['attempts']] == [1, 1]
    assert report['persistenceCorrelation']['status'] == 'INCONCLUSIVE'
    assert report['persistenceCorrelation']['matchedBlocks'] == 1


@pytest.mark.parametrize('failure', ['no_observations', 'wrong_start', 'duplicate', 'bad_identity'])
def test_structural_failures_do_not_retry(monkeypatch, failure):
    report, evidence = fixture()
    if failure == 'no_observations':
        report['rows'] = []
    elif failure == 'wrong_start':
        evidence['rows'][0]['commenceTime'] = '2026-09-16T02:38:00Z'
    elif failure == 'duplicate':
        evidence['rows'].append(copy.deepcopy(evidence['rows'][0]))
    else:
        evidence['rows'][0]['officialGamePk'] = 'invalid'
    table, calls, clock = install_reads(monkeypatch, [evidence])
    SUBJECT.read_persistence_with_retry(report, table, '2026-09-15', 300)
    assert report['persistenceCorrelation']['status'] == 'INCONCLUSIVE'
    assert report['persistenceReadback']['stopReason'] == 'NON_RETRYABLE_CORRELATION'
    assert len(calls) == 1 and not clock.sleeps


@pytest.mark.parametrize('error', [
    RuntimeError('invalid persisted passive batter observation: not_pre_t45'),
    ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': 'denied'}}, 'Query'),
    TimeoutError('read failed'),
])
def test_validation_and_permissions_fail_immediately(monkeypatch, error):
    report, _ = fixture()
    table, calls, clock = install_reads(monkeypatch, [error])
    with pytest.raises(type(error)):
        SUBJECT.read_persistence_with_retry(report, table, '2026-09-15', 300)
    assert report['persistenceReadback']['stopReason'] == 'READ_OR_VALIDATION_FAILED'
    assert report['persistenceReadback']['attempts'][0]['status'] == 'INVALID'
    assert len(calls) == 1 and not clock.sleeps


@pytest.mark.parametrize('wait', [-1, 301, 1.5, True])
def test_wait_budget_is_bounded_before_any_read(monkeypatch, wait):
    report, evidence = fixture()
    table, calls, clock = install_reads(monkeypatch, [evidence])
    with pytest.raises(ValueError):
        SUBJECT.read_persistence_with_retry(report, table, '2026-09-15', wait)
    assert not calls and not clock.sleeps


def test_workflow_retains_read_only_main_scope_and_bounded_wait():
    workflow = (ROOT / '.github/workflows/mlb-passive-context-persistence-proof.yml').read_text()
    assert 'contents: read' in workflow and 'contents: write' not in workflow
    assert "with: {ref: '${{ github.sha }}'}" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert 'PROOF_SOURCE_SHA: ${{ github.sha }}' in workflow
    assert '--persisted --persistence-wait-seconds 300' in workflow
    assert 'timeout-minutes: 10' in workflow
    assert 'test_mlb_team_context_persistence_retry.py' in workflow
