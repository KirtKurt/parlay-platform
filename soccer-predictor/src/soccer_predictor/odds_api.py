from __future__ import annotations
import json, logging, os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import numpy as np
import pandas as pd
from .io import TeamNames, day, write_json
from .odds import devig

LOG=logging.getLogger(__name__); BASE_URL="https://api.the-odds-api.com/v4"; SPORTS={"E0":"soccer_epl","UCL":"soccer_uefa_champs_league"}; BOOK_PRIORITY=("pinnacle","betfair_ex_eu","betfair","williamhill","william_hill"); HISTORICAL_START=day("2020-06-06")
class OddsAPIError(RuntimeError): pass

def _key(api_key=None): return api_key if api_key is not None else os.getenv("ODDS_API_KEY","")
def _request(path,params,api_key=None,timeout=20):
    key=_key(api_key)
    if not key: return None
    query=dict(params or {}); query["apiKey"]=key; url=f"{BASE_URL}{path}?{urlencode(query)}"; req=Request(url,headers={"User-Agent":"soccer-predictor/1.1","Accept":"application/json"})
    try:
        with urlopen(req,timeout=timeout) as response:
            body=response.read().decode("utf-8"); return json.loads(body),{"remaining":response.headers.get("x-requests-remaining"),"used":response.headers.get("x-requests-used"),"last":response.headers.get("x-requests-last")}
    except HTTPError as exc:
        detail=exc.read().decode("utf-8",errors="replace")[:500]; raise OddsAPIError(f"Odds API HTTP {exc.code}: {detail}") from exc
    except (URLError,TimeoutError,json.JSONDecodeError) as exc: raise OddsAPIError(f"Odds API request failed: {exc}") from exc
def list_sports(api_key=None):
    response=_request("/sports",{"all":"true"},api_key)
    if response is None: return []
    data,_=response; return data if isinstance(data,list) else []
def confirm_sport_keys(api_key=None,required=None):
    required=required or list(SPORTS.values())
    if not _key(api_key): return {sport:False for sport in required}
    keys={item.get("key") for item in list_sports(api_key)}; return {sport:sport in keys for sport in required}
def _live_request(sport,markets,api_key=None,regions="uk,eu"): return _request(f"/sports/{sport}/odds",{"regions":regions,"markets":markets,"oddsFormat":"decimal","dateFormat":"iso"},api_key)
def live_odds(sport,api_key=None,regions="uk,eu"):
    if not _key(api_key): LOG.info("ODDS_API_KEY unset; skipping The Odds API"); return {"events":[],"quota":{},"markets_requested":None,"skipped":True}
    try: response=_live_request(sport,"h2h,totals,btts",api_key,regions); markets="h2h,totals,btts"
    except OddsAPIError as exc: LOG.warning("BTTS market unavailable for %s (%s); retrying h2h,totals",sport,exc); response=_live_request(sport,"h2h,totals",api_key,regions); markets="h2h,totals"
    data,quota=response; return {"events":data if isinstance(data,list) else [],"quota":quota,"markets_requested":markets,"skipped":False}
def historical_day(sport,date,cache_root="data/odds_cache",api_key=None,regions="uk,eu"):
    d=day(date)
    if d<HISTORICAL_START: return None
    cache=Path(cache_root)/sport/f"{d.date().isoformat()}.json"
    if cache.exists(): return json.loads(cache.read_text(encoding="utf-8"))
    if not _key(api_key): return None
    snapshot=f"{d.date().isoformat()}T12:00:00Z"; response=_request(f"/historical/sports/{sport}/odds",{"regions":regions,"markets":"h2h,totals","oddsFormat":"decimal","dateFormat":"iso","date":snapshot},api_key); data,quota=response; envelope={"schema":1,"requested_at":snapshot,"sport":sport,"quota":quota,"response":data}; write_json(cache,envelope); return envelope
def _market(book,key):
    for market in book.get("markets",[]):
        if market.get("key")==key: return market
    return None
def _choose_book(event):
    books=event.get("bookmakers") or []; keyed={str(b.get("key","")).casefold():b for b in books}
    for key in BOOK_PRIORITY:
        if key in keyed: return keyed[key]
    for title in ("Pinnacle","Betfair Exchange","William Hill"):
        for book in books:
            if str(book.get("title","")).casefold()==title.casefold(): return book
    return books[0] if books else None
def _h2h_probs(event,names=None):
    names=names or TeamNames(); book=_choose_book(event)
    if not book: return None
    market=_market(book,"h2h")
    if not market: return None
    home,away=names.resolve(event.get("home_team")),names.resolve(event.get("away_team")); prices={}
    for outcome in market.get("outcomes",[]): prices[names.resolve(outcome.get("name"))]=outcome.get("price")
    draw_keys=[k for k in prices if str(k).casefold() in {"draw","tie"}]
    if home not in prices or away not in prices or not draw_keys: return None
    p=devig([prices[home],prices[draw_keys[0]],prices[away]])
    if p is None: return None
    return {"mkt_h":float(p[0]),"mkt_d":float(p[1]),"mkt_a":float(p[2]),"mkt_book":book.get("key") or book.get("title"),"mkt_last_update":book.get("last_update")}
def _ou25(event):
    book=_choose_book(event)
    if not book: return None
    market=_market(book,"totals")
    if not market: return None
    over=under=None
    for outcome in market.get("outcomes",[]):
        try: point=float(outcome.get("point"))
        except (TypeError,ValueError): continue
        if not np.isclose(point,2.5): continue
        name=str(outcome.get("name","")).casefold()
        if name=="over": over=outcome.get("price")
        elif name=="under": under=outcome.get("price")
    p=devig([over,under]); return None if p is None else float(p[0])
def event_market_features(event,names=None):
    values=_h2h_probs(event,names)
    if values is None: return None
    values["mkt_ou25"]=_ou25(event); values["odds_event_id"]=event.get("id"); values["commence_time"]=event.get("commence_time"); return values
def normalize_live_events(payload,names=None,div=None):
    names=names or TeamNames(); rows=[]
    for event in payload.get("events",[]):
        try:
            market=event_market_features(event,names); home,away=names.resolve(event.get("home_team")),names.resolve(event.get("away_team")); kickoff=pd.Timestamp(event.get("commence_time")); kickoff=kickoff.tz_localize("UTC") if kickoff.tzinfo is None else kickoff.tz_convert("UTC"); rows.append({"id":event.get("id"),"kickoff":kickoff.isoformat(),"date":day(kickoff),"home":home,"away":away,"div":div,**(market or {})})
        except (ValueError,TypeError): continue
    return rows
def historical_market_rows(envelope,names=None):
    if not envelope: return []
    response=envelope.get("response") or {}; events=response.get("data",[]) if isinstance(response,dict) else []; rows=normalize_live_events({"events":events},names); snapshot=response.get("timestamp") if isinstance(response,dict) else None
    for row in rows: row["market_snapshot_time"]=snapshot or envelope.get("requested_at")
    return rows
def _kickoff_utc_from_football_data(row):
    value=str(row.get("Time","")).strip()
    if not value: return None
    try: hh,mm=[int(x) for x in value.split(":",1)]; local=pd.Timestamp(row["date"]).tz_convert("Europe/London").normalize()+pd.Timedelta(hours=hh,minutes=mm); return local.tz_convert("UTC")
    except Exception: return None
def attach_historical_market(results,names,cache_root="data/odds_cache",api_key=None):
    frame=results.copy()
    for col in ["mkt_h","mkt_d","mkt_a","mkt_ou25","mkt_book","market_snapshot_time","market_feature_safe"]:
        if col not in frame.columns: frame[col]=np.nan if col.startswith("mkt_") else None
    if frame.empty: return frame
    epl=frame[(frame.div=="E0") & (frame.date>=HISTORICAL_START)]
    for date in sorted(epl.date.unique()):
        envelope=historical_day(SPORTS["E0"],date,cache_root,api_key)
        if not envelope: continue
        by_match={(day(r["date"]),r["home"],r["away"]):r for r in historical_market_rows(envelope,names)}; mask=(frame.div=="E0") & (frame.date==date)
        for idx in frame.index[mask]:
            key=(day(frame.at[idx,"date"]),frame.at[idx,"home"],frame.at[idx,"away"]); market=by_match.get(key)
            if not market: continue
            for col in ["mkt_h","mkt_d","mkt_a","mkt_ou25","mkt_book","market_snapshot_time"]: frame.at[idx,col]=market.get(col)
            kickoff=_kickoff_utc_from_football_data(frame.loc[idx]); snapshot=pd.Timestamp(market.get("market_snapshot_time")) if market.get("market_snapshot_time") else None
            if snapshot is not None and snapshot.tzinfo is None: snapshot=snapshot.tz_localize("UTC")
            frame.at[idx,"market_feature_safe"]=bool(kickoff is not None and snapshot is not None and snapshot<kickoff)
    return frame
