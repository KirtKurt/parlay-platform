# Retained substitution evidence and development improvement

The v4 trusted-main run [34922067306](https://github.com/KirtKurt/parlay-platform/actions/runs/34922067306) recovered 45/64 dates, reaching 402 outcome dates (161 nonempty), 423 physical dates (182 nonempty), and 728,870 retained pitches. ZIP SHA256: `49596611272035cc209383bb4f30581a02c52bf2227b0c54d3ac8af7168ccfc2`. All ten file digests, versioned readback receipts, incumbent identity, frozen 300-game manifest, and development population/split hashes were independently checked.

Qualification remained deferred: the full recipe lost on development and used no substantive matchup values. The away seven-day platoon field first cleared admission with 332 fit rows; home had 293. No candidate was exported or registered, and serving authority is unchanged.

## Concrete coverage repair

[Diagnostic run 34924432028](https://github.com/KirtKurt/parlay-platform/actions/runs/34924432028) read exactly two dates, without source writes. ZIP SHA256: `6df1dd538473ac10eeb13935745cb0ea60aaeed5c0d3399af5455da21335e8a5`.

| Date | Raw SHA256 | Proven obstruction |
| --- | --- | --- |
| 2026-07-26 | `b3e75cb808377e3f04a6b40a260bf37e546168bb076211c399b59bd5facf13b7` | Game 823028, AB37: Edwin Arroyo replaced Spencer Steer at 0–2 and completed a lineout; all six pitches retain their actual batter. |
| 2026-08-07 | `ed8f9b0dbbb594cdc8b5fd16c05f404f05eec2560d7eaedc95a3f09923d54f3e` | Game 824081, AB64: Caleb Thielbar entered at 0–0 before the automatic ball and both thrown pitches. The automatic event had a spurious raw speed of 90.4. |

Official full feeds: [823028](https://statsapi.mlb.com/api/v1.1/game/823028/feed/live), [824081](https://statsapi.mlb.com/api/v1.1/game/824081/feed/live). [2026 Official Baseball Rules, 9.15(b), printed page 133](https://mktg.mlbstatic.com/mlb/official-information/2026-official-baseball-rules.pdf) assigns a two-strike substitute's non-strikeout outcome to the substitute; strikeouts have different credit and remain unsupported by this repair.

Method v6 permits only a single, matching, zero-count pitching substitution before all count events, or the existing single pinch-hitter transition with a proven inherited two-strike count and a matching non-strikeout outcome. Exact sequences, per-pitch player/type/speed evidence, official box totals, chronology, raw/source digests, and versioned rereads remain mandatory. V1–v5 objects reproduce under their original policies.

Both full-day local replays pass physical and outcome gates. Public-feed replay receipts explicitly say `LOCAL-REPLAY-NOT-RETAINED`: these are debugging evidence, not hosted qualification or retained production evidence.

## Development-only model improvement

The six regularization variants were defined before inspecting the repaired v4 results. Each was fit to the same 4,091 training games and scored only on the purged 300-game development tail. No frozen qualification predictions were generated.

Input SHA256: `06bc21ba17bc6fadbea402f2452723339c15e2166fd499f34b8ee47ca518e702`.
Fit ID hash: `4caf5f3397224c8427a28a788741ace1d3eb7a543b2e9e6ff23db4a63e497332`.
Development ID hash: `536e2804df82f361aa2956f08e183568b191a7bfdecd0b9148a59ace6b119121`.

| Shallow-shrink recipe | Development Brier | Development log-loss |
| --- | ---: | ---: |
| Starter | 0.2460985331447083 | 0.6853913793775583 |
| Starter + batters | 0.2461255739516370 | 0.6854148353239415 |
| Starter + batters + bullpen | 0.2459486132559088 | 0.6850418864928322 |

The full shallow-shrink model improves both measures over the best starter found in this exploration, but still uses no matchup values. Add this single development-supported configuration (`num_leaves=3`, `n_estimators=150`, `learning_rate=.02`, `reg_lambda=100`) to the bounded search. This is not acceptance: all feature-admission, substantive-usage, provenance, and frozen-cohort Brier/log-loss gates still apply after coverage recovery.
