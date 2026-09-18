import json
from ks1.nightly_forensic_learning import build

def test_join_is_development_only(tmp_path):
    audit=tmp_path/"audit.json"; f=tmp_path/"forensic.json"
    audit.write_text(json.dumps({"games":[{"game_id":"1","winner":"B"}]}))
    f.write_text(json.dumps({"games":[{"game_id":"1","date":"2026-09-17","as_of":"x","selected_team":"A","model_selected_probability":.6,"severity":"medium","flags":[{"code":"X"}],"starter_regime_summary":{"home":{"era_7d_minus_30d":3}},"contract":"KS1-forensic-consideration-v1"}]}))
    out=build(audit,f)
    assert out["rows"]==1 and out["observations"][0]["selected_won"] is False
    assert out["prediction_writes"]==out["official_ledger_writes"]==out["model_ref_writes"]==out["lock_writes"]==0
    assert out["authority_effect"]=="none"
