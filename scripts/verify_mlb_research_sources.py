"""Public-source proof for every currently eligible pregame, without AWS writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mlb_research'))
from mlb_research_store_v1 import now,digest,utc
import mlb_research_sources_v1 as source
import mlb_player_windows_v1 as players


def main():
    at=now();day=at.astimezone(source.ET).date()
    games,_=source.schedule(str(day-timedelta(days=30)),str(day))
    cache=Path('/tmp/mlb-source-verification');cache.mkdir(exist_ok=True)
    def read(game):
        try:
            path=cache/(str(game['gamePk'])+'.json')
            if path.exists():return json.loads(path.read_text()),None
            value=source.final_source(game);path.write_text(json.dumps(value));return value,None
        except Exception as exc:return None,{'gamePk':game['gamePk'],'error':type(exc).__name__}
    completed={};errors=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for value,error in pool.map(read,[g for g in games if source.final(g)]):
            if error:errors.append(error)
            else:completed[value['officialGamePk']]=value
            if (len(completed)+len(errors))%100==0:
                print(json.dumps({'sourceGames':len(completed),'sourceFailures':len(errors)}),flush=True)
    current,_=source.schedule(str(day));proof=[]
    eligible=[g for g in current if g['status']['abstractGameState']=='Preview' and now()<utc(g['gameDate'])-timedelta(minutes=10)]
    for game in eligible:
        payload,_=source.feed(game)
        observation=players.observe(game,payload,completed,now())
        details=[]
        for side,team in observation['teams'].items():
            pitchers=[p for p in team['players'] if p['pitcher']]
            missing=[{'id':p['id'],'name':p['name']} for p in pitchers if p['pitching']['30d']['status']=='INCOMPLETE']
            lineup=[p for p in team['players'] if p['lineupSlot'] is not None]
            incomplete_batters=[{'id':p['id'],'name':p['name']} for p in lineup if any(p['hitting'][str(n)+'d']['status']=='INCOMPLETE' for n in (7,15,30))]
            details.append({'side':side,'roster':team['activeRosterCount'],'pitchers':len(pitchers),
                            'lineupBatters':sum(p['lineupSlot'] is not None for p in team['players']),
                            'incompletePitcherWindows':missing,'incompleteLineupBatterWindows':incomplete_batters,
                            'pitcherWindowRates':[{'id':p['id'],'name':p['name'],
                                'windows':{str(n)+'d':{'status':p['pitching'][str(n)+'d']['status'],
                                    'rates':p['pitching'][str(n)+'d']['rates']} for n in (7,15,30)}} for p in pitchers],
                            'lineupWindowRates':[{'id':p['id'],'name':p['name'],'slot':p['lineupSlot'],
                                'windows':{str(n)+'d':{'status':p['hitting'][str(n)+'d']['status'],
                                    'rates':p['hitting'][str(n)+'d']['rates']} for n in (7,15,30)}} for p in lineup]})
        proof.append({'gamePk':game['gamePk'],'teams':details,'observationFingerprint':digest(observation)})
        print(json.dumps({'gamePk':game['gamePk'],'teams':[{k:v for k,v in d.items() if not k.endswith('WindowRates')} for d in details]}),flush=True)
    report={'updatedAtUtc':now().isoformat(),'eligibleGames':len(eligible),'verifiedGames':len(proof),
            'priorGames':len(completed),'sourceErrors':errors,'games':proof,'productionWrites':False}
    (ROOT/'runtime_reports/mlb_research_source_summary_20260909.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='games'},indent=2))


if __name__=='__main__':main()
