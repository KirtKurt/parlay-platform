from pathlib import Path
import numpy as np
import pandas as pd
from .io import atomic_bytes, write_json
from .predict import double_chance


def proper_scores(p, y):
    p, y = np.asarray(p, float), np.asarray(y, int)
    if not len(y): return {"n":0,"accuracy":None,"rps":None,"brier":None,"log_loss":None,"double_chance_acc":None}
    if p.shape != (len(y),3) or not np.isfinite(p).all() or np.any(p<0) or not np.allclose(p.sum(axis=1),1): raise ValueError("Invalid metric probabilities")
    truth=np.eye(3)[y]; cumulative=np.cumsum(p,axis=1)[:,:2]-np.cumsum(truth,axis=1)[:,:2]; includes={"1X":{0,1},"12":{0,2},"X2":{1,2}}; covered=[int(int(label) in includes[double_chance(prob)[0]]) for prob,label in zip(p,y)]
    return {"n":len(y),"accuracy":float(np.mean(p.argmax(axis=1)==y)),"rps":float(np.mean(np.sum(cumulative*cumulative,axis=1)/2)),"brier":float(np.mean(np.sum((p-truth)**2,axis=1))),"log_loss":float(-np.mean(np.log(np.clip(p[np.arange(len(y)),y],1e-15,1)))),"double_chance_acc":float(np.mean(covered))}
def _binary_accuracy(p,y): return float(np.mean((np.asarray(p)>=.5)==np.asarray(y))) if len(y) else None
def calibration(p,y,label):
    p,y=np.asarray(p,float),np.asarray(y,float); bins=np.minimum((p*10).astype(int),9); result=[]
    for b in range(10):
        mask=bins==b; result.append({"market":label,"bin_lower":b/10,"bin_upper":(b+1)/10,"n":int(mask.sum()),"mean_probability":float(p[mask].mean()) if mask.any() else None,"observed_frequency":float(y[mask].mean()) if mask.any() else None})
    return result
def summarize(ledger):
    metrics,bins=[],[]
    for season,rows in [("POOLED",ledger),*list(ledger.groupby("season",sort=True))]:
        y=rows.y.to_numpy(int)
        for engine,prefix in [("blend",""),("ML","ml_"),("Dixon-Coles","dc_")]:
            p=rows[[prefix+k for k in ["p_home","p_draw","p_away"]]].to_numpy(float); m={"season":season,"engine":engine,**proper_scores(p,y)}
            if engine=="ML": m.update({"ou25_acc":None,"btts_acc":None})
            else:
                bp="dc_" if engine=="Dixon-Coles" else ""; m.update({"ou25_acc":_binary_accuracy(rows[bp+"p_over25"],rows.y_ou25),"btts_acc":_binary_accuracy(rows[bp+"p_btts_yes"],rows.y_btts)})
            m["always_home_acc"]=float(np.mean(y==0)); book_cols=["book_p_home","book_p_draw","book_p_away"]; mask=rows[book_cols].notna().all(axis=1).to_numpy(); model_same=proper_scores(p[mask],y[mask]); book_same=proper_scores(rows.loc[mask,book_cols].to_numpy(float),y[mask])
            for key,value in model_same.items(): m["model_odds_subset_"+key]=value
            for key,value in book_same.items(): m["book_"+key]=value
            metrics.append(m)
            for k,label in enumerate(["H","D","A"]): bins.extend({"season":season,"engine":engine,**b} for b in calibration(p[:,k],y==k,label))
            bins.extend({"season":season,"engine":engine,**b} for b in calibration(p.max(axis=1),p.argmax(axis=1)==y,"top_label"))
    return pd.DataFrame(metrics),pd.DataFrame(bins)
def markdown_metrics(metrics):
    lines=["| Season | Engine | N | Accuracy | RPS | Brier | Log loss | O/U 2.5 | BTTS | Double chance |","|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def fmt(value,percent=False): return "N/A" if value is None or pd.isna(value) else f"{value:.1%}" if percent else f"{value:.4f}"
    for r in metrics.to_dict("records"): lines.append(f"| {r['season']} | {r['engine']} | {r['n']} | "+" | ".join(fmt(r[k],k.endswith("acc") or k=="accuracy") for k in ["accuracy","rps","brier","log_loss","ou25_acc","btts_acc","double_chance_acc"])+" |")
    return "\n".join(lines)
def write_reports(ledger,directory,prefix="backtest",provenance=None):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True); metrics,bins=summarize(ledger); metrics.to_csv(directory/f"{prefix}_metrics.csv",index=False)
    if prefix=="backtest": metrics.to_csv(directory/"backtest_by_season.csv",index=False)
    bins.to_csv(directory/f"{prefix}_calibration.csv",index=False); write_json(directory/f"{prefix}_summary.json",{"status":"COMPLETED","rows":len(ledger),"provenance":provenance or {},"metrics":metrics.to_dict("records")}); atomic_bytes(directory/f"{prefix}_report.md",("# Out-of-sample report\n\n"+markdown_metrics(metrics)+"\n").encode()); return metrics
def prediction_card(predictions):
    lines=["| UTC kickoff | Competition | Match | H / D / A | Score (P) | O2.5 | BTTS Yes | Double chance | Tier |","|---|---|---|---|---|---|---|---|---|"]; shown=0
    for r in predictions:
        if r["div"] not in {"E0","UCL"}: continue
        shown+=1; safe=lambda x:str(x).replace("|","/").replace("\n"," "); lines.append(f"| {safe(r['kickoff'])} | {r['div']} | {safe(r['home'])} – {safe(r['away'])} | {r['p_home']:.1%} / {r['p_draw']:.1%} / {r['p_away']:.1%} | {r['score']} ({r['p_score']:.1%}) | {r['p_over25']:.1%} | {r['p_btts_yes']:.1%} | {r['double_chance_pick']} ({r['double_chance_probability']:.1%}) | {r['confidence_tier']} |")
    return "\n".join(lines) if shown else "No verified upcoming UCL or Premier League fixtures in this window."
