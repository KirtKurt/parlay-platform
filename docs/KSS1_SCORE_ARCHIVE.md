# Independently witnessed KSS1 score archive

## Source decision

Use the public-domain [OpenFootball football.json](https://github.com/openfootball/football.json)
data as captured by [Software Heritage](https://archive.softwareheritage.org/api/1/origin/https://github.com/openfootball/football.json/visits/).
The independent archive's full visit binds a timestamp to a snapshot, whose
`refs/heads/master` branch binds the exact Git revision. Git author/committer
dates are not used. This supplies the historical availability evidence missing
from a present-day download.

Scope is the 2023/24, 2024/25 and 2025/26 men's EPL, Bundesliga, La Liga, Serie A
and Ligue 1. The preceding season supplies warm-up history; it is not a chosen
subset based on holdout outcomes. Archive observation dates can lag matches
substantially. That lag is preserved even when it excludes a row from training.

Software Heritage documents [visits and snapshot identities](https://docs.softwareheritage.org/devel/swh-web/uri-scheme-api-origin.html)
and its [snapshot model](https://docs.softwareheritage.org/devel/swh-web/uri-scheme-browse-snapshot.html).
The raw evidence is a source receipt, not proof that community-maintained
results are infallible. Conflicting score/schedule versions and explicit
administrative results are quarantined.

## Evidence and chronology

| Requirement | Implementation |
| --- | --- |
| Independently verifiable availability | Fetch the paginated visit inventory from the fixed Software Heritage HTTPS API before installation or CLI qualification. Recompute each complete snapshot's intrinsic hash and compare it with the independently returned visit. Reject any origin, date, status, snapshot or revision mismatch. |
| Immutable source content | Preserve raw commit, tree and file objects. Recompute their Git object hashes and verify the complete revision → root tree → season tree → file chain. Preserve raw file SHA256 and row-level evidence in each receipt. |
| Exact competition | Allowlisted season/file paths, explicit historical competition-name aliases, and explicit per-competition team aliases. No fuzzy join. |
| Exact kickoff | Require date plus time. Convert the source's local league time using the upstream sportdb country-zone convention. Reject missing, ambiguous and nonexistent wall-clock times. |
| Regulation semantics | Only league Matchday records with explicit `score.ft`; reject extra-time/penalty fields, unknown record extensions, playoff rounds and non-integer scores. An awarded/flagged score cannot reappear through an earlier unflagged capture. |
| Historical availability | Use the earliest independent capture containing that exact accepted score/kickoff version. Never use kickoff-plus-delay, download time backdating, or Git author dates. |
| Corrections and duplicates | Quarantine differing final scores and kickoff revisions across captures. Reconcile these five round-robin leagues against signed settlements by competition, season and exact normalized home/away teams, including reschedules on different dates. Exclude conflicts, administrative flags and existing settlement quarantines. Count agreeing duplicates once. |
| Feature chronology | Existing `HistoryIndex` still uses only prior receipts available at T60, excludes the same fixture, requires five prior games per team, and preserves its 20-game/365-day window. |
| Qualification | Existing latest-5000 cap, 60/20/20 time boundaries, two-day purges, 500/100/100 minima, lower Brier and no-worse log loss remain unchanged. xG remains optional. |
| Authority | Only the goals score-archive pointer and shadow context are written. No settlement, public champion, T10 lock or prediction is created by admission. |

The local time convention is implemented in the source project's
[sportdb timezone configuration](https://github.com/sportdb/sport.db/blob/43ff505bf726db5f62890c0b8413eb30a12c88b3/leagues/config/timezones_europe.csv)
and [timezone conversion code](https://github.com/sportdb/sport.db/blob/43ff505bf726db5f62890c0b8413eb30a12c88b3/leagues/lib/leagues/timezones.rb).
The adapter uses Python ZoneInfo with rejection of ambiguous/nonexistent times;
it does not copy the upstream parser's special-case time adjustments.

Snapshot hashing follows the independent archive's
[published manifest format](https://docs.softwareheritage.org/devel/apidoc/swh.model.git_objects.html#swh.model.git_objects.snapshot_git_object).
This binds branch contents without re-requesting every immutable snapshot during
verification. A 429 response remains an external acquisition failure; no alternate
host, invented timestamp, or unverified installation fallback is used.

## Connected runtime path

`scripts/import_kss1_score_archive.py` builds the raw-evidence bundle from
independently witnessed revisions. A local clone is only a transport for Git
objects whose hashes are bound to those revisions. Missing objects never fall
back to the current branch.

Installation revalidates the independent witnesses, writes a content-addressed
bundle under the isolated bucket's `artifacts/kss1/score_archives/` prefix,
verifies S3 readback, and conditionally updates the separate
`MODEL#kss1_goals#global / VERIFIED_SCORE_ARCHIVE` pointer. Older imports cannot
replace a newer archive. The reader verifies bucket/prefix, digest and raw object
proofs and rederives history; it does not trust caller-supplied normalized rows
or a `verified: true` flag.

`source_history()` combines that archive with the existing signed settlement
history. Source audit, rejected files, regulation flags, quarantines,
deduplication, and actual eligible/split counts remain visible in readiness.
No configured archive retains the existing signed-score behavior; an invalid
configured archive fails closed.

The existing manual deployment workflow now admits the archive after the
integrity/settlement proof and before the goals trainer. It then requires the
same fitted shadow candidate, matched model/context readback, operational
health, and a real recorded `12` selection. Admission alone never makes an
empty card pass. The readback repairs also address #897's reviewed findings:

- Filter to the new candidate's model and context before requiring a nonempty
  proof; other models or older contexts cannot falsely fail a valid fresh card.
- Query both Eastern dates covered by the actual T90 capture window, including
  midnight/DST boundaries. Any truncated/failed date response still blocks proof.

Example local audit (no AWS writes):

```sh
python scripts/import_kss1_score_archive.py --out /tmp/kss1-score-archive --train
```

Only `--install`, or the explicit install step in the existing manual deploy,
writes the isolated archive pointer. No deployment was executed while preparing
this change. A fresh manual run from merged main is required for AWS installation,
combined live-settlement qualification, and real recorded-pick readback.

## Cost comparison checked 2026-09-15

| Source | Publicly listed cost | Suitability for this admission |
| --- | --- | --- |
| OpenFootball + Software Heritage | $0 data access | Selected: independently witnessed file versions; admitted rows must pass the adapter's exact chronology/identity checks. Existing AWS storage/execution costs still apply. |
| [API-Football Pro](https://www.api-football.com/) | $19/month; 7,500 requests/day | Historical fixtures are offered. The documentation checked does not establish retained historical score-version receipt evidence; a paid subscription alone does not qualify it. |
| [Sportmonks Starter](https://www.sportmonks.com/football-api/plans-pricing/) | From €29/month for five leagues; older-history add-on from €29 one time | [Older-than-three-season history is an add-on](https://www.sportmonks.com/faq/). Historic version/availability evidence still needs proof before admission. Prices are starting prices, not a project quote. |
| [Sportradar Soccer](https://developer.sportradar.com/soccer/docs/soccer-ig-historical-data) | No fixed project price verified | Historical coverage is documented, varying by competition. No independently verified versioned receipt export was obtained; scope, evidence and commercial terms require a quote. |

No purchase is needed for the demonstrated free-source path. No paid plan was
opened, purchased, or represented as meeting provenance solely because it sells
historical results. Football-Data.co.uk and the existing StatsBomb research
adapter remain outside this production-source admission path.

## Local qualification evidence

[Machine-readable evidence](KSS1_SCORE_ARCHIVE_QUALIFICATION_20260915.json) records
5,216 accepted scores from 72 independently witnessed snapshots (925 file
captures). The unchanged 5,000-row cap leaves 3,165 team-history-eligible rows,
with splits of 1,604 training / 144 validation / 639 holdout. The final candidate
has Brier 0.614068464 versus 0.651073260 and log loss 1.024400969 versus
1.076343882 on the identical 639-game cohort. This comparison is against the
existing untrained goals grid, not the incumbent market model. Both attack and
opponent-defence coefficients have nonzero holdout contributions. xG is absent
and the candidate is not research-only.

The evidence binds the exact raw bundle digest and the retained 92-visit API
inventory fetched directly from Software Heritage in this session. All 72
intrinsic snapshot hashes and their Git object chains were recomputed against
that independent inventory. Repeated API rereads encountered HTTP 429; retained
evidence was verified locally without further requests during that limit. The
installer still requires its own successful fresh inventory request.

These are local retrospective results. AWS deployment, current live-settlement
combination, a recorded future 12 selection, and prospective promotion remain
unproven. The deployment workflow retains all of those checks.
