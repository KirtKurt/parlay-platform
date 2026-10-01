# KS1 odds-auth degrade

Schedule run 36892414457 failed in `ks1.live_inputs` because The Odds API returned HTTP 401. BBS returned 200 with 3 events. Official schedule had 1 game (849844 PHI@ATL). This is not an unmatched-BBS slate killer.

Same cause on schedule run 36899463124 (2026-10-01T17:27:05Z, failure, ~6m). `ks1.live_inputs` raised `provider capture failed` after Odds HTTP 401. BBS was HTTP 200 with 3 events. No `ks1-daily` artifact. Nightly artifact `ks1-nightly-36899463124` still graded. Not a BBS identity slate killer. Not a cron gap.

`odds_auth_unavailable` treats odds 401 and missing key as an empty catalogue (`degraded=odds_auth_unavailable`, `market_status=unavailable`) so capture errors stay empty and daily can score. Odds 429, BBS failures, truncation, and duplicate BBS-to-one-game stay hard errors.

Status 2026-10-01 18:14 UTC: draft PR 1111. verify-ks1 success. Not merged. Main not patched. Watchdog not started. No p_home/lock/ledger rewrite. No official model train. 2stackMLB stays shadow-only.

Status 2026-10-01 19:14 UTC: schedule run 36907961985 FAILURE 18:35:28Z-18:46:15Z (~11m). Cause still Odds API 401 in ks1.live_inputs. BBS 200 count=3. Official games=1 (849844 PHI@ATL Preview). ks1-daily missing. ks1-nightly present. Brier=0.235618 n=227 new_grades=0. Not CRON_GAP. Did not dispatch, merge, or start watchdog.

Status 2026-10-01 20:14 UTC: schedule run 36914263110 FAILURE 19:26:12Z-19:35:53Z (~10m). Ingest step Refresh KS1 lineups failed: ValueError provider capture failed. Odds HTTP 401 on /v4/sports/baseball_mlb/odds. BBS HTTP 200 count=3 (PHI@ATL, CHC@SD, BOS@NYY). Official games=1 Preview 849844 PHI@ATL 2026-10-02T00:00Z. No ks1-daily artifact (predictions.parquet absent). ks1-nightly-36914263110 present. official Brier=0.235618 n=227 new_grades=0. Not CRON_GAP (last schedule start 48m before this check). Did not dispatch, merge, patch main, or start the 24h watchdog. NYY/NYM and CWS remain sits. 2stackMLB shadow-only; no official train.
