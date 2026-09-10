# soccer-predictor

Walk-forward soccer model living under `soccer-predictor/` in `KirtKurt/parlay-platform`.
This subtree is **not** the live Soccer AUTO / MLB / Tennis stack. Do not merge to `main` or run `Deploy SAM to AWS` until a real backtest table exists and a separate stack name `soccer-predictor` is approved.

Not betting advice.

## Data stack

| Source | Role |
| --- | --- |
| football-data.co.uk | Historical scores + closing 1X2. Required. No key. |
| BBD (`BBD_API_KEY`) | Optional live xG / lineups / injuries |
| The Odds API (`ODDS_API_KEY`) | Optional live + historical market / CLV |

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

Windows activate: `.venv\Scripts\activate`

Optional keys:

```bash
cp .env.example .env
```

Without keys the engine still trains on football-data.co.uk.

## What good looks like

- `data/raw/2526/E0.csv` exists after download
- `artifacts/backtest_by_season.csv` exists after backtest
- Pooled 1X2 accuracy about 50-55%, not 65%+
- Backtest takes minutes, not two seconds

<!-- BACKTEST_RESULTS_START -->

PLACEHOLDER: run `python scripts/backtest.py --start 2018-08-01 --end 2026-09-07 --refit-every 7` and the script will replace this block.

<!-- BACKTEST_RESULTS_END -->

## AWS

`infra/template.yaml` is a **separate** SAM stack: function `soccer-predictor-run`, bucket `soccer-predictor-<owner>-<region>`.
Do not reuse existing Soccer AUTO stack names. Do not dispatch `.github/workflows/deploy.yml` for this work.
