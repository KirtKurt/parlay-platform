# Align live pitch windows with verified recovery evidence

PR #903 repaired unfinished-at-bat credit in historical training. KS1's live
capture still read the compact raw Statcast artifact, which does not consume
those recovery pointers. A successful historical candidate could therefore see
different coverage at serving time.

Live capture now restores only the 30 calendar dates preceding its requested
slate, using the same retained-history loader as training. The date filter is
applied before source reads and is capped at 30 dates. It cannot fetch a provider,
create a recovery object, or write to AWS. The target date is excluded; scheduled
unfinished games remain unqualified.

The shared loader checks the independently retained schedule and official boxes,
exact thrown-pitch counts, individual batter PAs, outcome completeness, and all
reconciliation derivations. Recovery reads require exact retained raw and official
source versions and hashes. These receipts enter both the compressed captured
history and its input manifest through their existing shared receipt list.
The observation time advances to the latest retained source timestamp, so the
existing T-10 validator cannot mistake new evidence for the older compact
artifact. Missing source timestamps remain unknown.

Physical and outcome coverage remain separate. Missing or invalid evidence stays
unknown. Source completeness flags are unchanged, and evidence outside this
bounded window retains its existing scope. If complete versioned official history
is unavailable, capture preserves its existing compact inputs and makes no new
recovery claims.

No serving-model reference, selection parameter, final qualification cohort,
Brier/log-loss rule, actual feature-usage gate, T-10 rule, or prediction ledger
behavior changes. Live capture's historical-source report explicitly retains the
non-prospective qualification label used by the historical loader.
