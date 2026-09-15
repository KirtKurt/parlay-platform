# Remaining PA identity diagnosis, 2026-09-15

Main run 34920107571 recovered 46/64 additional dates under v3. Artifact 10378053006 has ZIP SHA-256 `115d872337cc146cff010faca8350356671a5f2bd038c649748f9bf9e0a035e1`. All 10 artifact file hashes, versioned readback receipts, input/manifest hashes, the fixed 300 game identities and label/as-of/completion hashes, and development population hashes were verified.

Nonempty physical dates rose from 102 to 140 and outcome dates from 70 to 116. Development-fit platoon xwOBA values occur in only 21 home and 31 away rows, below the unchanged 300-row admission floor. No matchup value feature is admitted or used. The full recipe loses to starter on development Brier and log-loss, so qualification remains deferred; no final predictions/model or serving change was produced.

The v4 repair in #906 is merged and its main run 34922067306 is pending verification. June 2 separately failed official/Statcast PA identity equality, which v4 deliberately does not relax. The existing two-date read-only diagnostic samples June 2 and June 30 to distinguish data error from a provable attribution rule. Existing permissions, source-write authorization, recovery budgets, model selection, and serving references are unchanged.

## Proven accounting reconciliation

Retained diagnostic artifact 10378383679 from run 34922411327 has ZIP SHA-256 `f4c094da306aef01d5d11462bdb48a3857609655899f364f7c56c0ab5c7266f2`.

June 2 game 823700, at-bat 65 has identical batter 668885 and pitcher 670950 in both sources. Statcast records `fielders_choice_out`, a ground ball, observed wOBA weight 0 and denominator 1, and contact estimate 0.151. The official feed records `force_out`: one other runner forced out at second and the batter reaching first. The terminal pitch matches number 2, FF, 91.6 mph, and the completed play time.

V5 permits only that direction of accounting comparison with a separately retained rich source: matching ground-ball terminal pitch, exactly one non-batter forced out, the batter reaching first, and already observed zero wOBA weight and unit denominator. It never rewrites the event, weight, denominator, or contact estimate for this PA. Missing or contradictory pitch/runner evidence, ordinary field outs, changed player identities, nonzero/unknown weights, or a source without the rich schema all fail closed. Existing v1/v2/v3/v4 objects reproduce their original policies; v4 still rejects this taxonomy mismatch.

Primary sources: [official game and runners](https://statsapi.mlb.com/api/v1.1/game/823700/feed/live), [MLB event taxonomy](https://statsapi.mlb.com/api/v1/eventTypes). The taxonomy identifies both labels as non-hit plate appearances; the independent pitch and runner record establishes this particular force-out accounting match.

Both complete retained daily inputs now pass local physical and outcome replay. Local public-feed receipt versions remain explicitly `LOCAL-REPLAY-NOT-RETAINED`; hosted recovery must retain and reread actual versions before qualification.

- 2026-06-02: raw SHA-256 `ed671cdb29b00f00ae2bfc0d01767ebb05044eecd23b2a0146b969495c49383e`; physical/outcome replay pass.

- 2026-06-30: raw SHA-256 `23546ca7dc7efed73a84aabdbc5e7c8489051eeb96cf23d914f634ad151aef34`; physical/outcome replay pass.
