# Retained unfinished-at-bat credit repair

The read-only PR #903 diagnostic artifact from run 34911942492 has ZIP SHA256
`7f2c2a22e97d2335701c8dce12576923af1c0787099605db515f702e3a38106b`.
Both selected dates have exact official pitcher pitch counts and unique pitch
identities. The rejection is an extra credited batter PA for an unfinished at-bat.

| Date | Game | At-bat | Batter | Raw terminal event | Official ending |
|---|---|---|---|---|---|
| 2026-05-04 | 823874 | 26 | 672640 | truncated_pa | caught_stealing_2b |
| 2026-06-04 | 823779 | 59 | 592885 | truncated_pa | caught_stealing_2b |
| 2026-06-04 | 824185 | 49 | 687462 | blank | pickoff_caught_stealing_2b |

Primary evidence is MLB's final game feed, filtered by `official_outcomes.endpoint`:
[823874](https://statsapi.mlb.com/api/v1.1/game/823874/feed/live),
[823779](https://statsapi.mlb.com/api/v1.1/game/823779/feed/live), and
[824185](https://statsapi.mlb.com/api/v1.1/game/824185/feed/live).
At-bat numbers above equal the official zero-based `atBatIndex` plus one.

`official_pa_credit_and_denominator_v3` preserves all raw observations. Only a
wholly blank/truncated at-bat is a reconciliation candidate. It requires the
entire game's recognized PA identity/outcome set to match the official source,
and an exact, unique, completed official non-PA ending for the candidate at-bat.
The batter must match on every row and the terminal pitcher must match. Only the
terminal row's event is derived; every row in that proven non-PA group has
zero denominator exposure. Raw pitch fields, weights, and contact estimates
remain intact. Prior fields and official source hashes accompany each derivation.
Unknown events, missing official PAs, ambiguous identities and incomplete endings
fail closed. Blank/truncated labels alone never exempt a PA from counting.

The trusted-main recovery can now attempt this reconciliation on a retained
physical-attribution mismatch with an exact thrown-pitch inventory. Each retained
candidate is fully reconciled and validated before selection; a rejected base
object cannot hide a usable game-set revision or prevent a fresh provider attempt.
Admission still requires the unchanged exact
physical pitch, individual batter PA, complete outcome and provenance predicates.
Raw/official sources must be retained and reread by exact S3 version and SHA;
all derivations are recomputed on read. v1 and v2 artifacts remain reproducible
under their original policies. Recovery remains bounded to 64 dates/1200 seconds.

Local replay verified the raw hashes against the exported retained receipts:

- 2026-05-04: `bc40505b681ed613f98d9a2ad0f6ce1bf342c82646dcabb96f96bd07ff0f4ff2`
- 2026-06-04: `368e9208d5cf94a46a606b548ca7d1a7246ed84989e0858eda58145b06b19887`

Both dates pass the unchanged physical and outcome predicates after reconciliation:
three non-PA event derivations and ten denominator derivations across 5,947 rows
(five PA accounting fixes and five non-PA exclusions).
This is local algorithm replay with public official responses, not an S3 admission
or qualification claim. Hosted versioned recovery and development evaluation must
still complete. Serving reference, fixed 300-game qualification cohort, Brier,
log-loss, provenance and substantive feature-usage gates are unchanged.

The diagnostic now reads only the retained prior-games bundle, reports unknown
player identities even on uncounted events, and preserves bounded source error
reasons. Its temporary two-date workflow remains read-only under the existing PR
credential policy.
