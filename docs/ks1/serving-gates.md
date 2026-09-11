# KS1 serving gates (lock continuity, starters, totals, disagreement)

Runtime-only. Frozen model hashes are unchanged. Existing locked `p_home` values
are never rewritten.

1. **Lock continuity.** A valid pregame row (`as_of` ≤ T-10) stays in the date
   parquet if a later poll cannot rescore it (Live/Final, dropped from Preview).
   Nightly lock admission recovers that first-lock version even if the latest
   object omitted the game (`recovered_missing_current`).
2. **Starter risk.** `pick_status=pass` when a starter is missing or lineups
   are still projected. Confirmed lineups with both probables can be `bet`.
3. **Totals.** New scores set `proj_total` to the midpoint of raw Poisson and
   the fresh market total, then scale `lambda_home`/`lambda_away` to match.
4. **Engine agreement.** `pick_status=pass` when LightGBM and Poisson differ by
   more than 8pp, or when those two and the market do not share a side.

`p_home` remains raw LightGBM so temperature/Platt still see the engine.
Serve only `pick_status=bet` rows.
