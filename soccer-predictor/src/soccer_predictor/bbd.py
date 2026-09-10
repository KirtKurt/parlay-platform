from __future__ import annotations
import json, logging, os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import numpy as np
import pandas as pd
from .io import TeamNames, day

LOG=logging.getLogger(__name__); BASE_URL="https://api.bigballsdata.com"; _WARNED=set()
class BBDError(RuntimeError): pass

def _key(api_key=None): return api_key if api_key is not None else os.getenv("BBD_API_KEY","")
def _request(path,params=None,api_key=None,timeout=20,optional=False):
    key=_key(api_key)
    if not key: return None
    url=f"{BASE_URL}{path}" + (("?"+urlencode(params)) if params else "")
    req=Request(url,headers={"Authorization":f"Bearer {key}","Accept":"application/json","User-Agent":"soccer-predictor/1.1"})
    try:
        with urlopen(req,timeout=timeout) as response: return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if optional and exc.code in {401,403,404}:
            token=(path,exc.code)
            if token not in _WARNED: LOG.warning("Optional BBD endpoint unavailable (%s HTTP %s); skipping",path,exc.code); _WARNED.add(token)
            return None
        detail=exc.read().decode("utf-8",errors="replace")[:500]; raise BBDError(f"BBD HTTP {exc.code}: {detail}") from exc
    except (URLError,TimeoutError,json.JSONDecodeError) as exc: raise BBDError(f"BBD request failed: {exc}") from exc

def _items(payload):
    if payload is None: return []
    if isinstance(payload,list): return payload
    if isinstance(payload,dict):
        for key in ("data","matches","results"):
            value=payload.get(key)
            if isinstance(value,list): return value
        if isinstance(payload.get("data"),dict):
            for key in ("matches","results"):
                value=payload["data"].get(key)
                if isinstance(value,list): return value
    return []
def matches(league,api_key=None):
    if not _key(api_key): LOG.info("BBD_API_KEY unset; skipping BBD"); return []
    try: return _items(_request("/v1/matches",{"sport":"football","league":league},api_key))
    except BBDError: return _items(_request("/v1/matches",{"league":league},api_key))
def match_statistics(match_id,api_key=None):
    for path in (f"/v1/stored/matches/{match_id}/stats",f"/v1/matches/{match_id}/statistics"):
        payload=_request(path,api_key=api_key,optional=True)
        if payload is not None: return payload
    return None
def injuries(league,api_key=None): return _request("/v1/injuries",{"sport":"football","league":league},api_key,optional=True)
def _team_name(value): return (value.get("name") or value.get("short_name") or value.get("title")) if isinstance(value,dict) else value
def _nested_number(obj,*paths):
    for path in paths:
        current=obj
        try:
            for key in path: current=current[key]
            value=float(current)
            if np.isfinite(value): return value
        except (KeyError,TypeError,ValueError): continue
    return np.nan
def _xg_pair(stats):
    if stats is None: return np.nan,np.nan
    data=stats.get("data",stats) if isinstance(stats,dict) else stats
    home=_nested_number(data,("home","xg"),("home","expected_goals"),("home_xg",),("xg_home",)); away=_nested_number(data,("away","xg"),("away","expected_goals"),("away_xg",),("xg_away",))
    if (not np.isfinite(home) or not np.isfinite(away)) and isinstance(data,dict):
        items=data.get("statistics") or data.get("stats")
        if isinstance(items,list):
            for item in items:
                name=str(item.get("name") or item.get("type") or "").casefold().replace(" ","_")
                if name in {"xg","expected_goals","expected_goals_(xg)"}: home=_nested_number(item,("home",),("home_value",)); away=_nested_number(item,("away",),("away_value",)); break
    return home,away
def _lineup_flags(match):
    lineups=match.get("lineups") or match.get("lineup") or {}
    if isinstance(lineups,dict): return float(bool(lineups.get("home") or lineups.get("home_team"))),float(bool(lineups.get("away") or lineups.get("away_team")))
    if isinstance(lineups,list): return float(len(lineups)>0),float(len(lineups)>0)
    return np.nan,np.nan
def normalize_fixture(match,league,names=None,api_key=None):
    names=names or TeamNames(); home_raw=_team_name(match.get("home") or match.get("home_team")); away_raw=_team_name(match.get("away") or match.get("away_team"))
    if not home_raw or not away_raw: return None
    home,away=names.resolve(home_raw),names.resolve(away_raw); kickoff_raw=match.get("kickoff") or match.get("commence_time") or match.get("start_time") or match.get("date"); kickoff=pd.Timestamp(kickoff_raw); kickoff=kickoff.tz_localize("UTC") if kickoff.tzinfo is None else kickoff.tz_convert("UTC"); mid=str(match.get("id") or match.get("match_id") or "")
    xgh,xga=_xg_pair(match_statistics(mid,api_key) if mid else None); lh,la=_lineup_flags(match)
    return {"bbd_match_id":mid or None,"kickoff":kickoff.isoformat(),"date":day(kickoff),"home":home,"away":away,"league":league,"xg_h_bbd":xgh,"xg_a_bbd":xga,"lineup_h_bbd":lh,"lineup_a_bbd":la}
def injury_counts(payload,names=None):
    names=names or TeamNames(); counts={}
    for item in _items(payload):
        raw=_team_name(item.get("team") or item.get("club") or item.get("team_name"))
        if not raw: continue
        try: team=names.resolve(raw)
        except ValueError: continue
        counts[team]=counts.get(team,0)+1
    return counts
def weekly_card(names=None,api_key=None):
    if not _key(api_key): return []
    out=[]
    for league in ("epl","ucl"):
        injury_map=injury_counts(injuries(league,api_key),names)
        for match in matches(league,api_key):
            try: row=normalize_fixture(match,league,names,api_key)
            except (ValueError,BBDError,TypeError): row=None
            if row: row["injuries_h_bbd"]=float(injury_map.get(row["home"],0)); row["injuries_a_bbd"]=float(injury_map.get(row["away"],0)); out.append(row)
    return out
