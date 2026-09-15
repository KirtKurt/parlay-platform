# KSS1 historical score source evaluation — 2026-09-15

This records the earlier investigation at head `5c10eb2`. The subsequent
[Software Heritage archive qualification](KSS1_SCORE_ARCHIVE.md) establishes
independent witnesses and a locally qualifying cohort that this earlier search
did not obtain. AWS admission and live-pick proof still require a fresh deploy.

## Decision

No new historical source is admitted. The signed settlement source remains
the only production goals-history input. The readiness fixes in PR #897 do
not fill the data gap, fit a model, or authorize a public prediction.

This investigation followed the selected order: retained evidence, public
archives, then licensed providers. None of the examined evidence establishes
a complete, usable archive meeting all admission requirements. This is a
bounded source investigation, not a claim that such an archive cannot exist.

## Retained evidence

The log of [isolated deploy run 34871343076, job 104072459848](https://github.com/KirtKurt/parlay-platform/actions/runs/34871343076/job/104072459848)
contains a goals context at `2026-09-14T17:08:14.540364Z` with:

- 56 goals-table rows, `trained=false`, `model_digest=null`;
- `INSUFFICIENT_TEAM_HISTORY` and optional BBD not configured;
- zero trained `12` selections across 34 fixtures;
- context digest `86604e65f37878a98dc9a88daf5afbec5745a4eaad0a0f098a42ea90e689ce36`.

The context URI is
`s3://parlay-platform-soccer-auto-soccerartifactbucket-yf3bcrhqexri/artifacts/kss1/contexts/86604e65f37878a98dc9a88daf5afbec5745a4eaad0a0f098a42ea90e689ce36.json`.
This is an existing evidence pointer, not a downloaded or reverified S3 object.
The run's artifact list was empty. Its later 264-row market-training result
belongs to the separate market model. Repository runtime reports contain
aggregate settlement counts, not a sufficient raw score/receipt export.

The retained StatsBomb research bundle remains excluded: its assumed
availability and research licensing cannot become production receipts.

## Public archives

### OpenFootball: useful archival leads, not an admitted cohort

[OpenFootball](https://github.com/openfootball/football.json) describes its
datasets as public domain and exposes match dates, some kickoff times, and
scores. A Git author or committer date does not prove publication. For example,
GitHub reports commit `6a225eabc8be1f7e354faa55befe790fea93332d` as unsigned with
no `verified_at` timestamp.

GitHub's server-recorded PR merge time provides a separate lead for when an
exact merged tree was visible. The following merged trees were inspected for
`2025-26/{en.1,en.2,de.1,it.1,es.1,fr.1,nl.1,pt.1}.json`:

| Server merge witness | PR / exact merged commit | Score entries in parseable files | Entries dated after 2025-11-11 |
| --- | --- | ---: | ---: |
| 2025-11-11 14:49:22 UTC | [#46](https://github.com/openfootball/football.json/pull/46), `5dc74a3cae903eba387ac237d85503c403788293` | 835 | 0 |
| 2025-12-04 15:34:17 UTC | [#47](https://github.com/openfootball/football.json/pull/47), `16a82f2b27b59408cd0cd330a22628d98612b7e8` | 814 | 0 |
| 2025-12-09 15:06:23 UTC | [#49](https://github.com/openfootball/football.json/pull/49), `6bae9dea07e9a5064fb3989ef66af852494d4284` | 934 | 10 |
| 2026-08-16 16:25:30 UTC | [#50](https://github.com/openfootball/football.json/pull/50), `ea767ac28cf9a2d737bb3e4ce65aa4b1f4ac9361` | 2,919 | 1,995 |

These are raw file-entry counts, not unique, eligible, or accepted training
rows. Count either a two-element `score` array or a two-element `score.ft`
array. The December 4 EPL file fails JSON parsing and is excluded from that
row's count. A later repair does not retroactively validate the earlier bytes.

This sample illustrates the unresolved chronology: the first sampled witness
is after all its scored matches; the December 9 snapshot contains only ten
score entries after that first witness day. The August snapshot contains many
more results, but its timestamp cannot be moved into the preceding season to
fill a training or validation split. The sampled witnesses alone do not prove
the necessary 500/100/100 usable rows. No model was trained on these files.

Further admission work would need a sufficiently dense series of independent
witnesses, exact UTC kickoff/timezone evidence, an explicit team and event
identity mapping, normal-time semantics, and duplicate/correction reconciliation.
The current generic name normalizer does not establish every provider alias
(for example, a trailing `FC` is not universally removed). Never silently use
fuzzy matches or reinterpret a source correction as an original receipt.

[OpenFootball/england's closed PR history](https://github.com/openfootball/england/pulls?q=is%3Apr+is%3Aclosed)
also contains server-timed result updates, but no complete qualifying source
cohort was assembled or validated from those text snapshots.

### Football-Data.co.uk and independent web captures

[The publisher's archive](https://football-data.co.uk/downloadm.php) offers
historical CSV results. [Its field notes](https://football-data.co.uk/notes.txt)
define match date/time and full-time scores, but do not provide a historical
publication receipt for each score version. A current CSV download cannot fill
that missing field.

[Common Crawl's index documentation](https://blog.commoncrawl.org/cdxj-index)
describes capture timestamps, payload digests and WARC offsets. Those could
support an independently checkable snapshot if the actual score payload is
retrieved and verified. The sampled March 2025 domain query returned only
HTTP-429 robots records, not score CSV payloads; the July query failed with
HTTP 502. Wayback's availability query for the 2024/25 EPL CSV near March 1,
2025 returned no snapshot, and the separate CDX query failed with HTTP 503.
These bounded lookup failures do not prove that no captures exist elsewhere.

## Licensed provider evaluation

| Provider | Documented useful fields / coverage | Evidence still required |
| --- | --- | --- |
| [Sportradar Soccer timeline](https://developer.sportradar.com/soccer/reference/soccer-sport-event-timeline) | Stable event/team IDs, UTC start/resume times, explicit normal-time scores, event processing/update timestamps | A real licensed archive and proof that retained timestamps cover the exact final regulation score and all relevant revisions; complete competition/team coverage |
| [football-data.org](https://docs.football-data.org/general/v4/match.html) | Match/team IDs, UTC date, score duration and full-time scores, `lastUpdated`; [ML Pack Light](https://www.football-data.org/pricing) advertises ten seasons | Provider documentation/receipts binding historical availability to the exact score version, correction history, and applicable use rights |

Sportradar's documented timeline `time` is the record's processing/update time,
not the on-pitch event time, and later updates replace the earlier timestamp.
That makes it a useful candidate to investigate; it does not by itself prove
that a present-day response recreates an earlier complete final-score state.
A historical-data subscription alone is not provenance qualification.

No provider was contacted, no subscription was purchased, and no credential
or archive was supplied in this session. Sportradar is the stronger documented
schema candidate; football-data.org publishes a €29/month history plan (VAT may apply). Neither
is approved for ingestion on documentation alone.

## Evidence needed to complete admission

Obtain an immutable, reproducible export with the following evidence for every
accepted score version:

1. Exact competition, team and event identities; UTC kickoff and reschedule
   history; final regulation-time scores excluding overtime/shootouts.
2. Raw response/archive bytes, hashes, provider/source URI and immutable
   version; independent timestamp evidence bound to those exact bytes.
3. A defensible `available_at` covering receipt/publication, completion and
   any required eligibility certificate; correction and conflict handling.
   Current fetch time remains separate. Never substitute kickoff plus a delay.
4. Applicable source-use rights and exact identity joins to current fixtures.
5. Enough historical cadence and coverage to produce at least five available
   prior games per team, then the existing fixed 60/20/20 chronological
   boundaries, two-day purge, and actual minimum split counts 500/100/100.

Only after source admission should a provider-specific adapter feed the
existing goals-history normalization and feature builder. Keep archived score
receipts distinct from prospective signed settlements; never forge a settlement
signature or backfill a prediction lock. Preserve raw evidence, reconcile
duplicates/conflicts, expose rejection reasons in the source audit, then run
the unchanged qualification and readback path.

Missing xG remains explicit and optional. Candidate selection uses development
data, with lower Brier and no worse log loss on the identical untouched holdout.
Fresh fitted output must pass T60 readback. Public T10 authority still requires
its separate prospective promotion. All of these conditions remain unchanged.
