"""Immutable JSONL persistence and grading for cross-sport shadow intelligence."""
from __future__ import annotations
import json, math
from pathlib import Path
from typing import Iterable, Mapping

REQUIRED=("sport","event_id","observed_at","p_fundamental","p_market_aware","p_market")

def validate_binary_record(row: Mapping) -> dict:
    out=dict(row)
    missing=[k for k in REQUIRED if k not in out]
    if missing: raise ValueError("missing shadow fields: "+",".join(missing))
    for k in ("p_fundamental","p_market_aware","p_market"):
        v=float(out[k])
        if not math.isfinite(v) or not 0<=v<=1: raise ValueError(k+" must be probability")
        out[k]=v
    out["authority_changed"]=False
    return out

def append_jsonl(path: str|Path,row: Mapping) -> None:
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    record=validate_binary_record(row)
    key=(record["sport"],record["event_id"],record["observed_at"])
    if path.exists():
        for line in path.read_text().splitlines():
            old=json.loads(line)
            if (old.get("sport"),old.get("event_id"),old.get("observed_at"))==key:
                if old != record: raise ValueError("conflicting immutable shadow record")
                return
    with path.open("a") as f: f.write(json.dumps(record,sort_keys=True)+"\n")

def grade_binary(row: Mapping, home_won: bool) -> dict:
    r=validate_binary_record(row); y=1.0 if home_won else 0.0
    def metrics(p):
        q=min(1-1e-12,max(1e-12,float(p)))
        return {"correct":(q>=.5)==bool(home_won),"brier":(q-y)**2,
                "log_loss":-(y*math.log(q)+(1-y)*math.log(1-q))}
    f,a,m=metrics(r["p_fundamental"]),metrics(r["p_market_aware"]),metrics(r["p_market"])
    return {**r,"result_home_win":bool(home_won),"fundamental":f,"market_aware":a,"market":m,
            "market_flip_helped": f["correct"] is False and a["correct"] is True,
            "market_flip_hurt": f["correct"] is True and a["correct"] is False}
