from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import json
import pandas as pd
from .io import day, match_id, sha, csv_records, parse_date
from .network import fetch_bytes


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href=dict(attrs).get("href")
            if href: self.links.append(href)

def discover_feeds(season_start, fetch=fetch_bytes):
    root="https://fixturedownload.com"; pending=[root+"/sport/football"]; visited=set(); candidates=set()
    while pending and len(visited)<12:
        url=pending.pop(0)
        if url in visited: continue
        visited.add(url); parser=Links(); parser.feed(fetch(url).decode("utf-8"))
        for href in parser.links:
            link=urljoin(root,href); parsed=urlparse(link)
            if parsed.hostname not in {"fixturedownload.com","www.fixturedownload.com"}: continue
            if parsed.path.startswith("/feed/json/"): candidates.add(link)
            elif parsed.path.startswith("/sport/football") and link not in visited and link not in pending: pending.append(link)
    result={}
    for div,tokens in [("E0",("epl","english-premier-league")),("UCL",("uefa-champions-league","champions-league"))]:
        valid=[]
        for url in candidates:
            slug=urlparse(url).path.rsplit("/",1)[-1].lower()
            if any(slug.startswith(t+"-") for t in tokens) and str(season_start) in slug and not any(t in slug for t in ["women","youth","qualif"]): valid.append(url)
        if not valid: raise ValueError(f"No published {div} JSON fixture feed discovered for season {season_start}; supply verified fixture CSV")
        result[div]=sorted(valid,key=len)[0]
    return result

def utc_kickoff(value, provider_utc=False):
    t=pd.Timestamp(value)
    if pd.isna(t): raise ValueError("Missing fixture kickoff")
    if t.tzinfo is None:
        if not provider_utc: raise ValueError("Operator kickoff must include Z or UTC offset")
        t=t.tz_localize("UTC")
    return t.tz_convert("UTC")

def normalize_fixture(row, div, source, names, provider=True):
    kickoff=utc_kickoff(row["DateUtc"] if provider else row["kickoff"],provider_utc=provider); home=names.resolve(row["HomeTeam"] if provider else row["home"]); away=names.resolve(row["AwayTeam"] if provider else row["away"])
    if home==away: raise ValueError("Fixture team names are identical")
    return {"match_id":match_id(div,kickoff,home,away),"kickoff":kickoff.isoformat(),"div":div,"home":home,"away":away,"source":source}

def fetch_fixtures(first,last,names,fetch=fetch_bytes,now=None):
    first,last=day(first),day(last)
    if first>last: raise ValueError("Fixture date range is reversed")
    season_start=first.year if first.month>=7 else first.year-1; last_season=last.year if last.month>=7 else last.year-1
    if season_start!=last_season: raise ValueError("Split fixture requests at July 1 season boundary")
    feeds=discover_feeds(season_start,fetch); fixtures=[]; statuses={}
    for div,url in feeds.items():
        body=fetch(url); rows=json.loads(body)
        if not isinstance(rows,list) or not rows: raise ValueError(f"Empty or invalid fixture feed: {div}")
        count=0
        for row in rows:
            if row.get("HomeTeamScore") is not None or row.get("AwayTeamScore") is not None: continue
            item=normalize_fixture(row,div,url,names)
            if first<=day(item["kickoff"])<=last: fixtures.append(item); count+=1
        statuses[div]={"ok":True,"url":url,"sha256":sha(body),"feed_rows":len(rows),"window_rows":count}
    try:
        odds_url="https://www.football-data.co.uk/fixtures.csv"; index={}
        for row in csv_records(fetch(odds_url)):
            div=row.get("Div")
            if div not in {"E0","UCL"}: continue
            mid=match_id(div,parse_date(row["Date"]),names.resolve(row["HomeTeam"]),names.resolve(row["AwayTeam"])); index[mid]=row
        for fixture in fixtures: fixture["odds"]=index.get(fixture["match_id"],{})
        statuses["odds"]={"ok":True,"url":odds_url}
    except (RuntimeError,ValueError,KeyError,OSError) as exc: statuses["odds"]={"ok":False,"reason":str(exc)}
    generated=pd.Timestamp(now or datetime.now(timezone.utc)).isoformat()
    return {"schema":1,"fetched_at":generated,"coverage_from":first.isoformat(),"coverage_to":last.isoformat(),"source_status":statuses,"fixtures":sorted(fixtures,key=lambda r:(r["kickoff"],r["div"],r["home"]))}

def operator_fixtures(body,first,last,names,now=None):
    first,last=day(first),day(last); fixtures=[]
    for row in csv_records(body):
        if not all(row.get(k) for k in ["kickoff","div","home","away","source"]): raise ValueError("Operator fixture CSV requires kickoff,div,home,away,source")
        item=normalize_fixture(row,row["div"],row["source"],names,provider=False)
        if first<=day(item["kickoff"])<=last: item["odds"]=row; fixtures.append(item)
    return {"schema":1,"fetched_at":pd.Timestamp(now or datetime.now(timezone.utc)).isoformat(),"coverage_from":first.isoformat(),"coverage_to":last.isoformat(),"source_status":{"operator_supplied":{"ok":True,"sha256":sha(body)}},"fixtures":fixtures}
