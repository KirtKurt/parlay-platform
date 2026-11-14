# 2stackMLB shadow cycle 2026-10-08T21:14Z

Shadow only. promoted=false. No official model train. No p_home, lock, or ledger rewrite.

## Observed
- Main ingest 37840018289 failed after BBS /v1/matches 429. Odds 401 already degraded.
- Identity isolate is not this failure. Isolate-skip remains on ks1-isolate-bbs-identity-20260911 (PR 713, draft).
- 429 degrade already drafted on ks1-bbs-429-degrade-20261008-1618 (PR 1124). Not merged.
- Nightly artifact ks1-nightly-37840018289: official Brier 0.2368, n=241, new_grades=0, status=no_new_final_grades.
- Last successful daily 37778781271 (12:50Z): predictions.parquet rows=0. Official games=1 (849832) excluded missing_bbs_identity. Confirmed=0 projected=0. NYY/NYM and CWS not on the scored slate.

## Failure taxonomy
Provider rate-limit is a missing catalogue, not a model miss. Do not retrain KS1 or 2stackMLB from a 429.

## Next
Land 429 degrade only with separate approval. Keep 2stackMLB off the publish path.
