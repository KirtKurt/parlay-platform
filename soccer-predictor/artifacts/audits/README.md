# Daily soccer audits

Runs at 1:30 AM America/New_York against every stored card whose kickoff fell on the prior local day.

Markets graded: 1X2 pick, double chance, BTTS, O/U 2.5, exact score.
Results source: football-data.co.uk after `download_data.py`.

Files:
- `YYYY-MM-DD.md` human report
- `YYYY-MM-DD.csv` row-level hits
- `ledger.csv` running history
- `latest.json` machine summary
