#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, logging, os, pickle, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
try:
    from _common import ROOT, fail_main
except ModuleNotFoundError:
    from scripts._common import ROOT, fail_main
from soccer_predictor.bbd import weekly_card as bbd_weekly_card
from soccer_predictor.fixtures import fetch_fixtures
from soccer_predictor.io import TeamNames, day, match_id, write_json
from soccer_predictor.model import Model
from soccer_predictor.odds import bookmaker
from soccer_predictor.odds_api import SPORTS, confirm_sport_keys, live_odds, normalize_live_events
from soccer_predictor.report import prediction_card
from soccer_predictor.state import State

LOG = logging.getLogger("soccer_predictor.daily")


def _ensure_models(cutoff):
    required = [ROOT/"models/logistic.pkl", ROOT/"models/elo.pkl", ROOT/"models/dixon_coles.pkl", ROOT/"models/bundle.json"]
    if all(path.exists() for path in required): return
    LOG.warning("Trained model files missing; running train.py with cutoff %s", cutoff)
    subprocess.run([sys.executable, str(ROOT/"scripts/train.py"), "--cutoff", str(cutoff)], cwd=ROOT, check=True)


def _load_models(cutoff):
    _ensure_models(cutoff)
    bundle=json.loads((ROOT/"models/bundle.json").read_text(encoding="utf-8"))
    with open(ROOT/"models/logistic.pkl","rb") as fh: ml=pickle.load(fh)
    with open(ROOT/"models/elo.pkl","rb") as fh: state=pickle.load(fh)
    with open(ROOT/"models/dixon_coles.pkl","rb") as fh: dc=pickle.load(fh)
    model=Model.from_dict(bundle["model"]); model.ml,model.dc=ml,dc
    if not isinstance(state,State): raise ValueError("models/elo.pkl is not a persisted State")
    return bundle,model,state


def _fixture_key(row): return (day(row["kickoff"]).date().isoformat(),row["home"],row["away"])


def _merge_sources(first,last,names):
    first_d,last_d=day(first),day(last); merged={}; status={}
    try:
        base=fetch_fixtures(first,last,names); status["football_data_fixture_layer"]={"ok":True,"count":len(base["fixtures"])}
        for f in base["fixtures"]: merged[_fixture_key(f)]=dict(f)
    except Exception as exc: status["football_data_fixture_layer"]={"ok":False,"reason":str(exc)}
    bbd_rows=[]
    if os.getenv("BBD_API_KEY"):
        try: bbd_rows=bbd_weekly_card(names); status["bbd"]={"ok":True,"count":len(bbd_rows)}
        except Exception as exc: status["bbd"]={"ok":False,"reason":str(exc)}
    else: status["bbd"]={"ok":False,"skipped":"BBD_API_KEY unset"}
    for r in bbd_rows:
        if not first_d <= day(r["kickoff"]) <= last_d: continue
        div="E0" if r["league"]=="epl" else "UCL"; key=_fixture_key(r)
        target=merged.setdefault(key,{"kickoff":r["kickoff"],"home":r["home"],"away":r["away"],"div":div,"source":"BBD"})
        target.update({k:r.get(k) for k in ["bbd_match_id","xg_h_bbd","xg_a_bbd","lineup_h_bbd","lineup_a_bbd","injuries_h_bbd","injuries_a_bbd"]})
        target["source"]="+".join(sorted(set(str(target.get("source","")).split("+")+["BBD"])))
    odds_rows=[]
    if os.getenv("ODDS_API_KEY"):
        try:
            confirmation=confirm_sport_keys(); status["odds_api_sports"]=confirmation
            for div,sport in SPORTS.items():
                if not confirmation.get(sport,False): continue
                for r in normalize_live_events(live_odds(sport),names,div):
                    if first_d <= day(r["kickoff"]) <= last_d: odds_rows.append(r)
            status["odds_api"]={"ok":True,"count":len(odds_rows)}
        except Exception as exc: status["odds_api"]={"ok":False,"reason":str(exc)}
    else: status["odds_api"]={"ok":False,"skipped":"ODDS_API_KEY unset"}
    for r in odds_rows:
        key=_fixture_key(r); target=merged.setdefault(key,{"kickoff":r["kickoff"],"home":r["home"],"away":r["away"],"div":r["div"],"source":"The Odds API"})
        target.update({k:r.get(k) for k in ["mkt_h","mkt_d","mkt_a","mkt_ou25","mkt_book","mkt_last_update","odds_event_id"]}); target["market_feature_safe"]=True
        target["source"]="+".join(sorted(set(str(target.get("source","")).split("+")+["The Odds API"])))
    fixtures=[]
    for row in merged.values():
        if not row.get("kickoff") or not row.get("home") or not row.get("away") or not row.get("div"): continue
        row["match_id"]=row.get("match_id") or match_id(row["div"],row["kickoff"],row["home"],row["away"]); fixtures.append(row)
    return sorted(fixtures,key=lambda r:(r["kickoff"],r["home"])),status


def main():
    parser=argparse.ArgumentParser(description="Daily inference only: BBD + Odds API optional layers; never runs historical backtest")
    parser.add_argument("--from",dest="first",default=None); parser.add_argument("--to",dest="last",default=None); parser.add_argument("--cutoff",default=None)
    parser.add_argument("--out",default=str(ROOT/"artifacts/predictions.csv")); args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    now=pd.Timestamp(datetime.now(timezone.utc)); first=day(args.first or now); last=day(args.last) if args.last else first+pd.Timedelta(days=7); cutoff=args.cutoff or first.date().isoformat()
    bundle,model,state=_load_models(cutoff); names=TeamNames(bundle.get("team_names",{})); fixtures,source_status=_merge_sources(first,last,names); predictions=[]
    for f in fixtures:
        kickoff=pd.Timestamp(f["kickoff"]); kickoff=kickoff.tz_localize("UTC") if kickoff.tzinfo is None else kickoff.tz_convert("UTC")
        if kickoff <= now: continue
        row={"date":day(kickoff),"home":f["home"],"away":f["away"],"div":f["div"],**state.features(f["home"],f["away"],kickoff)}
        for key in ["mkt_h","mkt_d","mkt_a","mkt_ou25","xg_h_bbd","xg_a_bbd","lineup_h_bbd","lineup_a_bbd","injuries_h_bbd","injuries_a_bbd","market_feature_safe"]: row[key]=f.get(key)
        pred=model.predict(row); pred.update(bookmaker(row,pred)); predictions.append({"match_id":f["match_id"],"kickoff":kickoff.isoformat(),"div":f["div"],"home":f["home"],"away":f["away"],"source":f.get("source"),**pred})
    output={"schema":1,"generated_at":now.isoformat(),"fit_cutoff":model.meta["fit_cutoff"],"source_status":source_status,"count":len(predictions),"predictions":predictions,"disclaimer":"This is not betting advice."}
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True); pd.DataFrame(predictions).to_csv(out,index=False); write_json(out.with_suffix(".json"),output); print(prediction_card(predictions)); print(f"\nSaved {out} and {out.with_suffix('.json')}")


def lambda_handler(event,context):
    bucket=os.environ.get("PREDICTION_BUCKET")
    if not bucket: raise RuntimeError("PREDICTION_BUCKET is required")
    old_argv=sys.argv[:]
    try: sys.argv=[str(ROOT/"scripts/daily_job.py"),"--out","/tmp/predictions.csv"]; main()
    finally: sys.argv=old_argv
    payload_path=Path("/tmp/predictions.json")
    if not payload_path.exists(): raise RuntimeError("daily_job.py did not produce /tmp/predictions.json")
    import boto3
    boto3.client("s3").put_object(Bucket=bucket,Key="predictions.json",Body=payload_path.read_bytes(),ContentType="application/json")
    payload=json.loads(payload_path.read_text(encoding="utf-8")); return {"status":"ok","bucket":bucket,"key":"predictions.json","count":payload.get("count",0),"generated_at":payload.get("generated_at")}

if __name__ == "__main__": fail_main(main)
