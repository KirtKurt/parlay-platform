# Premier League matchweek 4 card

Generated 2026-09-10 from the verified walk-forward model (fit cutoff 2026-09-10, 10,769 training rows).
Fixtures from the published Premier League list. Kickoffs in UTC.

Not betting advice. Model 1X2 accuracy on 22,147 historical matches: ML 52.3%, blend 51.3%.

Odds API and BBD were **not** used on this card (keys exist in GitHub Secrets; this run had no env).
FBref was not scraped. ClubElo was not used as a training feature.

| UTC kickoff | Match | H / D / A | Score (P) | O2.5 | BTTS Yes | Double chance | Tier |
|---|---|---|---|---|---|---|---|
| 2026-09-12 14:00 | Aston Villa – Nottingham Forest | 33.9% / 27.4% / 38.8% | 1-1 (13.1%) | 51.4% | 55.7% | 12 | Low |
| 2026-09-12 14:00 | Bournemouth – Brentford | 45.4% / 27.5% / 27.0% | 1-1 (13.2%) | 48.2% | 52.8% | 1X | Low |
| 2026-09-12 14:00 | Chelsea – Hull | 30.4% / 28.1% / 41.6% | 1-1 (13.2%) | 41.8% | 46.5% | 12 | Low |
| 2026-09-12 14:00 | Crystal Palace – Ipswich Town | 51.3% / 24.7% / 24.1% | 1-1 (10.9%) | 65.0% | 65.9% | 1X | Medium |
| 2026-09-12 14:00 | Liverpool – Fulham | 55.7% / 24.4% / 19.9% | 1-1 (11.3%) | 60.3% | 60.2% | 1X | Medium |
| 2026-09-12 16:30 | Tottenham – Everton | 34.3% / 30.2% / 35.5% | 1-1 (14.2%) | 40.9% | 48.0% | 12 | Low |
| 2026-09-12 19:00 | Sunderland – Arsenal | 12.8% / 27.9% / 59.3% | 0-1 (18.9%) | 32.1% | 32.4% | X2 | High |
| 2026-09-13 13:00 | Coventry – Brighton | 19.5% / 26.8% / 53.7% | 1-1 (12.7%) | 46.0% | 47.4% | X2 | Medium |
| 2026-09-13 15:30 | Manchester United – Manchester City | 34.1% / 25.0% / 40.8% | 1-1 (10.7%) | 67.2% | 69.0% | 12 | Low |
| 2026-09-14 19:00 | Leeds – Newcastle | 37.4% / 29.0% / 33.6% | 1-1 (13.8%) | 45.2% | 51.5% | 12 | Low |

## Flags

- **Chelsea – Hull** away lean is not credible for a newly promoted side at Stamford Bridge. Treat as a naming/history-gap, not a pick.
- Most matches are **Low** confidence. That matches a 52% model, not a lock sheet.
- Only **Sunderland – Arsenal** is High (away / X2). Still not a guarantee.
- Over 2.5 / BTTS on Palace–Ipswich and United–City are the strongest totals leans.
