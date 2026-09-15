# Bounded physical-count replay

The physical/outcome split's hosted run
[34909229379](https://github.com/KirtKurt/parlay-platform/actions/runs/34909229379)
retained only 57 nonempty physically verified dates. Its report lists 323 dates
with physical pitch/batter-attribution mismatches; every whiff matchup column
was still null, and qualification remained deferred. Re-downloading 46 failing
dates did not resolve their physical mismatch.

The existing read-only PR Phase 1 job can collect two bounded replay snapshots
before its long table build. This diagnostic reads the retained base/revision
objects for 2026-05-04 and 2026-06-04 and the already retained official boxes.
It exports exact pitcher-count and individual batter-PA differences, duplicate
identities, ambiguous at-bats, and profiles of untracked rows. The raw payloads,
checksums, version receipts, schedule and official box inputs accompany them.

No provider request, source-store write, admission change, or serving change is
performed. The temporary workflow steps run only for the diagnostic PR branch,
using the same existing read-only job path and unchanged credentials/permissions.
The replay is uploaded immediately so a targeted repair can be developed before
waiting for another full historical evaluation.
