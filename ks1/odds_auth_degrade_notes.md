# KS1 odds-auth degrade

Schedule run 36892414457 failed in `ks1.live_inputs` because The Odds API returned HTTP 401. BBS returned 200 with 3 events. Official schedule had 1 game (849844 PHI@ATL). This is not an unmatched-BBS slate killer.

Same cause on schedule run 36899463124 (2026-10-01T17:27:05Z, failure, ~6m). `ks1.live_inputs` raised `provider capture failed` after Odds HTTP 401. BBS was HTTP 200 with 3 events. No `ks1-daily` artifact. Nightly artifact `ks1-nightly-36899463124` still graded. Not a BBS identity slate killer. Not a cron gap.

`odds_auth_unavailable` treats odds 401 and missing key as an empty catalogue (`degraded=odds_auth_unavailable`, `market_status=unavailable`) so capture errors stay empty and daily can score. Odds 429, BBS failures, truncation, and duplicate BBS-to-one-game stay hard errors.

Status 2026-10-01 18:14 UTC: draft PR 1111. verify-ks1 success. Not merged. Main not patched. Watchdog not started. No p_home/lock/ledger rewrite. No official model train. 2stackMLB stays shadow-only.
