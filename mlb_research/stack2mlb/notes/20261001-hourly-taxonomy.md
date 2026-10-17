# 2stackMLB shadow note 2026-10-01 hourly

Champion remains KS1-LGB+dual-Poisson. promoted=false. No official model trained. No p_home, lock, or ledger rewrite.

Source: scheduled ingest 36949148401 (success, 2026-10-02T01:04:19Z–01:12:20Z). Artifacts ks1-daily-36949148401, ks1-nightly-36949148401.

## Official baseline (not a challenger result)
- ledger_rows 227, new_grades 0, status no_new_final_grades
- official Brier 0.235617813837353, logloss 0.6644707582726495, n=227
- pick accuracy 0.5859 (133/227), mean_p_home 0.5117 vs home_win_rate 0.5286
- one eligible grade still excluded: game 823490 no_bound_final

## Failure taxonomy (one miss is not a retrain)
- Late official game 849844 excluded not_scheduled_before_T10; lock coverage 0/1. Not a model miss.
- Two unmatched BBS events isolated (88cf058e-9f36-4750-8905-f8f8180c8964, a490caaf-97fe-4dda-a546-e322ff1e39cb). Slate continued. Isolate already on main.
- Odds 401 slate-kill already degraded on main via #1112. Not reopened here.

## Next shadow step
Walk-forward attach of Elo/Markov/market-prior against the frozen 227-row ledger only. Do not fit LightGBM. Do not publish stack2 over KS1.
