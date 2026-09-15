# Official pitch attribution repair, 2026-09-15

Two retained diagnostic dates expose distinct causes of failed exact counts. The July 28 game 824489 at-bat 8 automatic-ball row carries an observed speed of 79.8 even though the official timer-violation event is `isPitch:false`, `type:no_pitch`, and has no pitch data. The September 1 game 822854 at-bat 10 contains an injury substitution after a 2–1 count: three pitches to batter 695720, then two to batter 683021, who receives the PA.

The v4 reconciliation requires the complete ordered official count-event sequence, matching physical pitch type/speed, player identities, completed play chronology, and a matching terminal PA. Only a single documented pinch-hitter substitution before two strikes is currently supported. Original pitch-to-batter attribution is preserved. Automatic-event tracking contradictions are cleared only in the derived record; original observations and every changed field remain in the exact-version raw envelope. Unknown cases still fail closed.

Official play-event sources use a separate content-bound namespace and endpoint. Every source version is reread and every derivation recomputed before admission. v1/v2/v3 envelopes remain reproducible. Final pitcher counts, individual batter PA totals, outcome completeness, game/date/finality, independent completion time, and retained provenance remain mandatory. A derived payload cannot obtain physical-only admission from an ordinary raw-source path.

Local replay of the complete retained daily inputs passes both physical and outcome gates. This is diagnostic evidence, not hosted qualification. ZIP artifact 10377256239 from run 34918121092 has SHA-256 `8c70b46fcbe0fa8ba7a803c5723331b085bb58a76a5bef36338edf423142c9d5`. Local public-feed receipts are explicitly labeled `LOCAL-REPLAY-NOT-RETAINED`.

| Date | Retained raw SHA-256 | Physical / outcome replay |
|---|---|---|
| 2026-07-28 | `648b97209841b24f83463147086cf0d996a86503271cf78fa1a65c1ee014a2eb` | pass / pass |
| 2026-09-01 | `4da812d3c4bcf9e3eb490b99fdbc99956299de5403befe211b8b0e76d8106956` | pass / pass |

The existing read-only two-date diagnostic is scoped to August 6 and August 26 on this repair branch to investigate the next remaining failures. It uses the existing PR read credentials and makes no source writes. Recovery remains trusted-main only, at most 64 dates and 1,200 seconds per run.

No serving model reference, prediction ledger, qualification cohort, Brier/log-loss criterion, development-only selection, or substantive feature-usage gate changes. The latest hosted v3 run recovered 49/64 dates but still had no substantive matchup values in the development-fit population, so qualification remained deferred and serving authority stayed unchanged.

Follow-up diagnostics: artifact 10377262948 from run 34919212324 has ZIP SHA-256 `4705310deee897314be24bb69605c5dcc412697aa03066eea5cebfb1a5be517e`. August 26 combines a pitcher timer violation (`VP`) and an automatic intentional ball (`VB`). The automatic-code mapping is verified against https://statsapi.mlb.com/api/v1/pitchCodes, including ball codes V/VB/VC/VP/VS and strike codes A/AB/AC. Real pitches preceding an intentional walk remain physical.

August 6 game 824664 ends on a walk-off throwing error during a stolen base. A new derived non-PA ending requires the last completed play of a final game, bottom of inning nine or later, a transition from tied/behind to ahead, matching non-batter scoring runners, fewer than four balls/three strikes, and the final event ending at the play completion time. The richer source is fetched and independently retained before this derivation. An ordinary stolen base cannot establish a non-PA ending.

Both additional complete daily inputs pass local physical and outcome replay:

- 2026-08-06: raw SHA-256 `e08c8e81851505dfc6e5863c5dc27df3efe2a204ade2f1e92530b2d4d665382b`; physical and outcome gates pass.

- 2026-08-26: raw SHA-256 `1feec209b7722671dfd43e8ff3d03a701787622bbca79f36f330f9dd848fa7a6`; physical and outcome gates pass.
