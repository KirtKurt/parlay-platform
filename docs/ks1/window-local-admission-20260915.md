# Historical context admission during a current Savant delay

## Reproduced failure

[Main qualification run 34930565335](https://github.com/KirtKurt/parlay-platform/actions/runs/34930565335)
on `c056c50830daf9662b964e3b970084c930f9af28` stopped at training-population
admission with `insufficient training games with verified team context`.
No development comparison or final-holdout predictions were produced.

Downloaded artifact `10382591161` matches GitHub's ZIP SHA-256
`a1cf2f547fdff5ddcd6e7be3cfed11be9b36242b61d9d16d3c11402063637aab`.
Its table has 4,747 rows: 4,704 historical contexts marked
`HISTORY_INCOMPLETE_FAIL_CLOSED`, 33 unavailable, and ten frozen profiles.
Every row reports complete retained official team finals. The separate team
collector verified 4,714 timestamped contexts. The recovery report retained
1,200,485 events and verified 572/622 physical dates and 567/622 outcome dates
(including off-days); these counts do not establish every requested window.

[The same-head production run 34930565266](https://github.com/KirtKurt/parlay-platform/actions/runs/34930565266)
reports 4,732/4,732 official games but a September 14 Savant delay:
29/30 recent dates and 256/257 current-season dates. That run-wide fetch flag
was included in historical official-player admission, even for 2025 games.

## Repair boundary

- Official player-history admission still requires all three official-history
  completeness flags, target-season certification, pregame identities, exact
  team/starter matches, and no missing current/prior-season player boxes
  (including former clubs). Savant fetch flags no longer reject official boxes.
- Physical and outcome window checks use their separate, exact retained-date
  sets when present. A missing date within the consumed window still rejects
  the affected metrics. Legacy callers without date proof still require the
  global completeness flag. Empty proof sets never qualify.
- Seven-day pitch-type matchups continue to require the opposing starter's
  complete 30-day arsenal. Full outcome proof remains separate from physical
  proof; no raw values, source reconciliation or source receipts are changed.
- Input-table/source/active-model bindings are written to the local run artifact
  before admission, so an early failure retains a useful audit trail. This does
  not publish a model, register anything, or write official predictions/grades.

The live profile admission contract, frozen rows, incumbent references,
calibration lineage, provider roles, exact retrospective final-300 manifest,
development-only recipe selection, statistical/usage gates, T-10 rules and
02:00 Eastern audit schedule are unchanged. No score was used to choose this
repair. A corrected main run must qualify independently before any activation.

## Regression checks

Tests cover each missing Savant fetch flag, real official batting/relief values,
explicit missing pitch values, each missing official-history flag, existing
traded-player/missing-box checks, restored exact-version historical windows,
a missing 30-day arsenal date outside the seven-day batter window, absent
outcome proof, legacy/empty proof sets, and artifact retention on failed
admission. Full test/check results are recorded in the repair PR.
