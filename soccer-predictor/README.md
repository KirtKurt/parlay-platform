# soccer-predictor

Walk-forward soccer model under `soccer-predictor/` in `KirtKurt/parlay-platform`.
Isolated from Soccer AUTO / MLB / Tennis. Do not merge to `main` or run `Deploy SAM to AWS` yet.

Not betting advice.

Verified run: https://github.com/KirtKurt/parlay-platform/actions/runs/34495684676
Artifact: https://github.com/KirtKurt/parlay-platform/actions/runs/34495684676/artifacts/10161014306

## Data stack

| Source | Role | This verified run |
| --- | --- | --- |
| football-data.co.uk | Historical scores + closing 1X2 | Used. 92 CSVs. |
| Our Elo + Dixon-Coles + ML | Walk-forward engine | Used. 22,147 matches. |
| The Odds API (`ODDS_API_KEY`) | Live 1X2 / O/U / BTTS | Not used in this backtest. |
| BBD (`BBS_API_KEY` mapped to `BBD_API_KEY`) | Live xG / lineups | Not used in this backtest. |
| ClubElo | Date-matched Elo audit | Helper only. Not a training feature. |
| FBref | Website, no official API | Not scraped. |

## Verified walk-forward (2018-08-01 to 2026-09-07, refit every 7 days)

<!-- BACKTEST_RESULTS_START -->

| Season | Engine | N | Accuracy | RPS | Brier | Log loss | O/U 2.5 | BTTS | Double chance |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| POOLED | blend | 22147 | 51.3% | 0.2047 | 0.5959 | 0.9986 | 54.4% | 53.0% | 77.7% |
| POOLED | ML | 22147 | 52.3% | 0.2001 | 0.5861 | 0.9842 | N/A | N/A | 78.5% |
| POOLED | Dixon-Coles | 22147 | 49.4% | 0.2116 | 0.6112 | 1.0226 | 54.4% | 53.0% | 76.0% |
| 1819 | blend | 2744 | 50.8% | 0.2060 | 0.5966 | 1.0000 | 55.7% | 53.4% | 76.9% |
| 1920 | blend | 2569 | 51.0% | 0.2085 | 0.6014 | 1.0060 | 54.8% | 54.5% | 76.6% |
| 2021 | blend | 2858 | 50.3% | 0.2085 | 0.6079 | 1.0172 | 52.7% | 51.6% | 76.6% |
| 2122 | blend | 2818 | 50.4% | 0.2062 | 0.6004 | 1.0049 | 53.3% | 52.5% | 77.6% |
| 2223 | blend | 2780 | 53.3% | 0.2044 | 0.5867 | 0.9865 | 54.8% | 52.9% | 78.8% |
| 2324 | blend | 2744 | 52.1% | 0.1973 | 0.5848 | 0.9810 | 55.1% | 53.0% | 78.8% |
| 2425 | blend | 2706 | 51.9% | 0.2031 | 0.5917 | 0.9916 | 54.8% | 53.0% | 78.1% |
| 2526 | blend | 2670 | 51.0% | 0.2033 | 0.5983 | 1.0019 | 53.9% | 53.2% | 77.8% |
| 2627 | blend | 258 | 50.4% | 0.2044 | 0.5904 | 0.9898 | 58.1% | 57.4% | 77.1% |

Holdout inside `train.py` (last 180 days, N=953): blend 49.7%, ML 50.4%, Dixon-Coles 48.6%. Final fit rows: 10,769.

These numbers sit in the honest 50-53% band. They are not a 65% fantasy model.

<!-- BACKTEST_RESULTS_END -->

## Local run

```bash
cd soccer-predictor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/download_data.py
python scripts/backtest.py --start 2018-08-01 --end 2026-09-07 --refit-every 7
python scripts/train.py --cutoff 2026-09-07
python scripts/daily_job.py
```

GitHub secrets for the daily workflow: `ODDS_API_KEY` and `BBS_API_KEY` (mapped to `BBD_API_KEY`).

## AWS

`infra/template.yaml` is a separate SAM stack. Do not dispatch `.github/workflows/deploy.yml`.
