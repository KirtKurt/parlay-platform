# Historical pitch windows and incomplete later deliveries

Main run `34930565335` at `c056c50830daf9662b964e3b970084c930f9af28`
recovered 40 of 64 additional dates using v8, including May 23. It verified
331 physical-pitch objects, 326 PA-outcome objects and 1,200,485 retained rows.
May 23's recovered artifact has SHA-256
`a03b9c5edb1bf031a73ef9d25649c74a317a065b0e8084a448ca20b86e7dccf1`
and version `_neLKPe8G0jqlPRg11Uf13K4XQvQPS4.`.

The run then failed before fitting. Its frozen 300-game holdout identities,
labels, as-of times and completion times still verified, but all 4,413
training rows were excluded. Of these, 4,395 had
`HISTORY_INCOMPLETE_FAIL_CLOSED`; 18 lacked historical pregame state. The
timecoded team collector independently verified 4,714 retained contexts.
The official-history receipt still certified both 2025 and 2026.

The preceding ingestion run `34930565266` reported the exact delivery gap:
September 14 returned zero of ten expected Statcast games. Recent delivery
coverage fell to 29/30 dates and current-year delivery coverage to 256/257.
`table.build` required those run-relative Statcast download flags for every
historical team row. `Features` also required the current global flag for
every older pitch window. Thus a later missing delivery invalidated earlier
seasons despite their independently verified retained date evidence.

## Exact retained diagnostic

Read-only run `34933768956`, job `104267233307`, downloaded and verified
artifact `10382591161` directly within GitHub, then applied the failed
commit's frozen-split and structural source predicates. It used no AWS
credentials, called no provider, fitted no model and wrote no source.

- Artifact ZIP SHA-256:
  `a1cf2f547fdff5ddcd6e7be3cfed11be9b36242b61d9d16d3c11402063637aab`.
- Failed input-table SHA-256:
  `1cc19a1c3abd3c285f169d8fc4bd7dc01298b349dfdfe6eb87a6b8fe9fbe8d7c`.
- Recovery-report SHA-256:
  `2dd19a1980e7fccbff663a70a0f5bf57c6941400d37c976347b485ac63833639`.
- Official-history SHA-256:
  `03b759c0ccb3e7edf9f5c81a8f4d07e349ae6745b9835156c093514e3710cdbe`,
  version `BwFKn0Gtgt5EqO2H5JyjGCaGBTlXhoO8`.

The failed artifact did not contain the complete source inventory because
it was previously saved only after evaluation. The diagnostic explicitly
reported that limitation; its structural checks did not claim admission.
The trainer now retains input proof and the source report before evaluation.

## Repair boundary

The retained loader can issue an immutable historical pitch scope only after
daily source validation, bound to its exact official-history version and
retained receipt inventory. The table checks that binding before using it.
Physical-pitch dates and PA-outcome dates remain separate. Every consumed
calendar date must be proven; a rejected or deleted historical date cannot
inherit an old scope, and a missing measurement remains missing.

This additional historical proof path leaves the global delivery flags
unchanged. Without a matching scope, their original behavior remains in
force. Official-history completeness, known missing boxes, pregame team
identity, explicit missingness, the 300-row development floor, frozen
qualification cohort, Brier/log-loss and substantive feature-use requirements,
T-10, source-write authorization and serving references remain mandatory.
The scope is not copied into live captures or enabled in the live feature path.
