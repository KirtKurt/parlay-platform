# KS1 odds-auth degrade

Schedule run 36892414457 failed in `ks1.live_inputs` because The Odds API returned HTTP 401. BBS returned 200 with 3 events. Official schedule had 1 game (849844 PHI@ATL). This is not an unmatched-BBS slate killer.

`odds_auth_unavailable` treats odds 401 and missing key as an empty catalogue (`degraded=odds_auth_unavailable`, `market_status=unavailable`) so capture errors stay empty and daily can score. Odds 429, BBS failures, truncation, and duplicate BBS-to-one-game stay hard errors.

Status 2026-10-01 17:19 UTC: draft PR 1111. Not merged. Main not patched. No p_home/lock/ledger rewrite. No official model train. 2stackMLB stays shadow-only.
