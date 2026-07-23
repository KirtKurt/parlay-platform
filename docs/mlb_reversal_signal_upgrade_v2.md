# MLB reversal signal upgrade v2

## Decision

The algorithm must stop treating `REVERSAL` as a positive feature. The canonical June 28–July 22 audit produced 824 graded meaningful reversal events and only 49.76% moved toward the eventual winner. Synchronized movement across at least three books was 46.81%; tight same-size movement across at least four books was 52.17%. Those are descriptive market states, not betting edges.

The implementation therefore separates three layers:

1. **Measurement:** reversal leg size, prior-leg size, recovery ratio, duration, velocity, acceleration, velocity decay, path distance, path efficiency, persistence, stability, market flips, timing before first pitch, book agreement, book lag, compression, expansion, and dispersion.
2. **Similarity:** a deterministic signature bins the exact size/time/path/agreement state. Similarity can stratify research and add risk flags, but cannot increase winner probability or score.
3. **Admission:** a recommendation label is allowed only for an exact code-reviewed signature with frozen prospective evidence. The locked winner remains visible when the system abstains.

## What the audit supports

| Cohort | Sample | Observed | 95% Wilson lower bound | Treatment |
|---|---:|---:|---:|---|
| All meaningful reversal events | 824 events | 49.76% | 46.35% | Never positive authority |
| Synchronized at least 3 books | 423 events | 46.81% | 42.10% | Never positive authority |
| Tight same-size at least 4 books | 23 events | 52.17% | 32.96% | Similarity only |
| Highest-breadth market flip, one per game | 27 games | 74.07% | 55.32% | Freeze as prospective challenger candidate |
| Latest meaningful reversal within 180 minutes, followed | 99 games | 37.37% | 28.48% | Risk blocker |
| Same late cohort, faded | 99 games | 62.63% | 52.79% | Research only; no automatic flip |
| Highest-breadth 2–3 pp market flip | 9 games | 100.00% | 70.09% | Post-hoc; prohibited from qualification |

The 2–3 pp market-flip cluster is precisely the kind of result that can waste time: it clears 70% only because the sample is nine and the threshold was discovered after looking at outcomes. It is retained as a named research candidate so that future results can be collected, but the registry remains empty.

## Signal Quality Index

The Signal Quality Index is a bounded 0–100 coherence measure built from pre-cutoff data only:

- pull coverage;
- path efficiency;
- directional persistence;
- stability;
- coverage/noise-weighted multi-book direction agreement;
- book depth and coverage;
- market compression;
- book dispersion and reversal penalties.

It is explicitly marked `researchOnly`, `notWinProbability`, and `positiveScoreAuthority: false`. No sportsbook is assigned a subjective “sharp” prior; weights use only observed coverage and path noise.

## Seventy-percent admission contract

The phrase “70% qualified” means evidence admission, not a promise about future game outcomes. An exact signature must have:

- at least 100 prospective, outcome-untouched games over at least 20 slate dates;
- a rule frozen before evaluation begins;
- at least three chronological folds, each with at least 20 games and at least 65% accuracy;
- a most-recent 30-game window at or above 70%;
- overall observed precision at or above 70%;
- a 95% Wilson lower confidence bound at or above 70%;
- independent reproduction, an immutable artifact hash, production approval, and a code-reviewed registry entry.

Prediction rows and ad-hoc audit output cannot self-approve. Until a record is packaged in the registry, the platform retains the immutable pick but abstains from calling it a qualified recommendation. The live runtime enforces this after provider-neutral calibration by clearing `actionablePick`, `playable`, `playablePick`, and accuracy-target eligibility while preserving the selected team, side, probability, signals, and immutable lock semantics.

## Promotion boundary

This upgrade does not change winner direction, moneyline probability, EV, scoring weights, persisted locks, historical labels, or AWS model promotion. New temporal fields enter the frozen feature vector for prospective shadow evaluation. Positive scoring authority remains disabled until the admission contract is satisfied. The empty registry therefore produces visible forecasts but zero qualified wagering recommendations.

## Verification

Fourteen focused unit tests pass locally. They cover cutoff safety, seven temporal horizons, reversal leg construction, deterministic similarity signatures, post-hoc rejection, trusted-record fingerprinting, Wilson lower-bound admission, chronological folds, official-audit compatibility, live abstention, and runtime installation order. The branch is intended for draft review and CI only; it is not merged or deployed by this change set.
