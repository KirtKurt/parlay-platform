"""One-off chronological research benchmark, never deployment or qualification.

Run explicitly against the exact generated dataset. It is intentionally not
part of the scheduled preparation workflow or the production training owner.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from mlb_historical_development_data import VERSION, digest
from mlb_challenger_benchmark import fit_adjustment, predict, metrics

FEATURE_SETS = {
    'market_movement': ['deltaGapHome'],
    'market_movement_prior_baseball': ['deltaGapHome', 'teamBattingOps14dGapHome',
        'priorStartingPitchersEra14dGapHome', 'priorStartingPitchersKMinusBbPct14dGapHome',
        'bullpenPitches3dGapHome'],
}


def benchmark(dataset):
    if dataset.get('version') != VERSION or dataset.get('developmentOnly') is not True or dataset.get('prospectiveQualificationEvidence') is not False:
        raise ValueError('exact reconstructed development schema required')
    raw = dataset['rows']; seen = set(); rows = []
    rejected_dates = {r['slateDateEt'] for r in dataset.get('rejections') or []}
    for row in raw:
        if row['slateDateEt'] in rejected_dates: continue
        identity = row['officialGamePk']
        if identity in seen: raise ValueError('duplicate development game')
        seen.add(identity)
        if row.get('originalObservation') is not False or row.get('prospectiveQualificationEvidence') is not False:
            raise ValueError('historical provenance mismatch')
        if digest(row['features']) != row['featureFingerprint']:
            raise ValueError('development feature fingerprint mismatch')
        if not isinstance(row['label']['homeWon'], bool):
            raise ValueError('official binary outcome required')
        rows.append({**{k:v for k,v in row['features'].items() if not isinstance(v,dict)},
                     'gameId':identity,'slateDateEt':row['slateDateEt'],'homeWon':int(row['label']['homeWon'])})
    dates=sorted({r['slateDateEt'] for r in rows})
    if len(dates)<10: raise ValueError('at least ten complete development slates required')
    cut=dates[max(1,int(len(dates)*.7))]
    train=[r for r in rows if r['slateDateEt']<cut]
    validation=[r for r in rows if r['slateDateEt']>=cut]
    if len(train)<300 or len(validation)<100:
        raise ValueError('at least 300 training and 100 validation games required')
    baseline=metrics(validation,[r['marketHomeProbability'] for r in validation])
    candidates=[]
    for name,features in FEATURE_SETS.items():
        for penalty in (.1,1.,10.):
            fitted=fit_adjustment(train,features,penalty)
            result=metrics(validation,predict(validation,fitted))
            candidates.append({'name':name,'penalty':penalty,'model':fitted,'validation':result})
    best=min(candidates,key=lambda r:(r['validation']['brier'],r['validation']['logLoss'],r['name'],r['penalty']))
    return {'createdAtUtc':datetime.now(timezone.utc).isoformat(),'version':'MLB-RECONSTRUCTED-DEVELOPMENT-BENCHMARK-v1',
            'datasetFingerprint':digest(dataset),'datasetRows':len(raw),'completeSlateRows':len(rows),
            'excludedPartialSlateDates':sorted(rejected_dates),'trainRows':len(train),'validationRows':len(validation),
            'trainLastSlate':max(r['slateDateEt'] for r in train),'validationFirstSlate':cut,
            'sameTimeMarket':baseline,'candidates':candidates,'bestDevelopmentCandidate':best,
            'brierImprovementOverMarket':baseline['brier']-best['validation']['brier'],
            'historicalDevelopmentOnly':True,'freshProspectiveQualification':False,
            'productionAuthorityChanged':False,'automaticTrainingOwnerChanged':False,
            'validationUsedForSelection':True,'independentFreshTestStillRequired':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('dataset',type=Path)
    parser.add_argument('--output',type=Path,default=Path('runtime_reports/mlb_reconstructed_development_benchmark_latest.json'))
    args=parser.parse_args();report=benchmark(json.loads(args.dataset.read_text()))
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('candidates','bestDevelopmentCandidate')},indent=2))
