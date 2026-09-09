"""Read-only current pregame source proof; never backfills a locked snapshot."""
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'hello_world'))
import mlb_advanced_context as advanced
import mlb_statsapi_team_context as source


def main():
    now=datetime.now(timezone.utc);day=now.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    schedule=advanced._statsapi_schedule(day);history=advanced._statsapi_schedule_history(day)
    if not schedule.get('ok') or not history.get('ok'):raise RuntimeError('official schedule unavailable')
    started=time.monotonic();rows=[]
    for game in advanced._schedule_games(schedule):
        if game.get('status',{}).get('abstractGameState')!='Preview' or source._time(game.get('gameDate'))-timedelta(minutes=45)<=now:
            continue
        at=time.monotonic();a,b=source.observe(day,game,history,advanced._http_get_json)
        rows.append({'officialGamePk':game['gamePk'],'elapsedSeconds':round(time.monotonic()-at,3),'lineup':a,'bullpen':b})
    report={'readOnly':True,'createdAtUtc':datetime.now(timezone.utc).isoformat(),'version':source.VERSION,
            'elapsedSeconds':round(time.monotonic()-started,3),'eligibleGames':len(rows),
            'gamesWithBothLineups':sum(all(r['lineup'].get(s+'_lineup_confirmed') is True for s in ('home','away')) for r in rows),
            'gamesWithBothWorkloads':sum(all(r['bullpen'].get(s+'_reliever_usage_1d_3d_5d') is not None for s in ('home','away')) for r in rows),
            'rows':rows}
    output=ROOT/'runtime_reports/mlb_team_context_live_proof_latest.json';output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}))
    if rows and not report['gamesWithBothWorkloads']:raise RuntimeError('no complete observed relief workload')
    if report['elapsedSeconds']>90:raise RuntimeError('supplemental source proof exceeds 90 second slate budget')

if __name__=='__main__':main()
