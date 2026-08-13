from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean
from typing import Any, Mapping, Sequence

from .ml import Model, train_logistic, chronological_split, log_loss, brier_score, calibration_error

BLOCKED_NAMES = {
    'label','winner','home_won','result','score_home','score_away','settled','completed',
    'postgame','final','outcome','outcomes','actual_winner','game_result'
}


def _numeric(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(v)
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def candidate_numeric_features(rows: Sequence[Mapping[str, Any]], min_coverage: float = .65) -> list[str]:
    if not rows:
        return []
    counts: dict[str,int] = {}
    for row in rows:
        for k,v in row.items():
            lk = str(k).lower()
            if any(b == lk or lk.startswith(b + '_') for b in BLOCKED_NAMES):
                continue
            if _numeric(v) is not None:
                counts[str(k)] = counts.get(str(k),0) + 1
    threshold = max(3, int(len(rows) * min_coverage))
    return sorted(k for k,c in counts.items() if c >= threshold)


def _effect(rows, labels, name: str) -> float:
    pos=[]; neg=[]
    for row,y in zip(rows,labels):
        v=_numeric(row.get(name))
        if v is None: continue
        (pos if int(y) else neg).append(v)
    if len(pos) < 3 or len(neg) < 3:
        return 0.0
    pooled = (sum((x-mean(pos))**2 for x in pos)+sum((x-mean(neg))**2 for x in neg))/max(1,len(pos)+len(neg)-2)
    scale = math.sqrt(max(pooled,1e-12))
    return abs(mean(pos)-mean(neg))/scale


def rank_features(rows, labels, max_features: int = 40) -> list[str]:
    names = candidate_numeric_features(rows)
    scored = sorted(((name,_effect(rows,labels,name)) for name in names), key=lambda x:(x[1],x[0]), reverse=True)
    return [n for n,s in scored[:max_features] if s > 0]


def augment_interactions(rows: Sequence[Mapping[str, Any]], names: Sequence[str], max_base: int = 12) -> tuple[list[dict], list[str]]:
    base=list(names)
    strongest=base[:max_base]
    interactions=[]
    for i,a in enumerate(strongest):
        interactions.append(f'{a}__sq')
        for b in strongest[i+1:]:
            interactions.append(f'{a}__x__{b}')
    out=[]
    for row in rows:
        r=dict(row)
        for a in strongest:
            av=_numeric(row.get(a)) or 0.0
            r[f'{a}__sq']=av*av
        for i,a in enumerate(strongest):
            av=_numeric(row.get(a)) or 0.0
            for b in strongest[i+1:]:
                bv=_numeric(row.get(b)) or 0.0
                r[f'{a}__x__{b}']=av*bv
        out.append(r)
    return out, base+interactions


@dataclass(frozen=True)
class ChallengerResult:
    model: Model
    metrics: dict
    feature_names: tuple[str,...]
    search_manifest: dict


def discover_challenger(rows: Sequence[Mapping[str,Any]], labels: Sequence[int], *, min_train: int = 50, min_validation: int = 20) -> ChallengerResult:
    if len(rows) != len(labels) or len(rows) < min_train + min_validation:
        raise ValueError('INSUFFICIENT_ROWS_FOR_AUTONOMOUS_DISCOVERY')
    tr,ty,va,vy=chronological_split(list(rows),list(labels),.2)
    if len(va) < min_validation:
        cut=len(rows)-min_validation
        tr,ty,va,vy=list(rows[:cut]),list(labels[:cut]),list(rows[cut:]),list(labels[cut:])
    ranked=rank_features(tr,ty,max_features=40)
    if not ranked:
        raise ValueError('NO_PREGAME_NUMERIC_FEATURES_DISCOVERED')
    configs=[]
    for n in (8,16,24,40):
        names=ranked[:min(n,len(ranked))]
        configs.append(('linear',names))
        aug_tr,aug_names=augment_interactions(tr,names,max_base=min(8,len(names)))
        configs.append(('interaction',aug_names))
    best=None
    for family,names in configs:
        train_rows=list(tr); val_rows=list(va)
        if family=='interaction':
            train_rows,_=augment_interactions(tr,[n for n in names if '__' not in n],max_base=8)
            val_rows,_=augment_interactions(va,[n for n in names if '__' not in n],max_base=8)
        for lr in (.01,.03,.06):
            for l2 in (.0003,.001,.003):
                model=train_logistic(train_rows,ty,feature_names=names,epochs=650,lr=lr,l2=l2,
                    metadata={'autonomous':True,'family':family,'lr':lr,'l2':l2})
                metrics={'logLoss':log_loss(model,val_rows,vy),'brier':brier_score(model,val_rows,vy),
                         'calibrationError':calibration_error(model,val_rows,vy)}
                objective=metrics['logLoss'] + .25*metrics['brier'] + .15*metrics['calibrationError']
                if best is None or objective < best[0]:
                    best=(objective,model,metrics,tuple(names),family,lr,l2)
    _,model,metrics,names,family,lr,l2=best
    return ChallengerResult(model,metrics,names,{
        'candidateRawFeatures':len(candidate_numeric_features(tr)), 'rankedFeatures':ranked,
        'selectedFeatureCount':len(names),'selectedFamily':family,'learningRate':lr,'l2':l2,
        'trainingRows':len(tr),'validationRows':len(va),
    })
