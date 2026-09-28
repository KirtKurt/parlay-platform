"""Calibration metrics and fit boundaries; fitting data must be completed locked predictions."""
import math

def brier(rows):
    if not rows: return None
    return sum((float(r["p"])-int(r["y"]))**2 for r in rows)/len(rows)

def logloss(rows, eps=1e-12):
    if not rows:return None
    s=0.0
    for r in rows:
        p=min(1-eps,max(eps,float(r["p"]))); y=int(r["y"])
        s += -(y*math.log(p)+(1-y)*math.log(1-p))
    return s/len(rows)

def expected_calibration_error(rows, bins=10):
    if not rows:return None
    total=len(rows); ece=0.0
    for i in range(bins):
        lo,hi=i/bins,(i+1)/bins
        b=[r for r in rows if lo <= float(r["p"]) < hi or (i==bins-1 and float(r["p"])==1)]
        if b:
            ece += len(b)/total*abs(sum(float(r["p"]) for r in b)/len(b)-sum(int(r["y"]) for r in b)/len(b))
    return ece
