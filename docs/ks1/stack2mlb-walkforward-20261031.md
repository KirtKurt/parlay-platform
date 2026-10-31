# 2stackMLB shadow walk-forward (2026-10-31)

Champion remains KS1-LGB+dual-Poisson. 2stackMLB is shadow-only.

BBS isolate-skip is already committed on main (`isolate_unmatched=True` in `ks1/daily.py`). Unmatched BBS continue; missing BBS → exclusions; hard errors preserved for duplicates/schema/truncation.

Safe next research:
- Chronological walk-forward on graded_ledger.json + locked prediction rows.
- Failure taxonomy (one miss ≠ retrain).
- Shadow attach of Elo/Markov/market-prior/GLM.

Never train or promote an official model. No SCHEMA/publish path changes. No p_home rewrite.

Status: branch only. Do not merge.
