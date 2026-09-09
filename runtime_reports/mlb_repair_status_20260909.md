MLB repair status — 9 September 2026

The repaired MLB runtime and separate successor pipeline are deployed and verified. Public model predictions remain closed because R8 failed qualification and the successor has not trained or passed a fresh prospective test. There is no evidence-based activation date.

The final deployed source is `80af2e4fd33d3dabf5be477da0344a78ed4521cd`. Deployment passed at 09:34 UTC, including 1,415 regression tests (one skipped), clean-build/cold-start checks, exact Lambda artifact identity, scheduler ownership, read-only API smoke checks and immutable historical-lock checks. [Deployment evidence](https://github.com/KirtKurt/parlay-platform/actions/runs/34333834173).

The natural 09:45 UTC pull verified all 15 games with stored predictions and movements, zero missing records, zero timeouts and zero scheduled failures. Its published acceptance report is healthy, with only the model blocker `no_qualified_champion`. A read-only check at 09:48 UTC verified the 09:46 training record and 09:47 capture record as healthy and matching the final deployed identity; trainer errors were empty. The public model endpoint returned HTTP 503 / NO_QUALIFIED_CHAMPION at 09:51 UTC. The 09:51 results occurrence persisted its summary at 09:54:54 UTC, with one stable delivery metric, a clean matching Lambda request log and unchanged pinned historical HTTP partitions. Its original checker then failed on a raw API-export comparison. The repaired full checker passed at 10:12 UTC with no blockers. It verified the native 10:06 event, summary persistence at 10:09:51, a matching clean Lambda request, one stable delivery metric, unchanged pinned historical partitions, and unchanged deployed code, tables, rule, API routes and canonical API export. [Successful final results verification](https://github.com/KirtKurt/parlay-platform/actions/runs/34337874609).

The repairs cover pending/unpriced games, prediction storage, scoring coverage, bounded GET retries, deployment-rerun lineage and truthful model status. Official probable-starter ERA, strikeout-minus-walk percentage and handedness now reach immutable pregame snapshots. Protected storage was verified for 14 of today's 15 games; one lacked a complete probable-pitcher pair. Exact official game/player IDs, retrieval times, source fingerprints and the T−45 bound are enforced. Missing baseball observations remain missing.

A separate successor now develops under the existing trainer lease, freezes one candidate, captures immutable prospective predictions and evaluates a fresh test. Its read-only serving consumer requires an explicit review of the exact qualifying model and sealed evidence. Public requests cannot compute new picks, write storage or substitute a legacy engine. No activation or wagering was enabled.

| Model evidence | Verified result |
|---|---|
| R8 accepted rows | 610 |
| Fixed training / validation / prospective rows | 308 / 109 / 102 |
| Additional historical diagnostic rows | 91; do not reopen the sealed test |
| R8 / market prospective accuracy | 52.94% / 54.90% |
| R8 / market Brier score | 0.256883 / 0.245231; lower is better |
| R8 calibration error | 0.095167; maximum allowed 0.08 |
| Selected recommendations | 30; minimum required 100 |
| Selected-recommendation calibration error | 0.190878; maximum allowed 0.08 |
| Successor usable development rows | 115 of 610 |
| Rejected historical adapter records | 495; fail the full immutable snapshot contract |
| Successor split | 11 training / 53 calibration / 51 selection |
| Settled rows containing the new starter rates | 0; observations were first collected today |
| Successor status | ACCUMULATING_STARTER_RATE_DEVELOPMENT_DATA |
| Public model status | NO_QUALIFIED_CHAMPION |
| Automatic wagers | Disabled |

R8's 102-game direction test is sealed and failed. Additional accepted rows do not retrain its fixed model or improve that result. The feature audit reproduced its evaluation from exact S3 versions and checksums: its three baseball composite features were absent from all 519 partition records, and book-agreement/reversal differences were constant.

The benchmark evaluated 18 development configurations, producing 10 distinct prediction sets. None beat the market's reviewed-test Brier score. The configuration selected solely on later validation dates reached Brier 0.249740 and accuracy 55.88% on the reviewed test, improving on R8 but still losing to the market on probability error. Those outcomes are development evidence, not fresh qualification evidence.

The successor experiment is `mlb-successor-2026-09-09-starter-rates-v1`. It adjusts locked market probabilities with recorded movement and observed starter-rate differences. R8's features, partitions and test remain unchanged. The exact frozen-partition replay accepts 102 of 519 rows and excludes 417 historical adapters; the complete deployed trainer stream accepts 115 of 610. These are different input sets.

Public activation requires:

1. At least 300 training and 100 validation rows in whole chronological slates, including real starter observations in 100 training, 25 earlier calibration and 25 later selection rows. With 115 usable rows now, at least 285 more are needed. Whole-slate boundaries and starter coverage still apply. The rolling development window retains at most 750 rows.
2. Selection among six predeclared regularized configurations. Calibration uses earlier validation slates; model selection uses later validation slates. The candidate must pass its validation screen before freezing.
3. Fresh, pregame, canonical T−45 predictions beginning on the next Eastern-date slate after durable freeze. The first complete whole slates reaching at least 100 games seal the test once. Incomplete joins exclude the entire slate.
4. Existing direction qualification: at least 500 total rows and 100 fresh test rows, positive Brier skill, better log loss than the same-time market, calibration at most 0.08, at least one percentage point of accuracy lift and no significant McNemar regression.
5. An explicit first review bound to the exact model and qualification digests. Direction approval does not grant playability or wagering authority.

A failed successor test remains failed. A revised model needs a separate protocol and fresh evaluation. Historical snapshots are not reconstructed to manufacture observations. The existing six-hour trainer and two-minute capture owner run the new steps; no additional recurring trainer was introduced. `scripts/review_mlb_successor.py` is read-only by default.

Verification corrections:

- The deployment smoke verifies the successor's own probabilities, identity, artifact and capture time, then checks historical locks independently.
- The existing size guard stopped one intermediate deployment before AWS mutation. Removing redundant logging reduced the step from 20,685 to 20,373 characters, below the unchanged 20,500 limit.
- The storage audit now includes the successor's fixed namespace, verifies both write sites and conditional immutable insertion, and rejects escaped writes. Its source hashes match the final deployed runtime.
- The 09:21 results occurrence persisted a native EventBridge/request-bound summary at 09:24:47, but the verifier failed waiting for its metric. The expected window still had no metric at 09:42. A later timestamp-level diagnostic found an invocation in the preceding 09:20 minute bucket; the checker now uses a fixed EventBridge window that includes that bucket and excludes the next occurrence's preceding bucket. Lambda's HTTP-isolated window stays unchanged. AWS states that EventBridge metrics can be delayed or omitted; they are not complete delivery accounting. The corrected checker marks an absent metric unavailable and still requires the persisted summary, native event identity, matching Lambda request logs, historical HTTP immutability and unchanged deployment/control plane. Published failures or ambiguous delivery counts still fail. [AWS metric documentation](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-monitoring.html).

Both failed proofs are retained. They are not relabeled as successful. The final source correction hashes canonical JSON and sorts integration tuples, so formatting and key order cannot mimic API changes. The digest still covers the complete exported configuration; an authorization-change regression confirms that actual drift remains detectable. The one-time verification uses the exact deployed source, successful deploy-run lineage and original build artifact while identifying the repaired verifier separately.

| Change or check | Evidence |
|---|---|
| Feature audit and benchmark | [PR 670](https://github.com/KirtKurt/parlay-platform/pull/670) |
| Official starter observations | [PR 671](https://github.com/KirtKurt/parlay-platform/pull/671) |
| Protected starter-storage proof | [PR 672](https://github.com/KirtKurt/parlay-platform/pull/672) |
| Successor development and serving | [PR 673](https://github.com/KirtKurt/parlay-platform/pull/673) |
| Successor smoke integration | [PR 674](https://github.com/KirtKurt/parlay-platform/pull/674) |
| Deployment size correction | [PR 675](https://github.com/KirtKurt/parlay-platform/pull/675) |
| Storage audit and metric correction | [PR 676](https://github.com/KirtKurt/parlay-platform/pull/676) |
| Final collector proof | [Run 34335498652](https://github.com/KirtKurt/parlay-platform/actions/runs/34335498652) |
| Corrected API export comparison | [PR 677](https://github.com/KirtKurt/parlay-platform/pull/677) |
| Original final results check | [Run 34335505057](https://github.com/KirtKurt/parlay-platform/actions/runs/34335505057) |
| Repaired results checker | [Run 34337874609](https://github.com/KirtKurt/parlay-platform/actions/runs/34337874609) |
| Trainer refresh | [Run 33662149705](https://github.com/KirtKurt/parlay-platform/actions/runs/33662149705) |

Successor tests cover independent SciPy optimizer parity, valid source extraction, label exclusion, immutable capture, whole-slate test sealing, incomplete-slate exclusion, failed/corrupt/unreviewed authority rejection, DynamoDB pagination/conflicts and read-only public serving. The corrected results verifier and its HTTP/native-provenance contracts passed 339 focused tests.

Deployment reported missing IAM permissions for optional WAF listing and budget creation: `wafv2:ListWebACLs` and `budgets:ModifyBudget`. Those controls were not confirmed by this repair. These nonfatal setup errors do not establish a collector or model runtime failure.
