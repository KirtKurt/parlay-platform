"""Phase 6 feature comparison: fixed champion, no simulation or deployment."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from ks1.accuracy_features import load_archive
from ks1.accuracy_v2 import (digest, fit_frozen_pair, metric_pair, verify_cohort,
                             with_additions, write_json)
from ks1.phase6_features import FEATURES, VALUES, build_phase6
from ks1.poisson import align_reference, home_probability, predict_exported, verify_reference
from ks1.train import split


def select_additions(train, test):
    selected, coverage = [], {}
    for name in VALUES:
        rates = {part: float(frame[name].isna().mean()*100) for part,frame in [('train',train),('test',test)]}
        eligible = max(rates.values()) <= 40 and train[name].nunique(dropna=True) > 1
        coverage[name] = {'missing_pct': rates, 'eligible': bool(eligible),
                          'reason': 'admitted' if eligible else 'over_40_percent_missing_or_no_training_variation'}
        if eligible:
            selected.append(name)
            flag = name+'_missing'
            if train[flag].nunique() > 1:
                selected.append(flag)
    return selected, coverage


def run(phase2, archive_dir, output):
    if output.exists() and any(output.iterdir()):
        raise ValueError('use an empty output directory')
    output.mkdir(parents=True, exist_ok=True)
    proof = verify_reference(phase2)
    accepted = json.loads((phase2/'metrics.json').read_bytes())
    features = json.loads((phase2/'feature_list.json').read_bytes())
    refs = json.loads((Path(__file__).parent/'model_refs.json').read_bytes())
    poisson_bytes = (Path(__file__).parent/'poisson_model.json').read_bytes()
    if hashlib.sha256(poisson_bytes).hexdigest() != refs['poisson']['sha256']:
        raise ValueError('accepted Poisson hash mismatch')
    if hashlib.sha256((phase2/'model.txt').read_bytes()).hexdigest() != refs['lightgbm']['sha256']:
        raise ValueError('accepted LightGBM hash mismatch')
    poisson = json.loads(poisson_bytes)
    model = lgb.Booster(model_file=str(phase2/'model.txt'))
    if model.feature_name() != features or features != accepted['features']:
        raise ValueError('accepted feature order changed')
    original = pd.read_parquet(phase2/'input_table.parquet')
    old_train, old_test = split(original)
    for label, part in [('train',old_train),('test',old_test)]:
        if accepted[label] != {'start': part.date.min(), 'end': part.date.max(), 'games': len(part)}:
            raise ValueError('accepted split changed')
    baseline = align_reference(old_test, pd.read_parquet(phase2/'test_predictions.parquet'))
    lgb_p = model.predict(old_test[features].astype(float))
    np.testing.assert_allclose(lgb_p, baseline.p_home, atol=1e-12, rtol=0)
    home, away = [predict_exported(poisson[s], old_test) for s in ('home','away')]
    before = {'lightgbm_p_home': lgb_p, 'poisson_p_home': home_probability(home,away)[0],
              'lambda_home': home, 'lambda_away': away, 'projected_total': home+away}
    bundle, receipt = load_archive(archive_dir)
    frame, source_proof = build_phase6(original, bundle)
    train, test = split(frame)
    verify_cohort(train, old_train); verify_cohort(test, old_test)
    pd.testing.assert_frame_equal(train[features],old_train[features])
    pd.testing.assert_frame_equal(test[features],old_test[features])
    additions, coverage = select_additions(train, test)
    lgb_features = with_additions(features, additions)
    # A team's run expectation receives the opposing bullpen's state.
    # Environment, if eligible, applies to both sides. Original lists/order
    # remain unchanged; no previously dropped accuracy groups are restored.
    poisson_features = {side: with_additions(poisson[side]['features'], [c for c in additions
        if c.startswith(('away' if side == 'home' else 'home')+'_phase6_bullpen_') or c.startswith('phase6_')])
        for side in ('home','away')}
    after = fit_frozen_pair(train,test,lgb_features,poisson_features,accepted['parameters'],output/'phase6-candidate')
    champion = output/'champion'; champion.mkdir()
    shutil.copyfile(phase2/'model.txt',champion/'lightgbm.txt')
    (champion/'poisson.json').write_bytes(poisson_bytes)
    write_json(champion/'feature_list.json',{'lightgbm': features,'poisson': {s:poisson[s]['features'] for s in ('home','away')}})
    write_json(champion/'references.json',refs)
    y = test.home_win.astype(int).to_numpy()
    comparison = {'champion':metric_pair(y,before),'phase6':metric_pair(y,after)}
    keys = ['game_id','date','season','home_team','away_team','home_win','home_score','away_score','as_of_timestamp']
    statuses = ['home_phase6_bullpen_status','away_phase6_bullpen_status','phase6_environment_status','phase6_park_source','phase6_weather_missing']
    frame[keys+FEATURES+statuses].to_parquet(output/'phase6-feature-audit.parquet',index=False)
    frame[keys+lgb_features].to_parquet(output/'phase6-candidate'/'feature_table.parquet',index=False)
    test[keys+statuses].reset_index(drop=True).assign(**{
        f'{label}_{name}':values for label,pred in [('before',before),('after',after)] for name,values in pred.items()
    }).to_parquet(output/'test_predictions.parquet',index=False)
    rows = [{'version': label,'model':family,**metrics[family],
             'agreement_accuracy':metrics['agreement_p60']['accuracy'],'agreement_games':metrics['agreement_p60']['games']}
            for label,metrics in comparison.items() for family in ('lightgbm','poisson')]
    pd.DataFrame(rows).to_csv(output/'comparison.csv',index=False)
    report = {'system':'KS1','phase':6,'scope':'features_only','champion_references':refs,
        'train':accepted['train'],'test':accepted['test'],'train_ids_sha256':digest(train.game_id.tolist()),
        'test_ids_sha256':digest(test.game_id.tolist()),'same_ids_labels_and_original_features':True,
        'base_feature_count':len(features),'added_features':additions,'feature_count':len(lgb_features),
        'poisson_features':poisson_features,'coverage':coverage,'sources':source_proof,'comparison':comparison,
        'after_minus_before_brier':{m:float(np.mean((after[m+'_p_home']-y)**2-(before[m+'_p_home']-y)**2))
                                    for m in ('lightgbm','poisson')},
        'environment_by_split':{label:part.phase6_environment_status.value_counts().to_dict()
                                for label,part in [('train',train),('test',test),('unlabeled',frame.loc[frame.home_win.isna()])]},
        'rules':{'missingness_limit_percent':40,'coverage_gate_uses_test_missingness':True,
                 'parameters_changed':False,'test_labels_used_to_tune':False,'reused_holdout':True,
                 'rows_dropped':0,'raw_era_or_win_loss_added':False},
        'limitations':['BB/9 measures walk prevention, not all dimensions of reliever quality.',
                      'Recent reliever usage is an availability proxy; current roster and medical availability are not verified.',
                      'Retrospective prior statistics may include later scoring corrections.',
                      'Weather adjustment is an inherited temperature-only heuristic, not a fitted environmental model.',
                      'Sparse features are preserved for audit but omitted from model inputs.'],
        'simulation_paths':0,'provider_calls':0,'aws_writes':0,'deployment':False,'promotion_proposed':False}
    write_json(output/'comparison.json',report)
    write_json(output/'input_receipt.json',{'phase2':proof,'archive':receipt})
    write_json(output/'cohort.json',{'train_game_ids':train.game_id.tolist(),'test_game_ids':test.game_id.tolist(),'dropped_game_ids':[]})
    print(json.dumps(report,indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase2-dir',type=Path,required=True)
    parser.add_argument('--archive-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    run(args.phase2_dir,args.archive_dir,args.output)
