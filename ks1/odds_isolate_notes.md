# KS1 Odds 401/403 isolate

Confirmed 2026-09-29T21:36Z run 36633360587 (schedule 904): BBS 200 (8 matches), official+nightly OK, live_inputs failed Odds 401. No BBS-identity slate kill.

Confirmed 2026-09-29T22:40Z run 36639700907 (schedule 907): nightly no_new_final_grades (ledger 227, new_grades 0); live_inputs ValueError provider capture failed; Odds 401; BBS 200 (2+6). ks1-daily artifact missing. No BBS-identity slate kill.

Confirmed 2026-09-30T05:39Z run 36673668408 (schedule 917): ingest failed on live_inputs Odds 401; BBS 200 (6+2); nightly graded (official Brier 0.235617813837353, n=227, new_grades=0); no ks1-daily artifact. No BBS-identity slate kill.

Confirmed 2026-09-30T07:03Z run 36679887591 (schedule 922): ingest failed Refresh KS1 lineups on live_inputs Odds 401; BBS 200 (6+2); official+nightly OK (Brier 0.235617813837353, n=227, new_grades=0, locked_rows=228); no ks1-daily artifact. No BBS-identity slate kill.

Isolate lives on this branch only. Do not merge without Kurt. Do not patch main.
