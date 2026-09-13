#!/usr/bin/env python3
"""Read-only MLB pregame weather source diagnostic."""
from __future__ import annotations
import argparse,hashlib,json,math,urllib.parse,urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Callable,Mapping,Optional,Sequence
from zoneinfo import ZoneInfo
VERSION="MLB-PREGAME-WEATHER-DIAGNOSTIC-v4-main-pinned-complete-observation"
REPORT_TYPE="MLB_PREGAME_WEATHER_READ_ONLY_DIAGNOSTIC";ET=ZoneInfo("America/New_York")
SCHEDULE_URL="https://statsapi.mlb.com/api/v1/schedule";VENUE_URL="https://statsapi.mlb.com/api/v1/venues/{venue_id}";OPEN_METEO_URL="https://api.open-meteo.com/v1/forecast"
HOURLY_VARIABLES=("temperature_2m","precipitation_probability","wind_speed_10m","wind_direction_10m")
def _http_json(url,timeout=8):
    req=urllib.request.Request(url,headers={"accept":"application/json","user-agent":"inqsi-mlb-weather-diagnostic/1.0"})
    with urllib.request.urlopen(req,timeout=timeout) as r:p=json.loads(r.read().decode())
    if not isinstance(p,dict):raise ValueError("provider response must be an object")
    return p
def _parse_dt(v):
    try:
        d=datetime.fromisoformat(str(v).replace("Z","+00:00"));d=d if d.tzinfo else d.replace(tzinfo=timezone.utc);return d.astimezone(timezone.utc)
    except Exception:return None
def _finite(v):
    if isinstance(v,bool):return None
    try:n=float(v)
    except (TypeError,ValueError,OverflowError):return None
    return n if math.isfinite(n) else None
def _positive_id(v):
    n=_finite(v);return int(n) if n is not None and n>0 and n.is_integer() else None
def _fingerprint(p):return hashlib.sha256(json.dumps(p,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def _venue_identity(game):
    v=game.get("venue") if isinstance(game.get("venue"),Mapping) else {};return _positive_id(v.get("id")),str(v.get("name") or "") or None
def _venue_coordinates(payload,expected_id):
    venues=payload.get("venues")
    if not isinstance(venues,list) or len(venues)!=1 or not isinstance(venues[0],Mapping):raise ValueError("venue_response_not_unique")
    v=venues[0]
    if _positive_id(v.get("id"))!=expected_id:raise ValueError("venue_identity_mismatch")
    loc=v.get("location") if isinstance(v.get("location"),Mapping) else {};coords=loc.get("defaultCoordinates") if isinstance(loc.get("defaultCoordinates"),Mapping) else {}
    lat,lon=_finite(coords.get("latitude")),_finite(coords.get("longitude"))
    if lat is None or lon is None or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError("venue_coordinates_missing_or_invalid")
    return lat,lon,str(v.get("name") or "")
def _weather_row(payload,target):
    hourly=payload.get("hourly") if isinstance(payload.get("hourly"),Mapping) else {};raw=hourly.get("time")
    if not isinstance(raw,list) or not raw:raise ValueError("weather_hourly_times_missing")
    times=[_parse_dt(v) for v in raw];cand=[(abs((v-target).total_seconds()),i,v) for i,v in enumerate(times) if v]
    if not cand:raise ValueError("weather_hourly_times_invalid")
    distance,index,hour=min(cand)
    if distance>3600:raise ValueError("weather_hour_not_near_game_start")
    vals={}
    for name in HOURLY_VARIABLES:
        arr=hourly.get(name);vals[name]=arr[index] if isinstance(arr,list) and index<len(arr) else None
    temp,precip,wind,direction=(_finite(vals[k]) for k in HOURLY_VARIABLES)
    if any(v is None for v in (temp,precip,wind,direction)):raise ValueError("required_weather_values_missing")
    if not 0<=precip<=100:raise ValueError("precipitation_probability_invalid")
    if wind<0 or not 0<=direction<=360:raise ValueError("wind_values_invalid")
    return {"forecastTimeUtc":hour.isoformat().replace("+00:00","Z"),"temperatureF":temp,"precipitationRiskPct":precip,"windSpeedMph":wind,"windDirectionDegrees":direction}
def diagnose_game(game:Mapping[str,Any],*,clock:Callable[[],datetime],fetch_json:Callable[[str,int],dict[str,Any]]):
    start=_parse_dt(game.get("gameDate"));game_pk=_positive_id(game.get("gamePk"));status_obj=game.get("status") if isinstance(game.get("status"),Mapping) else {};status=str(status_obj.get("abstractGameState") or "");tbd=status_obj.get("startTimeTBD") is True;venue_id,schedule_name=_venue_identity(game)
    base={"gamePk":game_pk,"commenceTimeUtc":start.isoformat().replace("+00:00","Z") if start else None,"scheduleStatus":status,"venueId":venue_id,"venueName":schedule_name,"venueObservedAtUtc":None,"weatherRequestAtUtc":None,"weatherObservedAtUtc":None,"failureStage":None,"preT45":False,"sourceValid":False,"errors":[],"weather":None,"roofStatus":None,"roofStatusClaimed":False,"canCompleteWeatherRoofGroup":False,"canChangeProductionScoring":False}
    cutoff=start-timedelta(minutes=45) if start else None
    if not game_pk or start is None or status!="Preview" or tbd or not venue_id:
        base["errors"]=["official_game_or_venue_identity_incomplete"];base["failureStage"]="schedule_identity";return base
    if clock().astimezone(timezone.utc)>=cutoff:base["errors"]=["game_not_pre_t45_at_probe_start"];base["failureStage"]="probe_start";return base
    venue_url=VENUE_URL.format(venue_id=venue_id)+"?"+urllib.parse.urlencode({"hydrate":"location,fieldInfo"})
    try:venue_payload=fetch_json(venue_url,8)
    except Exception as exc:base["errors"]=[f"venue_source_failed:{type(exc).__name__}:{exc}"];base["failureStage"]="venue_fetch";return base
    venue_received=clock().astimezone(timezone.utc);base["venueObservedAtUtc"]=venue_received.isoformat().replace("+00:00","Z")
    if venue_received>=cutoff:base["errors"]=["venue_response_received_at_or_after_t45"];base["failureStage"]="venue_chronology";return base
    try:
        lat,lon,official_name=_venue_coordinates(venue_payload,venue_id)
        if schedule_name and official_name and schedule_name!=official_name:raise ValueError("venue_name_mismatch")
    except Exception as exc:base["errors"]=[f"venue_validation_failed:{type(exc).__name__}:{exc}"];base["failureStage"]="venue_validation";return base
    params={"latitude":round(lat,6),"longitude":round(lon,6),"hourly":",".join(HOURLY_VARIABLES),"temperature_unit":"fahrenheit","wind_speed_unit":"mph","timezone":"UTC","start_date":start.date().isoformat(),"end_date":start.date().isoformat()};weather_url=OPEN_METEO_URL+"?"+urllib.parse.urlencode(params)
    request_at=clock().astimezone(timezone.utc);base["weatherRequestAtUtc"]=request_at.isoformat().replace("+00:00","Z")
    if request_at>=cutoff:base["errors"]=["weather_request_would_start_at_or_after_t45"];base["failureStage"]="weather_request_chronology";return base
    try:weather_payload=fetch_json(weather_url,8)
    except Exception as exc:base["errors"]=[f"weather_source_failed:{type(exc).__name__}:{exc}"];base["failureStage"]="weather_fetch";return base
    received=clock().astimezone(timezone.utc);base["weatherObservedAtUtc"]=received.isoformat().replace("+00:00","Z");base["preT45"]=received<cutoff
    if not base["preT45"]:base["errors"]=["weather_response_received_at_or_after_t45"];base["failureStage"]="weather_chronology";return base
    try:weather=_weather_row(weather_payload,start)
    except Exception as exc:base["errors"]=[f"weather_validation_failed:{type(exc).__name__}:{exc}"];base["failureStage"]="weather_validation";return base
    base.update({"sourceValid":True,"errors":[],"failureStage":None,"venueName":official_name or schedule_name,"weather":weather,"sourceProvenance":{"provider":"Open-Meteo forecast + MLB Stats API venue identity","retrievedAtUtc":base["weatherObservedAtUtc"],"sourceEffectiveAtUtc":weather["forecastTimeUtc"],"venueEndpoint":venue_url,"weatherEndpoint":weather_url,"venuePayloadFingerprint":_fingerprint(venue_payload),"weatherPayloadFingerprint":_fingerprint(weather_payload)}});return base
def build_report(*,clock=None,fetch_json=_http_json):
    clock=clock or (lambda:datetime.now(timezone.utc));created=clock().astimezone(timezone.utc);slate=created.astimezone(ET).date().isoformat();schedule_url=SCHEDULE_URL+"?"+urllib.parse.urlencode({"sportId":1,"date":slate,"hydrate":"venue"});schedule=fetch_json(schedule_url,8)
    games=[g for day in schedule.get("dates") or [] if isinstance(day,Mapping) for g in day.get("games") or [] if isinstance(g,Mapping) and str((g.get("status") or {}).get("abstractGameState") or "")=="Preview"]
    rows=[diagnose_game(g,clock=clock,fetch_json=fetch_json) for g in games]
    return {"ok":True,"version":VERSION,"reportType":REPORT_TYPE,"createdAtUtc":created.isoformat().replace("+00:00","Z"),"slateDateEt":slate,"readOnly":True,"previewGameCount":len(rows),"preT45ProbeGameCount":sum(r.get("preT45") is True for r in rows),"sourceValidGameCount":sum(r.get("sourceValid") is True for r in rows),"roofStatusClaimCount":0,"weatherRoofCompletenessChanged":False,"productionAuthorityChanged":False,"automaticWagerAllowed":False,"recommendation":"Capture source-valid temperature/wind/precipitation prospectively; keep roof status unknown until independently sourced.","games":rows,"sourceOfTruth":{"scheduleEndpoint":schedule_url,"venueEndpointTemplate":VENUE_URL,"weatherProvider":OPEN_METEO_URL}}
def main(argv:Optional[Sequence[str]]=None):
    p=argparse.ArgumentParser();p.add_argument('--output',default='runtime_reports/mlb_pregame_weather_diagnostic_latest.json');a=p.parse_args(argv);report=build_report();path=Path(a.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps({k:report[k] for k in ('previewGameCount','preT45ProbeGameCount','sourceValidGameCount','roofStatusClaimCount')}));return 0
if __name__=='__main__':raise SystemExit(main())
