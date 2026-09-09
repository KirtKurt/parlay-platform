# MLB data expansion verification — 2026-09-09

Repair [#688](https://github.com/KirtKurt/parlay-platform/pull/688) merged as `4672f7ff3fae152394a579c565016798c984811c`.
The [AWS run](https://github.com/KirtKurt/parlay-platform/actions/runs/34395539667) completed successfully, including report publication and artifact upload. Merged production source contract run 34395539688 passed; 52 focused tests passed before merge.

## Verified data

- 4,589 verified archived games, covering 2025-04-01 through 2026-09-08.
- 4,481 reconstructed development games, up from the initial 1,960.
- 4,610 prior-game sources and zero source fetch failures.
- 108 excluded games: ambiguous prior suspended-game timing. Individual reasons are in the dataset; none were silently admitted.
- Original-source audit: 618 inspected, 115 admitted, 495 historical rows without original fundamentals, and eight probability-direction integrity corrections excluded. Source fingerprints remained unchanged.
- Today's collection: 15/15 games, 15 paired starter-rate records, 15 paired bullpen workloads, five posted lineup/OPS pairs. Five locks existed at audit time; ten later games were still Preview and awaiting locks. No official finals or settled labels yet.
- Daily reports published to main in commit `d8c3bd533dbe20fbd120dad83a8554e69dedbff6`. Scheduled preparation is enabled at 10:37 UTC daily.

## Exact persisted dataset

Bucket: `parlay-platform-dev-mlbmlartifactsbucket-rtsuugrwreet`

Key: `mlb/development-data/reconstructed-v1/b8bf86bd4654b60111d5980dd2a5fbaeb36ff73732ce6b0845bdb3926569ead3/dataset.json`

Version: `BQRaBwQ0T1vM3pODkDq6JDoaxOHeJPox`

SHA-256: `b8bf86bd4654b60111d5980dd2a5fbaeb36ff73732ce6b0845bdb3926569ead3`

Canonical bytes: 15,397,327. AWS verified an exact-version readback; the downloaded dataset canonical checksum independently matched this value. ZIP artifact 10121718210 SHA-256: `4476d31fbadc56f1f7832d94ca73eb6d20c7af107224580b6cb5e5633e53be31`.

## Explicit historical benchmark

The benchmark used 3,747 rows from complete archive slates: 2,525 training games through 2026-05-23 and 1,222 validation games starting 2026-05-24. It excluded all 61 dates containing rejected games, preserving whole-slate partitions. Six fixed configurations were compared. Imputation and scaling were fit on training rows only.

| Validation metric | Same-time market | Best candidate by Brier |
|---|---:|---:|
| Accuracy | 57.1195% | 56.7103% |
| Brier (lower is better) | 0.242228957 | 0.242216625 |
| Log loss (lower is better) | 0.677233877 | 0.677174079 |
| Calibration error | 0.005541393 | 0.011548540 |

Selected configuration: market movement plus prior baseball statistics, regularization 0.1. Its Brier improvement is only 0.000012331; accuracy and calibration are worse. This does not establish a meaningful edge over the market. Validation was used for configuration selection, so it is not an independent final test.

The full fitted models and metrics are saved in `mlb_reconstructed_development_benchmark_latest.json`, bound to the exact dataset checksum above.

## Operational boundary

These are reconstructed historical development records, not original live observations or fresh qualification evidence. Current-game pregame starter identities and batting orders remain missing; later official statistical corrections may be present. R8's sealed failed test and the active successor's qualification requirements remain unchanged. No model was promoted, no production authority was granted, and EventBridge remains the automatic production learning owner. The active successor still needs sufficient settled games with original starter/team observations, followed by a model freeze and fresh testing.
