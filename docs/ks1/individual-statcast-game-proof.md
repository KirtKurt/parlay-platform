# Independent Statcast game retention

The historical loader previously discarded a whole raw daily object when one
game failed its official physical-pitch or batter-PA reconciliation. That also
discarded independently complete games from the same object.

The fallback now retains a game only after the existing exact physical-pitch,
pitch identity, pitcher identity, batter identity and individual batter PA
checks pass. Outcome proof additionally requires the existing unique-terminal,
official batters-faced and finite wOBA outcome checks. Missing fields are never
filled. Each game's full row set comes from one versioned, checksum-bound source;
pitches are not spliced across revisions.

This path requires the exact scheduled daily game set and matching dates.
Missing/foreign games, unversioned sources, checksum failures, unfinished dates,
and reconciled/derived envelopes cannot enter the raw fallback. Reconciled
objects retain their existing whole-proof verification path. Subsequent failed
revalidation revokes overlapping game proof, as it already revokes date proof.

Verified game IDs remain separate from verified dates. In particular, retaining
individual games does not certify the rejected day, does not bypass calendar
coverage for lineup/pitch-type matchup inputs, and does not change global source
completeness. The existing starter/individual-reliever windows may consume game
proof only when all of their contributing games are verified. Whole-date errors
remain visible alongside explicit lists of individually retained games.

## Retained-payload diagnostic

A local replay of the retained 2026-09-18 diagnostic payload contains 15 games
and 4,541 rows. The new fallback retains 3,978 unchanged rows from 13 games with
complete physical evidence; 11 of those also pass the outcome checks.

- Games 823977 and 824463 remain physically unqualified.
- Games 823005 and 824303 retain physical proof only; incomplete outcome fields
  continue to block their outcome metrics.
- The date remains absent from both verified-date sets.

This is a local retained-payload reproduction, not a new live AWS readback or a
model qualification result. No frozen-holdout labels or predictions were used.

## Safety and validation

No provider calls, data fabrication, model training, model-reference changes,
prediction/lock/ledger writes, audit scheduling changes or authority changes are
introduced. The 300-game development floor and retrospective holdout gates are
unchanged. Hosted exact-head checks and review are required before landing.

Regression coverage includes per-game rejection, physical/outcome separation,
unchanged whole-date matchup gates, invalid envelopes, source-version isolation,
and revocation after later rejection.
