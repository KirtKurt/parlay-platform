# KS1 Statcast physical/outcome evidence separation

## Scope

Baseball Savant daily CSV responses can reconcile every physically thrown
pitch to the official MLB box while still omitting a terminal plate
appearance's `woba_denom` or `woba_value`. Those are two different evidence
claims and are now stored separately.

- `retainedPhysicalDates` / `retainedPhysicalGames` require exact official
  pitcher identities and `numberOfPitches` reconciliation, unique pitch
  identities, and the independently scheduled game set.
- `retainedCompleteDates` / `retainedCompleteGames` retain the stricter prior
  requirement: physical proof plus official batters-faced, unique terminal
  plate appearances, supported outcomes, and finite wOBA fields.
- Existing complete evidence remains valid for both claims. Missing, denied,
  unversioned, wrong-date, wrong-game, duplicate, truncated, or extra-pitcher
  sources qualify neither claim.

Physical proof can populate velocity, spin, movement, extension, pitch mix,
contact quality, swinging-strike and called-strike fields. Full-PA proof is
still mandatory for wOBA, xwOBA, platoon xwOBA and pitch-type matchup xwOBA.
Nothing synthesizes a missing provider field.

The lineup schema adds a physical-only
`pitch_type_matchup_whiff_pct` for 7- and 30-day windows. It weights each
batter's observed whiff rate by the opposing starter's verified 30-day pitch
mix. All nine lineup slots and the entire starter mix must be supported;
otherwise the value remains null and its explicit missingness indicator is 1.
Changing the opposing starter invalidates both xwOBA and whiff matchup fields.

## Qualification boundary

The new matchup is eligible for the same substantive matchup-usage gate as the
xwOBA matchups. Recipe and parameter selection remain confined to the purged
chronological development partition. The pinned 300-game retrospective final
holdout remains evaluation-only, and this implementation does not alter
`model_refs.json`, prediction locks, grading, calibration, or the 02:00 ET
audit.

No model promotion is implied by this repair. A fresh trusted-main backfill
must first prove the physical receipts, select the full recipe on development,
use substantive batter, matchup, and individual-reliever fields in tree splits,
then beat the incumbent's Brier score with no worse log loss on the exact pinned
300 games.
