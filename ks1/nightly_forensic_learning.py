"""Post-settlement KS1 forensic learning rows.

Builds development-only observations by joining immutable pregame KS1 diagnostics to
final audit outcomes. It never changes predictions, model refs, locks, ledgers, or
serving authority. Output is evidence for the normal KS1 challenger pipeline only.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

CONTRACT="KS1-nightly-forensic-learning-v1"

def build(audit_path: Path, forensic_path: Path):
    audit=json.loads(audit_path.read_text())
    forensic=json.loads(forensic_path.read_text())
    finals={}
    for row in (audit.get("games") or audit.get("gradedPredictions") or audit.get("rows") or []):
        gid=str(row.get("game_id") or row.get("gameId") or "")
        if not gid: continue
        winner=row.get("winner") or row.get("winner_team") or row.get("actualWinner")
        if winner: finals[gid]=winner
    rows=[]
    for game in forensic.get("games",[]):
        gid=str(game.get("game_id"))
        winner=finals.get(gid)
        if not winner: continue
        selected=game.get("selected_team")
        rows.append({
          "game_id":gid,"date":game.get("date"),"as_of":game.get("as_of"),
          "selected_team":selected,"actual_winner":winner,
          "selected_won":bool(selected==winner),
          "model_selected_probability":game.get("model_selected_probability"),
          "severity":game.get("severity"),"flags":game.get("flags",[]),
          "starter_regime_summary":game.get("starter_regime_summary",{}),
          "source_contract":game.get("contract"),
          "authority_effect":"development_only_no_serving_or_prediction_effect",
        })
    return {"contract":CONTRACT,"rows":len(rows),"observations":rows,
      "authority_effect":"none","prediction_writes":0,"official_ledger_writes":0,
      "model_ref_writes":0,"lock_writes":0,
      "training_use":"candidate_development_only_normal_chronological_qualification_required"}

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--audit",type=Path,required=True)
    p.add_argument("--forensic",type=Path,required=True); p.add_argument("--output",type=Path,required=True)
    a=p.parse_args(argv); payload=build(a.audit,a.forensic); a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"output":str(a.output),"rows":payload["rows"],"authority_effect":"none"}))
if __name__=="__main__": main()
