# KSS1 verified-score readiness

The first fitted release uses verified final-score history. Missing BBD/xG is
explicit and does not block the score-only candidate. It does not waive any
source, competition, team-history, chronological validation, or timing gate.

## Current evidence

Both isolated deployment runs `34871335851` and `34871343076` used source
`372a23e163e0adf52ee1f7cba3b11abe2651752a`. The latter goals readback at
2026-09-14 17:08 UTC had 56 table rows, no fitted model, and zero recorded 12
selections among 34 fixtures. A later market-trainer readback had 264 rows;
that is a different training table and model.

The goals source is the isolated signed settlement table. Each admitted score
must pass signature/digest, regulation eligibility, conflict exclusion, and
competition eligibility. Availability is the latest of score receipt,
completion, and any required eligibility certificate. A current download or
historical odds snapshot cannot supply a past verified score receipt.

The Odds API scores endpoint supports only the previous three days:
https://the-odds-api.com/liveapi/guides/v4/#get-scores
The historical odds backfill cannot reconstruct older final-score labels.

## Readiness evidence

`/v1/soccer-auto/kss1` exposes `training_context.readiness` after the updated
trainer runs. Its source audit counts invalid scores, quarantines, regulation
exclusions, competition exclusions, and accepted scores by competition.

`input_rows` counts table rows. `eligible_rows` counts rows where BOTH teams
have at least five prior verified games in the existing competition/window.
The minimum is 500 training rows plus 100 validation and 100 holdout rows.
700 is only a lower bound: the fixed chronological split and two-day purge
can require more. Actual split counts remain authoritative. All features use
receipts available at the original T60 cutoff; no same-match score or xG is a
feature. Source chronology and the holdout are not relaxed to fill shortages.

## Completing the missing data

Either retain enough eligible prospective score receipts using the existing
scheduled collector, or supply an independently verifiable historical score
archive with regulation semantics, exact team/event identity, and historical
availability evidence. An archive supplied today without that evidence is
research-only; Git author/committer dates alone do not prove public availability.
A follow-on verified archive path is documented in [KSS1_SCORE_ARCHIVE.md](KSS1_SCORE_ARCHIVE.md).
It admits independently witnessed OpenFootball versions through a separate
score-archive pointer; current downloads without that evidence stay excluded.

Before admitting a new archive, inspect coverage for the enabled competitions,
preserve the raw evidence and immutable provenance, reconcile duplicates and
conflicting scores, and verify team-history and split counts. Do not change
the production source to unverified research data or invent missing times.

## Deployment verification

Use a fresh manual `Deploy Isolated Soccer Auto` run from the merged `main`.
Rerunning either old failed run executes its original source SHA.

The final goals step trains after the existing settlement/integration proof,
reads the model and recorded picks back through the isolated API, and verifies:

- A fitted, non-research candidate passed lower Brier/no worse log loss on the
  same chronological holdout, with at least 500/100/100 split rows.
- The trainer result, selected model, context URI, and API readback agree.
- At least one actual recorded `12` selection belongs to that model, has
  complete team history, and was recorded at or before T60.
- The existing operational health proof and public T10 authority remain intact.

An empty card remains a blocked proof. If no qualifying future fixture is in
the existing T90-to-T60 capture interval, the scheduler must record an eligible
future selection before a successful 12-pick proof is possible. Past games are
never backfilled with predictions. Fitted KSS1 records remain shadow output;
prospective qualification and comparison with the incumbent are still required
before any separate public-authority promotion.
