from __future__ import annotations
import copy,importlib.util
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('weather',ROOT/'scripts/mlb_pregame_weather_diagnostic.py');SUBJECT=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(SUBJECT)
NOW=datetime(2026,9,11,18,0,tzinfo=timezone.utc);START=datetime(2026,9,11,22,40,tzinfo=timezone.utc)
def game():return {'gamePk':824227,'gameDate':START.isoformat().replace('+00:00','Z'),'status':{'abstractGameState':'Preview'},'venue':{'id':2394,'name':'Comerica Park'}}
def venue():return {'venues':[{'id':2394,'name':'Comerica Park','location':{'defaultCoordinates':{'latitude':42.339,'longitude':-83.0485}}}]}
def weather():return {'hourly':{'time':['2026-09-11T22:00','2026-09-11T23:00'],'temperature_2m':[74,72],'precipitation_probability':[20,25],'wind_speed_10m':[9,8],'wind_direction_10m':[225,230]}}
def provider(g,w=None):
    def fetch(url,timeout):
        if '/schedule?' in url:return {'dates':[{'games':[g]}]}
        if '/venues/' in url:return venue()
        return w or weather()
    return fetch
def test_tbd_start_never_becomes_source_valid():
    g=game();g['status']['startTimeTBD']=True;r=SUBJECT.build_report(clock=lambda:NOW,fetch_json=provider(g));row=r['games'][0];assert row['sourceValid'] is False;assert row['failureStage']=='schedule_identity';assert row['weatherObservedAtUtc'] is None
def test_missing_precipitation_fails_closed():
    w=weather();w['hourly']['precipitation_probability']=[None,None];r=SUBJECT.build_report(clock=lambda:NOW,fetch_json=provider(game(),w));assert r['sourceValidGameCount']==0;assert 'required_weather_values_missing' in r['games'][0]['errors'][0]
def test_missing_wind_direction_fails_closed():
    w=weather();w['hourly']['wind_direction_10m']=[None,None];r=SUBJECT.build_report(clock=lambda:NOW,fetch_json=provider(game(),w));assert r['sourceValidGameCount']==0;assert 'required_weather_values_missing' in r['games'][0]['errors'][0]
