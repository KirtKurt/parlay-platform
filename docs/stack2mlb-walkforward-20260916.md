# 2stackMLB walk-forward 2026-09-16 (shadow-only)

Champion remains KS1-LGB+dual-Poisson. `promoted=false`.

## Official KS1 ledger (nightly 35154548405)

- n=76 locked grades
- new_grades=2
- official Brier=0.246019
- logloss=0.684939
- pick accuracy=0.539 (41/76)
- Wilson 95% [0.428, 0.647]

## Walk-forward not computed

`stack2mlb.walkforward.evaluate` needs per-lock `p_lgb`, `p_poisson`, `p_market`, team/starter IDs.

`graded_ledger.json` lock_evidence is S3 pointer only (`bucket`,`key`,`sha256`,`stored_at`,`version_id`). No lambda/market/IDs on the grade row.

Do not rewrite the ledger. Next shadow attach must join historical date parquets read-only.

## BBS isolate

On main. Today unmatched BBS events skipped; official 823576 excluded `missing_bbs_identity`; slate continued (13 rows).
