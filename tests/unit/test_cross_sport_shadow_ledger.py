import json, pytest
from inqsi_intelligence.ledger import append_jsonl, grade_binary

def row():
 return {"sport":"nfl","event_id":"g1","observed_at":"2026-09-28T20:00:00Z",
 "p_fundamental":.55,"p_market_aware":.45,"p_market":.40}

def test_shadow_ledger_is_immutable(tmp_path):
 p=tmp_path/"l.jsonl"; append_jsonl(p,row()); append_jsonl(p,row())
 assert len(p.read_text().splitlines())==1
 changed={**row(),"p_market_aware":.44}
 with pytest.raises(ValueError): append_jsonl(p,changed)

def test_grading_measures_market_flip_damage():
 g=grade_binary(row(),True)
 assert g["market_flip_hurt"] is True
 assert g["market_flip_helped"] is False
 assert g["authority_changed"] is False
