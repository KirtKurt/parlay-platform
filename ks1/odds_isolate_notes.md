# KS1 Odds 401/403 isolate

Confirmed 2026-09-29T21:36Z run 36633360587 (schedule 904): BBS 200 (8 matches), official+nightly OK, live_inputs failed Odds 401. No BBS-identity slate kill.

Confirmed 2026-09-29T22:40Z run 36639700907 (schedule 907): nightly no_new_final_grades (ledger 227, new_grades 0); live_inputs ValueError provider capture failed; Odds 401; BBS 200 (2+6). ks1-daily artifact missing. No BBS-identity slate kill.

Confirmed 2026-09-30T05:39Z run 36673668408 (schedule 917): ingest failed on live_inputs Odds 401; BBS 200 (6+2); nightly graded (official Brier 0.235617813837353, n=227, new_grades=0); no ks1-daily artifact. No BBS-identity slate kill.

Confirmed 2026-09-30T07:03Z run 36679887591 (schedule 922): ingest failed Refresh KS1 lineups on live_inputs Odds 401; BBS 200 (6+2); official+nightly OK (Brier 0.235617813837353, n=227, new_grades=0, locked_rows=228); no ks1-daily artifact. No BBS-identity slate kill.

Confirmed 2026-09-30T10:38Z run 36702796236 (schedule 930): ingest failed Refresh KS1 lineups on live_inputs Odds 401; BBS 200 (6+2); official 4 games 2026-09-30; nightly official Brier 0.235617813837353 n=227 new_grades=0 locked_rows=228; no ks1-daily artifact. No BBS-identity slate kill.

Confirmed 2026-09-30T16:42Z run 36744947550 (schedule 943): ingest failed Refresh KS1 lineups; no ks1-daily artifact; nightly present; same Odds 401 pattern as 922/930. No BBS-identity slate kill.

Confirmed 2026-09-30T19:35Z run 36765663841 (schedule 950): ingest failed Refresh KS1 lineups; live_inputs ValueError provider capture failed; BBS 200 (6+2); nightly ks1-nightly-36765663841 present (Brier 0.235617813837353, n=227, new_grades=0, locked_rows=228, excluded 823490 no_bound_final); no ks1-daily artifact. No BBS-identity slate kill.

Confirmed 2026-09-30T23:26Z run 36791080224 (schedule 958): ingest failed Refresh KS1 lineups; live_inputs ValueError provider capture failed; Odds 401; BBS 200 (6+2); nightly ks1-nightly-36791080224 present (Brier 0.235617813837353, n=227, new_grades=0, locked_rows=228, excluded 823490 no_bound_final); no ks1-daily / predictions.parquet. No BBS-identity slate kill.

Confirmed 2026-10-01T02:46Z run 36807433185 (schedule 963): ingest failed Refresh KS1 lineups; live_inputs ValueError provider capture failed; Odds 401; BBS 200 (6+2); nightly ks1-nightly-36807433185 present; loss_trace already_published date=2026-09-30 (ledger 227, analyzed 179, wins 109, losses 70); no ks1-daily / predictions.parquet. No BBS-identity slate kill.

Confirmed 2026-10-01T03:39Z run 36811490145 (schedule 965): ingest failed Refresh KS1 lineups; live_inputs ValueError provider capture failed; Odds 401; BBS 200 (6+2); nightly ks1-nightly-36811490145 present (status no_new_final_grades, official Brier 0.235617813837353, n=227, new_grades=0, locked_rows=228, excluded 823490 no_bound_final); loss_trace already_published date=2026-09-30 (analyzed 179, wins 109, losses 70); no ks1-daily / predictions.parquet. No BBS-identity slate kill. Watchdog not started.

Confirmed 2026-10-01T04:33Z run 36815700912 (schedule 967): ingest failed Refresh KS1 lineups (~10m); live_inputs ValueError provider capture failed; Odds 401; BBS 200 (2+2); nightly ks1-nightly-36815700912 present (status not_due_or_already_completed); loss_trace ledger 227 analyzed 179 wins 109 losses 70; no ks1-daily / predictions.parquet. No BBS-identity slate kill. Watchdog not started. No dispatch.

Isolate lives on this branch only. Do not merge without Kurt. Do not patch main.
