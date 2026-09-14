# Denominator-only official reconciliation

## Hosted evidence

[Run 34907317474](https://github.com/KirtKurt/parlay-platform/actions/runs/34907317474)
completed on merge `61da110`. Its ZIP SHA-256 is
`cc37af4d8e88609e2aac93ab8000903e26e1f943b268caea8eb011d6380c3847`.
All ten exported files matched their exact-version readback receipts. The input
remained 4,732 games, with 4,395 source-qualified training games and the same
reserved 300 identities, labels and timestamps. No final predictions or model
were exported; qualification was deferred and serving remained unchanged.

Of 64 attempted dates, six recovered through 253 official requests and no new
Savant downloads. Fifty-two attempts were rejected by the new broad assumption
that every non-positive, non-excluded PA must carry a zero Savant weight. Four
remained incomplete, with catcher-interference rows carrying denominator `1`
and weight `0.7`; two failed physical/PA counts after accounting repair.
Outcome-qualified dates rose from 249 to 255, but all eight xwOBA matchup
columns remained null. The development-selected recipe was still starter.

The retained diagnostic for game `824747`, PA `9`, shows `catcher_interf`,
batter `677800`, pitcher `693645`, denominator `1`, and weight `0.7`. These are
provider observations, not values to relabel as missing.

## Revised accounting contract

New payloads use `official_pa_woba_denominator_v2`:

- Preserve every supplied event weight, including nonzero weights on events
  outside the ordinary hit/walk categories. Do not infer any missing weight.
- Derive an absent denominator from the independently matched official PA.
- Normalize an explicit counted denominator of `1` to `0` only for the existing
  exclusion set: intentional walk, catcher interference, sacrifice bunt, and
  sacrifice-bunt double play. Keep the raw value intact in the retained raw
  object, and record both original and canonical fields in each derivation.
- Leave other malformed or contradictory denominators untouched and rejected
  by the existing completeness predicate. Missing contact estimates stay null.
- Reproduce old v1 objects using their original rules, exact retained versions,
  and immutable derivations. No new v1 payload is produced.

MLB's denominator uses AB, unintentional walks, sacrifice flies and HBP.
[MLB wOBA definition](https://www.mlb.com/glossary/advanced-stats/weighted-on-base-average).
Catcher interference is excluded from official at-bats.
[MLB at-bat definition](https://www.mlb.com/glossary/standard-stats/at-bat).
Savant documents event weights, denominators and contact estimates as separate
fields. [Savant CSV documentation](https://baseballsavant.mlb.com/csv-docs).
The canonical denominator is an explicit accounting convention; this is not a
claim that the provider supplied the canonical value.

Full-game PA identity/event agreement, exact physical counts, independent
schedule/completion boundaries, source-version rereads, and downstream receipt
propagation remain required. Existing physical/outcome separation from #901 is
preserved. No model parameters, feature-usage requirements, final-cohort gates,
workflow permissions, serving references, T-10 behavior or audit rules change.

Regression coverage includes supplied nonzero event weights, exact exclusion
normalization with original-value provenance, missing weights, old-v1
reproduction, and all previous source identity/time/version rejection cases.
A fresh hosted result is required before claiming additional recovered dates.
