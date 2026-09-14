# Official outcome reconciliation

The first hosted recovery, [run 34899538340](https://github.com/KirtKurt/parlay-platform/actions/runs/34899538340),
completed successfully on merge `2d57a1d`. All 64 fresh daily downloads were
rejected for incomplete PA outcome fields. Zero dates recovered; 372 dates
remained unverified and all eight matchup columns remained empty. The input
table still contained 4,732 games. Development selected starter and deferred
qualification without scoring the reserved final 300 games.

The downloaded ZIP matched GitHub's SHA-256
`b6651e49e701c927bf72f960192f1c29b0a1f319e2a1d16a7ab2fb4d53c6fc78`.
All ten experiment files matched their versioned AWS readback receipts. The
incumbent reference, holdout identities, labels and timestamps were unchanged.

## Narrow repair

Repeated downloads alone do not solve this field-level gap. The repair retains
the raw selected Statcast records and reconciles deterministic accounting in a
separate payload against MLB's official completed plate appearances.

- Reference games must have identical complete PA sets in both sources, with
  the same at-bat identity, batter, pitcher and terminal event. Unknown,
  incomplete, contradictory and duplicate records fail closed.
- Only absent wOBA denominators and absent zero-outcome weights can be derived.
  Supplied fields are never overwritten. Positive-event seasonal weights and
  contact estimates are never inferred; unavailable values remain unknown.
- Each changed field identifies its row, PA and official-source checksum. Raw
  records, official responses and their exact-version storage receipts are
  retained. Every read fetches those exact source versions, checks their contents
  against the embedded evidence, recomputes the derived payload, and adds all
  underlying receipts to the downstream provenance chain. A missing version
  cannot be replaced with the latest version or an embedded copy.
- Official play completion must not exceed the independently retained game
  completion time or the source retrieval time. The feed timestamp must match
  the independently retained schedule's exact original or resume timestamp; PAs
  before a suspension remain bound to the original start. Historical reconstruction is
  still retrospective evidence, not original prospective storage.
- The existing physical-pitch, PA count, game/date and outcome predicate is
  unchanged and runs after reconciliation. Missing physical pitches cannot be
  compensated for with derived accounting. Chronological windows still need
  complete source coverage before exposing any measurement.

MLB's formula excludes intentional walks and sacrifice bunts; ordinary outs
add no numerator weight. [MLB wOBA definition](https://www.mlb.com/glossary/advanced-stats/weighted-on-base-average).
Savant distinguishes outcome accounting from contact estimates in its
[CSV field definitions](https://baseballsavant.mlb.com/csv-docs).

The official filtered feed contract was read directly for
[game 778563](https://statsapi.mlb.com/api/v1.1/game/778563/feed/live): 74 PAs,
including at-bat index zero, batter 660271, pitcher 684007, `field_out`, completed
at `2025-03-18T10:11:44.194Z`. This verifies the independent source format. The
regression tests inject missing fields into synthetic Statcast fixtures; they
do not claim to demonstrate recovered historical production dates.

## Recovery and qualification

The existing trusted-main job first checks both the base daily archive and
its exact game-set revision, avoiding a repeat Savant download when retained
records can supply the missing outcome accounting.
Official evidence is cached by exact raw-game hash, not only game ID. A new
method version can revisit a same-day failure of the earlier raw-only method;
same-method same-day retries remain suppressed. Interrupted dates remain
resumable through retained evidence without recording a false final failure.

The budget remains 64 attempted dates and 1,200 seconds. Counters distinguish
Savant and official-game requests and include both in total provider usage.
Access/rate-limit errors stop further requests. No new credential path or
workflow permission is introduced. Diagnostics retain the rejected event
counts and representative fields, making remaining failures inspectable.

The prespecified development search, fixed 300-game qualification cohort,
Brier/log-loss gates, feature-usage checks, model references, T-10 rules and
2 a.m. audit are unchanged. A hosted run must establish real coverage and a
qualifying candidate before any separate serving promotion.
