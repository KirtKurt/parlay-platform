"""Read-only, per-game admission accounting. Never repairs evidence in place."""
from collections import Counter
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hello_world'))
import mlb_successor_model_v2 as model
import mlb_fundamentals_snapshot_v2 as snapshots
from mlb_historical_development_data import is_final, slate_complete


def audit_rows(rows):
    details, counts, identities = [], Counter(), set()
    before = model.fingerprint(rows)
    for row in rows:
        identity = (str(row.get('slateDateEt') or ''), str(row.get('officialGamePk') or row.get('gameId') or ''))
        vector = row.get('featureSnapshot') or row.get('frozenFeatureVector') or {}
        snap = row.get('fundamentalsSnapshotV2') or vector.get('fundamentalsSnapshotV2') or {}
        try:
            errors = snapshots.validate(snap)
        except (ValueError, TypeError, KeyError) as exc:
            errors = ['snapshot_validation_failed:' + type(exc).__name__]
        reason, admitted = None, False
        try:
            if identity in identities:
                raise ValueError('duplicate game identity')
            if row.get('slateFinalized') is False:
                raise ValueError('waiting for complete slate settlement')
            if row.get('trainingEligible') is False:
                raise ValueError('canonical training admission rejected: ' + ','.join(row.get('trainingExclusionReasons') or []))
            model.record(row, labeled=True)
            admitted = True
        except (ValueError, TypeError, KeyError) as exc:
            reason = str(exc)
        identities.add(identity)
        historical = row.get('historicalTrainingOnly') is True or snap.get('historicalMissingnessOnly') is True
        classification = ('ADMITTED_ORIGINAL_OBSERVATION' if admitted else
                          'WAITING_FOR_COMPLETE_SLATE' if reason == 'waiting for complete slate settlement' else
                          'HISTORICAL_MISSINGNESS_SEPARATE_DATASET' if historical else
                          'MISSING_ORIGINAL_SNAPSHOT' if not snap else 'ORIGINAL_EVIDENCE_REQUIRES_REVIEW')
        counts[classification] += 1
        details.append({'slateDateEt': identity[0], 'officialGamePk': identity[1],
                        'admitted': admitted, 'reason': reason, 'classification': classification,
                        'snapshotErrors': errors, 'snapshotFingerprint': snap.get('fingerprint')})
    assert before == model.fingerprint(rows), 'admission audit mutated source'
    return {'inspectedRows': len(rows), 'admittedRows': sum(d['admitted'] for d in details),
            'classificationCounts': dict(counts),
            'rejectionReasons': dict(Counter(d['reason'] for d in details if d['reason'])),
            'rows': details, 'sourceUnchanged': True}


def daily_audit(day, game_rows, locks, rejected_locks, labels, schedule_games, canonical):
    by_pk = {str(r.get('official_game_pk')): r for r in labels}
    final_ids = {str(g['gamePk']) for g in schedule_games if is_final(g)}
    eligible, details = [], []
    for locked in locks:
        pk = str(locked.get('officialGamePk') or '')
        label = by_pk.get(pk)
        if label and pk in final_ids:
            joined = canonical._joined_training_row(day, label, copy.deepcopy(locked), slate_finalized=slate_complete(schedule_games))
            eligible.append(joined)
        else:
            details.append({'officialGamePk': pk, 'state': 'FINAL_LABEL_MISSING' if pk in final_ids else 'WAITING_FOR_SETTLEMENT'})
    admission = audit_rows(eligible)
    coverage = Counter()
    for item in game_rows:
        row = item.get('data') or item
        snap = row.get('fundamentalsSnapshotV2') or {}
        if snapshots.validate(snap):
            continue
        groups = snap.get('groups') or {}
        starter = groups.get('starter_quality') or {}
        lineup = groups.get('confirmed_lineups') or {}
        bullpen = groups.get('bullpen_availability') or {}
        if starter.get('dataset') in (model.starter_source.VERSION, model.starter_source.DATASET):
            values = starter.get('values') or {}
            if all(model.number(values.get(k)) is not None for k in ('homeEra','awayEra','homeKMinusBbPct','awayKMinusBbPct')):
                coverage['bothStarterRates'] += 1
        if lineup.get('dataset') == model.team_source.VERSION:
            values = lineup.get('values') or {}
            if all(values.get(s+'Confirmed') is True for s in ('home','away')):
                coverage['bothConfirmedLineups'] += 1
            if all(model.number(values.get(s+'MeanSeasonOps')) is not None for s in ('home','away')):
                coverage['bothLineupOps'] += 1
        if bullpen.get('dataset') == model.team_source.VERSION:
            values = bullpen.get('values') or {}
            if all(isinstance(values.get(s+'Usage1d3d5d'),dict) and model.number(values[s+'Usage1d3d5d'].get('3d',{}).get('pitches')) is not None for s in ('home','away')):
                coverage['bothBullpenWorkloads'] += 1
    collected_ids = {str((item.get('data') or item).get('officialGamePk') or '') for item in game_rows}
    locked_ids = {str(row.get('officialGamePk') or '') for row in locks}
    gaps = [{'officialGamePk':str(g['gamePk']),
             'collectionPresent':str(g['gamePk']) in collected_ids,
             'validLockPresent':str(g['gamePk']) in locked_ids,
             'officialState':g.get('status',{}).get('abstractGameState')}
            for g in schedule_games if str(g['gamePk']) not in collected_ids or str(g['gamePk']) not in locked_ids]
    return {'slateDateEt': day, 'scheduledGames': len(schedule_games),
            'collectedGames': len(game_rows), 'validLocks': len(locks),
            'rejectedLocks': rejected_locks, 'officialFinalGames': len(final_ids),
            'storedFinalLabels': len(labels), 'admittedSettledGames': admission['admittedRows'],
            'sourceCoverage': dict(coverage), 'waitingOrMissingLabels': details,
            'admission': admission, 'collectionOrLockGaps': gaps, 'readOnly': True}
