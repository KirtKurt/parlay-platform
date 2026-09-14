"""Rolling chronological holdout for seven-day KS1 features; never publish predictions."""
import argparse
from collections import Counter
import hashlib
import json
import os
import re
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

from ks1.features import Features, MATCHUP_METRICS, normalize
from ks1.historical_starters import V8_MANIFEST_PREFIX
from ks1.inventory import encode
from ks1.passive_context import (LINEUP_FEATURES, LINEUP_VALUE_FEATURES,
                                 LINEUP_PERFORMANCE_FEATURES,
                                 BULLPEN_FEATURES, BULLPEN_VALUE_FEATURES,
                                 BULLPEN_PERFORMANCE_FEATURES,
                                 MODEL_FEATURES as LINEUP_BULLPEN_FEATURES)
from ks1.sources import aws_clients, load_existing
from ks1.progress import record_progress
from ks1.statcast_history import load_training_statcast
from ks1.table import build, contract
from ks1.train import PARAMS, select_features, save_artifact
from ks1.prior_pitcher_context import (PriorPitcherContext, pregame_identity_index,
                                      verified_reconstruction)
from ks1.historical_feed import (collect as collect_historical_feeds,
                                 collect_team_context as collect_historical_team_context,
                                 feed_team_context)

# Feature-contract construction needs a valid timestamp but does not inspect data.
FEATURE_CONTRACT_DATE = '2026-09-01'
MIN_TRAIN = 500
EVALUATION_GAMES = 300
MIN_TEST = EVALUATION_GAMES
MIN_STARTER_ROWS = EVALUATION_GAMES


def completion_times(values):
    """Parse retained completion timestamps, accepting valid mixed ISO-8601 forms."""
    return pd.to_datetime(values, format='ISO8601', utc=True, errors='raise')


def split_recent(frame):
    if frame.game_id.duplicated().any():
        raise ValueError('duplicate game IDs')
    labeled = frame.loc[frame.home_win.notna() & frame.home_score.notna() & frame.away_score.notna()].copy()
    if not set(labeled.home_win.unique()).issubset({True, False, 0, 1}):
        raise ValueError('invalid labels')
    completed = completion_times(labeled.label_completed_at)
    predicted = completion_times(labeled.as_of_timestamp)
    valid = completed.notna() & predicted.notna() & (predicted < completed)
    eligible = labeled.loc[valid].assign(
        _label_completed_at=completed.loc[valid],
        _prediction_at=predicted.loc[valid],
    ).sort_values(['_label_completed_at', 'game_id'])
    if len(eligible) < MIN_TRAIN + MIN_TEST:
        raise ValueError('insufficient chronological train/test games')
    test = eligible.tail(EVALUATION_GAMES)
    boundary = test['_prediction_at'].min()
    # No training label may become available after the first held-out feature
    # vector was frozen, including overlapping games from the same slate.
    train = eligible.loc[eligible['_label_completed_at'] < boundary]
    if len(train) < MIN_TRAIN:
        raise ValueError('insufficient chronological train/test games')
    train = train.drop(columns=['_label_completed_at', '_prediction_at'])
    test = test.drop(columns=['_label_completed_at', '_prediction_at'])
    return train, test


def split_development(train):
    """Purge labels unavailable at the earliest development prediction."""
    count = min(EVALUATION_GAMES, max(100, len(train)//5), len(train)-MIN_TRAIN)
    if count <= 0:
        raise ValueError('insufficient games for recipe-selection development set')
    development = train.iloc[-count:]
    boundary = completion_times(development.as_of_timestamp).min()
    fit = train.iloc[:-count]
    fit = fit.loc[completion_times(fit.label_completed_at) < boundary]
    if len(fit) < MIN_TRAIN:
        raise ValueError('insufficient non-overlapping development training games')
    return fit, development


def individual_feature(column):
    return any(column.startswith(side+'_starter_') for side in ('home', 'away'))


def pitcher_context_feature(column):
    return any(column.startswith(side+'_pitcher_context_') for side in ('home', 'away'))


def lineup_feature(column):
    return any(column == side+'_'+name for side in ('home', 'away') for name in LINEUP_FEATURES)


def bullpen_context_feature(column):
    return any(column == side+'_'+name for side in ('home', 'away') for name in BULLPEN_FEATURES)


def lineup_value_feature(column):
    return any(column == side+'_'+name
               for side in ('home', 'away') for name in LINEUP_VALUE_FEATURES)


def bullpen_context_value_feature(column):
    return any(column == side+'_'+name
               for side in ('home', 'away') for name in BULLPEN_VALUE_FEATURES)


def lineup_performance_feature(column):
    return any(column == side+'_'+name
               for side in ('home', 'away') for name in LINEUP_PERFORMANCE_FEATURES)


def matchup_performance_feature(column):
    return column in {side+'_lineup_'+metric+'_'+window
                      for side in ('home', 'away')
                      for metric in ('platoon_xwoba', 'pitch_type_matchup_xwoba')
                      for window in ('7d', '30d')}


def bullpen_context_performance_feature(column):
    return any(column == side+'_'+name
               for side in ('home', 'away') for name in BULLPEN_PERFORMANCE_FEATURES)


def choose_features(train):
    _, dictionary = contract(train.iloc[0].to_dict())
    features, omitted = select_features(train, dictionary)
    # Identity must be recorded before the game's cutoff; actual-starter labels
    # are never substituted. Require genuine prior pitcher history, not a prior
    # computed for an ID with zero recorded appearances.
    coverage = {side: int((train[side+'_starter_id'].notna() &
                           (pd.to_numeric(train[side+'_starter_bf_30d'], errors='coerce') > 0)).sum())
                for side in ('home', 'away')}
    if min(coverage.values()) < MIN_STARTER_ROWS:
        rejected = [c for c in features if individual_feature(c)]
        features = [c for c in features if c not in rejected]
        omitted = sorted(set(omitted) | set(rejected))
    # An observed starter ID does not prove that a particular box/Statcast
    # metric is covered. Do not let LightGBM learn an advanced field from a
    # handful of non-null rows while silently treating the rest as missing.
    sparse = [c for c in features if individual_feature(c)
              and int(train[c].notna().sum()) < MIN_STARTER_ROWS]
    features = [c for c in features if c not in sparse]
    omitted = sorted(set(omitted) | set(sparse))
    # Historical point-in-time summaries can accelerate shadow learning without
    # claiming confirmed identity.  Each learned field still needs the same
    # 300-row floor on both sides.
    context_sparse = [c for c in features if pitcher_context_feature(c)
                      and int(train[c].notna().sum()) < MIN_STARTER_ROWS]
    features = [c for c in features if c not in context_sparse]
    omitted = sorted(set(omitted) | set(context_sparse))
    passive_sparse = [c for c in features if (lineup_feature(c) or bullpen_context_feature(c))
                      and int(train[c].notna().sum()) < EVALUATION_GAMES]
    features = [c for c in features if c not in passive_sparse]
    omitted = sorted(set(omitted) | set(passive_sparse))
    supported = {side+'_'+key for side in ('home', 'away')
                 for key in Features([]).at(FEATURE_CONTRACT_DATE+'T04:00:00Z', '0')}
    supported.update(side+'_starter_'+metric for side in ('home', 'away') for metric in MATCHUP_METRICS)
    supported.update(('market_home_prob', 'market_total', 'market_spread'))
    supported.update(side+'_'+name for side in ('home', 'away')
                     for name in LINEUP_BULLPEN_FEATURES)
    if set(features) - supported:
        raise ValueError('training features missing from daily inference: '+','.join(sorted(set(features)-supported)))
    if not any(c.endswith('_7d') for c in features):
        raise ValueError('seven-day features unavailable in training')
    return features, omitted, coverage


def metrics(y, p):
    p = np.asarray(p)
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError('invalid probability')
    return {'games': len(y), 'accuracy': float(accuracy_score(y, p >= .5)),
            'brier': float(brier_score_loss(y, p)),
            'logloss': float(log_loss(y, p, labels=[0, 1]))}


def accepted(candidate, incumbent):
    return (candidate['games'] == EVALUATION_GAMES and candidate['games'] == incumbent['games']
            and candidate['brier'] < incumbent['brier']
            and candidate['logloss'] <= incumbent['logloss'])


def improves(candidate, incumbent):
    return (candidate['games'] == incumbent['games']
            and candidate['brier'] < incumbent['brier']
            and candidate['logloss'] <= incumbent['logloss'])


def pitcher_promotion_ready(candidate, incumbent, context_features, qualified_rows):
    return bool(accepted(candidate, incumbent) and context_features
                and qualified_rows == EVALUATION_GAMES)


def historical_context_mask(frame):
    """Admit the verified historical manifest without calling it prospective.

    The table builder verifies the versioned manifest bytes, snapshot hashes,
    identity, and prior-only construction. Retain its receipt and timing checks
    here as an independent qualification boundary, never infer from non-null
    pitcher values alone.
    """
    def verified(row):
        if row.get('historical_pitcher_context_mode') not in (
                'confirmed_archive', 'strict_prior_projection'):
            return False
        # table.build applies manifest values only to a side with no observed
        # starter ID. A row-level receipt cannot prove an untouched other side.
        # Until side-specific receipts exist, qualify historical rows only when
        # both sides were populated from that manifest.
        if any(side+'_starter_id' not in row or not pd.isna(row[side+'_starter_id'])
               for side in ('home', 'away')):
            return False
        try:
            receipt = json.loads(row['historical_pitcher_context_source'])
            observed = pd.Timestamp(row['historical_pitcher_context_as_of'])
            cutoff = pd.Timestamp(row['as_of_timestamp'])
            start = pd.Timestamp(row['commence_time'])
            return bool(isinstance(receipt, dict)
                        and receipt.get('source_type') == 'v8_point_in_time_pitcher_context'
                        and receipt.get('bucket')
                        and str(receipt.get('key') or '').startswith(V8_MANIFEST_PREFIX)
                        and receipt.get('version_id') not in (None, '', 'null')
                        and re.fullmatch('[0-9a-f]{64}', str(receipt.get('sha256') or ''))
                        and all(t.tzinfo is not None and not pd.isna(t)
                                for t in (observed, cutoff, start))
                        and observed <= cutoff <= start-pd.Timedelta(minutes=10))
        except (KeyError, TypeError, ValueError):
            return False
    return frame.apply(verified, axis=1).astype(bool)


def qualified_context_coverage(frame, context_features, *, reconstruction=None):
    reconstructed = frame.apply(lambda row: verified_reconstruction(row, reconstruction), axis=1).astype(bool)
    historical = historical_context_mask(frame) | reconstructed
    prospective = (frame.pitcher_context_evidence.eq('frozen_versioned_ks1_profile')
                   & frame.historical_pitcher_context_mode.isna())
    values = frame[context_features].apply(pd.to_numeric, errors='coerce')
    finite = values.notna() & np.isfinite(values)
    complete = finite.all(axis=1) if context_features else pd.Series(False, index=frame.index)
    qualified = historical | prospective
    return {
        'historical_rows': int((historical & complete).sum()),
        'reconstructed_official_rows': int((reconstructed & complete).sum()),
        'prospective_rows': int((prospective & complete).sum()),
        'qualified_rows': int((qualified & complete).sum()),
        'per_feature': {c: int((qualified & finite[c]).sum()) for c in context_features},
    }


def qualification_basis(coverage):
    if coverage['qualified_rows'] != EVALUATION_GAMES:
        return 'insufficient_point_in_time_coverage'
    if coverage['historical_rows'] == EVALUATION_GAMES:
        return 'historical_point_in_time_300'
    if coverage['prospective_rows'] == EVALUATION_GAMES:
        return 'frozen_pregame_profiles_300'
    return 'mixed_historical_and_frozen_pregame_300'


def context_basis_counts(frame):
    counts = {side: Counter() for side in ('home', 'away')}
    for value in frame.reconstructed_pitcher_context_proof:
        try:
            proofs = json.loads(value)
        except (TypeError, ValueError):
            proofs = {}
        for side in counts:
            counts[side][proofs.get(side, {}).get('statistical_basis', 'unavailable')] += 1
    return {side: dict(values) for side, values in counts.items()}


def prospective_context_coverage(frame, context_features):
    prospective = frame.pitcher_context_evidence.eq(
        'frozen_versioned_ks1_profile') & frame.historical_pitcher_context_mode.isna()
    per_feature = {column: int((prospective & frame[column].notna()).sum())
                   for column in context_features}
    complete = frame[context_features].notna().all(axis=1) if context_features else False
    return per_feature, int((prospective & complete).sum())


def prospective_team_context_coverage(frame, features):
    prospective = frame.lineup_bullpen_context_evidence.eq('frozen_versioned_ks1_profile')
    per_feature = {column: int((prospective & frame[column].notna()).sum()) for column in features}
    complete = frame[features].notna().all(axis=1) if features else pd.Series(False, index=frame.index)
    return per_feature, int((prospective & complete).sum())


def historical_team_context_mask(frame, source_receipts=None):
    """Verify the row-bound receipt for MLB's timecoded pregame team state."""
    inventory = None
    if source_receipts is not None:
        inventory = {(str(item.get('bucket')), str(item.get('key')),
                      str(item.get('versionId') or item.get('version_id')),
                      str(item.get('sha256')))
                     for item in source_receipts if isinstance(item, dict)}

    def verified(row):
        if (row.get('lineup_bullpen_context_evidence') != 'historical_timecoded_mlb_feed'
                or row.get('historical_lineup_bullpen_context_status') not in (
                    'SUPPORTED_V1_COMPLETE', 'SUPPORTED_V1_EXPLICIT_MISSING')):
            return False
        try:
            receipt = json.loads(row['historical_lineup_bullpen_context_source'])
            observed = pd.Timestamp(row['historical_lineup_bullpen_context_as_of'])
            start = pd.Timestamp(row['commence_time'])
            cutoff = pd.Timestamp(row['as_of_timestamp'])
            binding = (str(receipt.get('bucket')), str(receipt.get('key')),
                       str(receipt.get('versionId') or receipt.get('version_id')),
                       str(receipt.get('sha256')))
            return bool(receipt.get('source_type') == 'mlb_statsapi_timecoded_team_context'
                        and receipt.get('provider') == 'MLB Stats API'
                        and receipt.get('bucket') and receipt.get('versionId') not in (None, '', 'null')
                        and str(receipt.get('key') or '').startswith(
                            'mlb/development-data/ks1-historical-team-context-v1/')
                        and re.fullmatch('[0-9a-f]{64}', str(receipt.get('sha256') or ''))
                        and re.fullmatch('[0-9a-f]{64}', str(receipt.get('payload_sha256') or ''))
                        and all(t.tzinfo is not None and not pd.isna(t)
                                for t in (observed, start, cutoff))
                        and observed <= cutoff <= start-pd.Timedelta(minutes=10)
                        and (inventory is None or binding in inventory))
        except (KeyError, TypeError, ValueError):
            return False
    return frame.apply(verified, axis=1).astype(bool)


def team_context_masks(frame, source_receipts=None):
    historical = historical_team_context_mask(frame, source_receipts)
    frozen = (frame.lineup_bullpen_context_evidence.eq('frozen_versioned_ks1_profile')
              & frame.get('lineup_bullpen_history_status',
                          pd.Series(None, index=frame.index, dtype=object)).eq('COMPLETE'))
    return historical, frozen


def qualified_training_population(train, source_receipts):
    """Exclude unproven training sources before fitting any candidate recipe.

    The final holdout is split first and is never filtered. This predicate does
    not inspect outcomes, feature values, or model predictions. Explicitly
    missing measurements with verified pregame evidence remain eligible.
    """
    historical, frozen = team_context_masks(train, source_receipts)
    keep = historical | frozen
    selected = train.loc[keep].copy()
    if len(selected) < MIN_TRAIN:
        raise ValueError('insufficient training games with verified team context')
    return selected, {
        'rule': 'verified_timecoded_or_complete_frozen_team_context',
        'input_games': len(train), 'retained_games': len(selected),
        'excluded_game_ids': train.loc[~keep, 'game_id'].astype(str).tolist(),
        'outcomes_used_for_filtering': False,
        'final_holdout_filtered': False,
        'same_population_for_all_recipes': True,
    }


def qualified_team_context_coverage(frame, features, *, source_receipts=None):
    """Count complete point-in-time evidence, allowing explicit missing values."""
    historical, frozen = team_context_masks(frame, source_receipts)
    qualified = historical | frozen
    complete = pd.Series(True, index=frame.index)
    per_feature = {}
    for column in features:
        values = pd.to_numeric(frame[column], errors='coerce')
        if column.endswith('_missing'):
            base = column.removesuffix('_missing')
            base_values = (pd.to_numeric(frame[base], errors='coerce')
                           if base in frame else pd.Series(np.nan, index=frame.index))
            base_finite = base_values.notna() & np.isfinite(base_values)
            covered = (values.eq(0.0) & base_finite) | (values.eq(1.0) & base_values.isna())
        else:
            missing = column+'_missing'
            indicators = (pd.to_numeric(frame[missing], errors='coerce')
                          if missing in frame else pd.Series(np.nan, index=frame.index))
            finite = values.notna() & np.isfinite(values)
            covered = (finite & indicators.eq(0.0)) | (values.isna() & indicators.eq(1.0))
        per_feature[column] = int((qualified & covered).sum())
        complete &= covered
    return {'historical_rows': int((historical & complete).sum()),
            'frozen_rows': int((frozen & complete).sum()),
            'qualified_rows': int((qualified & complete).sum()),
            'per_feature': per_feature}


def evaluate(frame, incumbent_bytes, output, proof, *, reconstruction=None):
    train, test = split_recent(frame)
    train, training_population = qualified_training_population(
        train, proof.get('source_receipts', []))
    features, omitted, coverage = choose_features(train)
    y_train, y_test = train.home_win.astype(int), test.home_win.astype(int)
    lineup_features = [c for c in features if lineup_feature(c)]
    bullpen_features = [c for c in features if bullpen_context_feature(c)]
    baseline_features = [c for c in features if c not in lineup_features+bullpen_features]
    batter_features = baseline_features+lineup_features
    recipes = {
        'starter': baseline_features,
        'starter_plus_batters': batter_features,
        'starter_plus_batters_and_bullpen': features,
    }
    # Freeze the recipe on a chronological development tail before touching the
    # exact 300-game final holdout. Hyperparameters remain fixed.
    development_fit, development = split_development(train)
    development_games = len(development)
    development_metrics = {}
    for name, columns in recipes.items():
        development_model = lgb.LGBMClassifier(**PARAMS).fit(
            development_fit[columns].astype(float), development_fit.home_win.astype(int))
        probability = development_model.predict_proba(
            development[columns].astype(float))[:, 1]
        development_metrics[name] = metrics(development.home_win.astype(int), probability)
    development_baseline = development_metrics['starter']
    development_eligible = ['starter'] + [
        name for name in ('starter_plus_batters', 'starter_plus_batters_and_bullpen')
        if improves(development_metrics[name], development_baseline)]
    development_selected = min(
        development_eligible, key=lambda name: development_metrics[name]['brier'])
    models, predictions, comparisons = {}, {}, {}
    output.mkdir(parents=True, exist_ok=True)
    for name, columns in recipes.items():
        model = lgb.LGBMClassifier(**PARAMS).fit(train[columns].astype(float), y_train)
        probability = model.predict_proba(test[columns].astype(float))[:, 1]
        path = output/('model_'+name+'.txt')
        model.booster_.save_model(str(path))
        loaded = lgb.Booster(model_file=str(path))
        np.testing.assert_allclose(loaded.predict(test[columns].astype(float)), probability,
                                   atol=1e-12, rtol=0)
        split_counts = dict(zip(columns, map(int, model.booster_.feature_importance())))
        models[name], predictions[name] = model, probability
        comparisons[name] = {
            'metrics': metrics(y_test, probability),
            'features': columns,
            'features_used_in_splits': [c for c in columns if split_counts[c] > 0],
            'lineup_features_used_in_splits': [c for c in columns
                                               if lineup_feature(c) and split_counts[c] > 0],
            'bullpen_features_used_in_splits': [c for c in columns
                                                if bullpen_context_feature(c) and split_counts[c] > 0],
            'lineup_value_features_used_in_splits': [c for c in columns
                                                     if lineup_value_feature(c)
                                                     and split_counts[c] > 0],
            'bullpen_value_features_used_in_splits': [c for c in columns
                                                      if bullpen_context_value_feature(c)
                                                      and split_counts[c] > 0],
            'feature_split_counts': split_counts,
            'matchup_value_features_used_in_splits': [c for c in columns
                                                     if matchup_performance_feature(c)
                                                     and split_counts[c] > 0],
            'model_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'model_reload_verified': True,
        }
    incumbent = lgb.Booster(model_str=incumbent_bytes.decode())
    old = incumbent.predict(test[incumbent.feature_name()].astype(float))
    incumbent_metrics = metrics(y_test, old)
    for name, comparison in comparisons.items():
        used_pitcher_context = [c for c in comparison['features_used_in_splits']
                                if pitcher_context_feature(c)]
        pitcher_qualification = qualified_context_coverage(
            test, used_pitcher_context, reconstruction=reconstruction)
        team_used = (comparison['lineup_features_used_in_splits']+
                     comparison['bullpen_features_used_in_splits'])
        comparison['pitcher_context_features_used_in_splits'] = used_pitcher_context
        comparison['pitcher_context_qualification'] = pitcher_qualification
        comparison['team_context_qualification'] = qualified_team_context_coverage(
            test, team_used, source_receipts=proof.get('source_receipts'))
        comparison['team_context_training_qualification'] = qualified_team_context_coverage(
            train, team_used, source_receipts=proof.get('source_receipts'))
        comparison['statistical_gate_passed'] = accepted(
            comparison['metrics'], incumbent_metrics)
        comparison['point_in_time_gate_passed'] = (
            not team_used
            or (comparison['team_context_qualification']['qualified_rows'] == EVALUATION_GAMES
                and comparison['team_context_training_qualification']['qualified_rows'] == len(train)))
        if name == 'starter':
            comparison['requested_group_usage_passed'] = True
        elif name == 'starter_plus_batters':
            comparison['requested_group_usage_passed'] = bool(
                any(lineup_performance_feature(c)
                    for c in comparison['lineup_value_features_used_in_splits']))
        else:
            comparison['requested_group_usage_passed'] = bool(
                any(lineup_performance_feature(c)
                    for c in comparison['lineup_value_features_used_in_splits'])
                and any(bullpen_context_performance_feature(c)
                        for c in comparison['bullpen_value_features_used_in_splits'])
                and comparison['matchup_value_features_used_in_splits'])
        comparison['qualified'] = bool(
            comparison['statistical_gate_passed']
            and used_pitcher_context
            and pitcher_qualification['qualified_rows'] == EVALUATION_GAMES
            and comparison['point_in_time_gate_passed']
            and comparison['requested_group_usage_passed'])
    # The final holdout is evaluation-only: it may accept or reject the recipe
    # frozen on development data, but it never changes which recipe is selected.
    selected = development_selected
    winning = selected if comparisons[selected]['qualified'] else None
    candidate, candidate_predictions = models[selected], predictions[selected]
    candidate_metrics = comparisons[selected]['metrics']
    # One prespecified ablation isolates the seven-day contribution from the
    # expanded training period. It is reported, never used to tune the model.
    without_seven = [c for c in recipes[selected]
                     if not (c.endswith('_7d') or c.endswith('_7d_missing'))]
    ablation = lgb.LGBMClassifier(**PARAMS).fit(train[without_seven].astype(float), y_train)
    ablated = ablation.predict_proba(test[without_seven].astype(float))[:, 1]
    selected_features = recipes[selected]
    split_counts = comparisons[selected]['feature_split_counts']
    context_features = [c for c in selected_features if pitcher_context_feature(c)]
    used_context_features = [c for c in context_features if split_counts[c] > 0]
    without_context = [c for c in selected_features if not pitcher_context_feature(c)]
    pitcher_ablation = lgb.LGBMClassifier(**PARAMS).fit(train[without_context].astype(float), y_train)
    without_pitcher = pitcher_ablation.predict_proba(test[without_context].astype(float))[:, 1]
    prospective_feature_coverage, prospective_context_rows = prospective_context_coverage(
        test, context_features)
    context_qualification = qualified_context_coverage(test, context_features, reconstruction=reconstruction)
    team_features = (comparisons[selected]['lineup_features_used_in_splits']+
                     comparisons[selected]['bullpen_features_used_in_splits'])
    prospective_team_feature_coverage, prospective_team_rows = prospective_team_context_coverage(
        test, team_features)
    team_context_qualification = qualified_team_context_coverage(
        test, team_features, source_receipts=proof.get('source_receipts'))
    statistical_gate = comparisons[selected]['statistical_gate_passed']
    promotion_ready = pitcher_promotion_ready(
        candidate_metrics, incumbent_metrics, used_context_features,
        context_qualification['qualified_rows'])
    # This task may promote only the full recipe, and only when both newly
    # requested groups are used by the fitted trees with complete pregame proof.
    promotion_ready = bool(promotion_ready
                           and selected == 'starter_plus_batters_and_bullpen'
                           and winning == selected
                           and team_context_qualification['qualified_rows'] == EVALUATION_GAMES
                           and comparisons[selected]['requested_group_usage_passed'])
    candidate.booster_.save_model(str(output/'model.txt'))
    loaded = lgb.Booster(model_file=str(output/'model.txt'))
    np.testing.assert_allclose(loaded.predict(test[selected_features].astype(float)),
                               candidate_predictions, atol=1e-12, rtol=0)
    report = {'system': 'KS1', 'split_date': test.date.min(),
              'split_completed_at': min(completion_times(test.label_completed_at)).isoformat(),
              'split_prediction_at': min(completion_times(test.as_of_timestamp)).isoformat(),
              'train': {'start': train.date.min(), 'end': train.date.max(), 'games': len(train)},
              'training_population': training_population,
              'test': {'start': test.date.min(), 'end': test.date.max(), 'games': len(test)},
              'candidate': candidate_metrics, 'incumbent': incumbent_metrics,
              'candidate_comparison': comparisons,
              'selected_candidate': selected,
              'winning_qualified_candidate': winning,
              'recipe_selection': {'method': 'chronological_development_tail',
                                   'games': development_games,
                                   'fit_games': len(development_fit),
                                   'selected': development_selected,
                                   'metrics': development_metrics,
                                   'final_holdout_used_for_selection': False},
              'ablation_baseline_without_lineup_or_bullpen': comparisons['starter']['metrics'],
              'ablation_baseline_plus_batters': comparisons['starter_plus_batters']['metrics'],
              'ablation_baseline_plus_batters_and_bullpen': comparisons['starter_plus_batters_and_bullpen']['metrics'],
              'without_seven_day': metrics(y_test, ablated),
              'without_pitcher_context': metrics(y_test, without_pitcher),
              'accepted': promotion_ready,
              'statistical_gate_passed': statistical_gate,
              'promotion_rule': 'strictly lower Brier and no worse logloss than incumbent on identical trailing 300-game holdout',
              'pitcher_promotion_rule': 'all 300 chronological holdout games must carry every learned pitcher context field with verified historical point-in-time or frozen pregame evidence',
              'qualification_basis': qualification_basis(context_qualification),
              'evaluation_kind': 'retrospective_chronological_holdout',
              'historical_evaluation_is_prospective': False,
              'pitcher_context_qualification': context_qualification,
              'heldout_pitcher_context_bases': context_basis_counts(test),
              'cold_start_priors_are_observed_current_form': False,
              'evaluation_window_games': EVALUATION_GAMES,
              'features': selected_features, 'all_admitted_features': features,
              'omitted_features': omitted,
              'individual_starter_training_rows': coverage,
              'individual_starter_features_learned': [c for c in selected_features if individual_feature(c)],
              'individual_starter_features_used_in_splits': [c for c in selected_features if individual_feature(c) and split_counts[c] > 0],
              'heldout_starter_identity_statuses': {
                  side: test[side+'_starter_status'].value_counts(dropna=False).to_dict()
                  for side in ('home', 'away')},
              'individual_feature_training_rows': {
                  c: int(train[c].notna().sum()) for c in features if individual_feature(c)},
              'historical_pitcher_context_training_rows': {
                  side: int(train[side+'_pitcher_context_quality'].notna().sum())
                  for side in ('home', 'away')},
              'pitcher_context_features_learned': context_features,
              'pitcher_context_features_used_in_splits': used_context_features,
              'feature_split_counts': split_counts,
              'pitcher_context_feature_training_rows': {
                  c: int(train[c].notna().sum()) for c in context_features},
              'prospective_pitcher_context_feature_rows': prospective_feature_coverage,
              'prospective_pitcher_context_test_rows': prospective_context_rows,
              'lineup_features_learned': [c for c in selected_features if lineup_feature(c)],
              'bullpen_features_learned': [c for c in selected_features if bullpen_context_feature(c)],
              'lineup_features_used_in_splits': comparisons[selected]['lineup_features_used_in_splits'],
              'bullpen_features_used_in_splits': comparisons[selected]['bullpen_features_used_in_splits'],
              'lineup_value_features_used_in_splits': comparisons[selected]['lineup_value_features_used_in_splits'],
              'bullpen_value_features_used_in_splits': comparisons[selected]['bullpen_value_features_used_in_splits'],
              'prospective_lineup_bullpen_feature_rows': prospective_team_feature_coverage,
              'prospective_lineup_bullpen_test_rows': prospective_team_rows,
              'lineup_bullpen_context_qualification': team_context_qualification,
              'lineup_bullpen_promotion_rule': 'the full candidate must use substantive lineup, matchup and individual-bullpen value fields, not only missingness indicators, in tree splits; every consumed training and exact-300 holdout value must have frozen or MLB-timecoded pre-T10 evidence',
              'minimum_individual_starter_rows_per_side': MIN_STARTER_ROWS,
              'parameters': PARAMS, 'test_used_for_tuning': False,
              'development_used_for_recipe_selection': True, 'holdout_refit': False,
              'model_reload_verified': True,
              'model_sha256': hashlib.sha256((output/'model.txt').read_bytes()).hexdigest(),
              'incumbent_sha256': hashlib.sha256(incumbent_bytes).hexdigest(),
              'input_table_sha256': proof['input_table_sha256'],
              'provider_calls': proof.get('provider_calls', 0), 'prediction_writes': 0, 'official_ledger_writes': 0,
              'limitations': ['Rolling retrospective evaluation, not official live grades.',
                             'Prior box scores can include later scoring corrections.',
                             'Individual starter inputs require retained pregame identity and earlier pitcher boxes.',
                             'Season debuts use explicitly identified prior-year pitcher history or a prior league-starter population; these are priors, not observed current-year form.',
                             'MLB timecoded historical state is retrospective provider reconstruction, not original prospective storage.',
                             'The trailing 300-game holdout does not establish future performance.']}
    (output/'metrics.json').write_bytes(encode(report))
    (output/'input_proof.json').write_bytes(encode(proof))
    (output/'feature_list.json').write_bytes(encode(selected_features))
    rows = test[['date', 'game_id', 'home_win']].copy()
    rows['candidate_p_home'], rows['incumbent_p_home'], rows['without_seven_p_home'] = candidate_predictions, old, ablated
    rows['baseline_without_lineup_or_bullpen_p_home'] = predictions['starter']
    rows['baseline_plus_batters_p_home'] = predictions['starter_plus_batters']
    rows['baseline_plus_batters_and_bullpen_p_home'] = predictions['starter_plus_batters_and_bullpen']
    rows['without_pitcher_context_p_home'] = without_pitcher
    rows.to_parquet(output/'test_predictions.parquet', index=False)
    print(json.dumps(report, indent=2))
    return report


def prepare_historical_feeds(bundle, s3, bucket):
    """PRs reuse retained inputs; main collects without discarding older history."""
    existing = bundle.get('historical_pregame_feeds', [])
    if os.environ.get('GITHUB_EVENT_NAME') == 'pull_request':
        return existing, {'selected_games':0,'verified_games':len(existing),
                          'provider_requests':0,'readback_verified_games':len(existing),
                          'collection_skipped':'pull_request_read_only','errors':[]}
    collected, report = collect_historical_feeds(s3, bucket, bundle.get('full', []))
    retained = {(entry['game_id'], entry['timecode']):entry for entry in existing}
    retained.update({(entry['game_id'], entry['timecode']):entry for entry in collected})
    report['retained_total_games'] = len(retained)
    return list(retained.values()), report


def prepare_historical_team_context(bundle, s3, bucket):
    existing = bundle.get('historical_team_context', [])
    if os.environ.get('GITHUB_EVENT_NAME') == 'pull_request':
        return existing, {'selected_games':0, 'verified_games':len(existing),
                          'provider_requests':0, 'readback_verified_games':len(existing),
                          'collection_skipped':'pull_request_read_only', 'errors':[]}
    collected, report = collect_historical_team_context(s3, bucket, bundle.get('full', []))
    retained = {(entry['game_id'], entry['timecode']):entry for entry in existing}
    retained.update({(entry['game_id'], entry['timecode']):entry for entry in collected})
    report['retained_total_games'] = len(retained)
    verified = [feed_team_context(entry) for entry in retained.values()]
    verified = [entry for entry in verified if entry is not None]
    report['retained_verified_games'] = len(verified)
    report['coverage_status_counts'] = dict(Counter(
        entry['coverage_status'] for entry in verified))
    report['side_status_counts'] = {
        side: {
            'lineup': dict(Counter(entry['sides'][side]['lineup_status']
                                   for entry in verified)),
            'bullpen': dict(Counter(entry['sides'][side]['bullpen_status']
                                    for entry in verified)),
        }
        for side in ('home', 'away')
    }
    return list(retained.values()), report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    record_progress(args.output, 'loading_sources')
    cf, s3, bucket = aws_clients('us-east-1', 'parlay-platform-dev')
    bundle = load_existing(cf, s3, bucket)
    record_progress(args.output, 'loading_historical_pitches')
    statcast_report = load_training_statcast(bundle, s3, bucket)
    (args.output/'historical_statcast_report.json').write_bytes(encode(statcast_report))
    args.output.mkdir(parents=True, exist_ok=True)
    record_progress(args.output, 'collecting_starter_history')
    historical_feeds, feed_report = prepare_historical_feeds(bundle, s3, bucket)
    bundle['historical_pregame_feeds'] = historical_feeds
    (args.output/'historical_feed_report.json').write_bytes(encode(feed_report))
    record_progress(args.output, 'collecting_team_history')
    historical_team, team_report = prepare_historical_team_context(bundle, s3, bucket)
    bundle['historical_team_context'] = historical_team
    bundle['source_receipts'].extend(entry['receipt'] for entry in historical_feeds)
    bundle['source_receipts'].extend(entry['receipt'] for entry in historical_team)
    (args.output/'historical_pregame_feeds.json').write_bytes(encode(historical_feeds))
    (args.output/'historical_team_context_report.json').write_bytes(encode(team_report))
    record_progress(args.output, 'building_table', retained_team_games=len(historical_team))
    table, source_report, *_ = build(bundle)
    args.output.mkdir(parents=True, exist_ok=True)
    frame = table.to_pandas()
    history = {str(g['officialGamePk']): g for g in bundle.get('compact', [])}
    history.update({str(g['officialGamePk']): g for g in bundle.get('full', [])})
    frame['label_completed_at'] = frame.game_id.map(
        lambda pk: history.get(str(pk), {}).get('completedAtUtc'))
    frame.to_parquet(args.output/'input_table.parquet', index=False)
    refs = json.loads((Path(__file__).parent/'model_refs.json').read_bytes())
    ref = refs['lightgbm']
    body = s3.get_object(Bucket=bucket, Key=ref['key'], VersionId=ref['version_id'])['Body'].read()
    if hashlib.sha256(body).hexdigest() != ref['sha256']:
        raise ValueError('incumbent hash mismatch')
    proof = {'input_table_sha256': hashlib.sha256((args.output/'input_table.parquet').read_bytes()).hexdigest(),
             'source_receipts': source_report['source_receipts'], 'source_coverage': source_report['coverage'],
             'optional_reads': source_report['optional_reads'],
             'incumbent_ref': ref, 'historical_feed_report': feed_report,
             'historical_team_context_report': team_report,
             'historical_statcast_report': statcast_report,
             'provider_calls': (feed_report['provider_requests']+
                                team_report['provider_requests'])}
    proof['official_history_source'] = bundle.get('official_history_source')
    reconstruction = PriorPitcherContext(normalize(bundle.get('full', [])),
                                         bundle.get('official_history_source', {}),
                                         pregame_identity_index(bundle))
    record_progress(args.output, 'evaluating_candidates', rows=len(frame))
    report = evaluate(frame, body, args.output, proof, reconstruction=reconstruction)
    # Only isolated experiment artifacts are saved. A separate reviewed model
    # reference change is required for serving; no authority or ledger write.
    record_progress(args.output, 'saving_artifacts', accepted=report['accepted'])
    save_artifact(s3, bucket, args.output)


if __name__ == '__main__':
    main()
