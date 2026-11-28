# 2stackMLB shadow cycle 2026-10-04T17:14Z

promoted: false
champion: KS1-LGB+dual-Poisson
attach: shadow-only
trained_official_model: false

BBS isolate-skip is already on main (`bbs_assignments(..., isolate_unmatched=True)`). Today's successful ingest matched 2/2 official games and wrote zero `missing_bbs_identity` exclusions, so no isolate repair was required.

## Walk-forward / taxonomy (no new grades)
- official ledger n: 231
- official Brier: 0.2350617033215114
- new_grades this tick: 0
- status: no_new_final_grades
- Failure taxonomy unchanged. One quiet night is not a retrain signal.
- Shadow must not overwrite KS1 p_home, locks, or ledgers.

## Slate shadow attach (not a publish)
- date: 2026-10-04
- rows: 2
- confirmed: SD@MIL 849825
- projected / missing starter: ATL@LAD 849823 (sit for selection)
- NYY/NYM and CWS not on slate

Do not merge. Do not dispatch mlb-research-ingestion.yml.
