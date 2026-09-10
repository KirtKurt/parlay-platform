from __future__ import annotations
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import unicodedata
import numpy as np
import pandas as pd
from .settings import sources


def day(value) -> pd.Timestamp:
    t = pd.Timestamp(value)
    if pd.isna(t): raise ValueError("Missing date")
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).normalize()

def stamp(value) -> str: return day(value).isoformat()

def clean(value):
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(v) for v in value]
    if isinstance(value, np.ndarray): return clean(value.tolist())
    if isinstance(value, np.generic): return clean(value.item())
    if isinstance(value, pd.Timestamp): return value.isoformat()
    if value is pd.NA or value is pd.NaT: return None
    if isinstance(value, float) and not np.isfinite(value): return None
    return value

def json_bytes(value) -> bytes:
    return json.dumps(clean(value), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")

def atomic_bytes(path, body: bytes):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_name(path.name + f".{os.getpid()}.tmp"); tmp.write_bytes(body); os.replace(tmp, path)

def write_json(path, value): atomic_bytes(path, json_bytes(value))
def sha(body: bytes) -> str: return hashlib.sha256(body).hexdigest()

class TeamNames:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}; self.lookup = {}
        for canonical, aliases in self.mapping.items():
            for name in [canonical, *aliases]:
                key = self.key(name)
                if key in self.lookup and self.lookup[key] != canonical: raise ValueError(f"Conflicting team alias: {name}")
                self.lookup[key] = canonical
    @staticmethod
    def key(name):
        text = str(name).replace("ø", "o").replace("Ø", "O"); text = unicodedata.normalize("NFKD", text).casefold()
        return "".join(c for c in text if c.isalnum() and not unicodedata.combining(c))
    def resolve(self, name):
        raw = str(name).strip()
        if self.key(raw) in {"", "nan", "none", "tbd", "tba", "tbc", "unknown"} or re.search(r"\b(winner|loser)\s+(of|match)\b", raw, re.I): raise ValueError(f"Unresolved team: {raw!r}")
        return self.lookup.get(self.key(raw), raw)
    @classmethod
    def load(cls, path): return cls(json.loads(Path(path).read_text(encoding="utf-8")))

def read_config(path): return json.loads(Path(path).read_text(encoding="utf-8"))

def csv_records(body: bytes):
    try: text = body.decode("utf-8-sig")
    except UnicodeDecodeError: text = body.decode("cp1252")
    if text.lstrip().startswith("<"): raise ValueError("Expected CSV, received HTML")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames: raise ValueError("CSV has no header")
    rows=[]
    for row in reader:
        if None in row and any(str(v).strip() for v in row[None]): raise ValueError("CSV row has unexpected nonempty columns")
        item={str(k).strip():(str(v).strip() if v is not None else "") for k,v in row.items() if k is not None}
        if any(item.values()): rows.append(item)
    return rows

def match_id(div, date, home, away): return sha(f"{div}|{day(date).date()}|{home}|{away}".encode())[:24]

def parse_date(value):
    value=str(value).strip()
    if re.match(r"^\d{4}-", value): return day(value)
    return day(pd.to_datetime(value, dayfirst=True, errors="raise"))

def normalize_results(rows, names, division=None, season=None):
    out=[]
    for r in rows:
        if not all(k in r for k in ["Date","HomeTeam","AwayTeam","FTHG","FTAG"]): raise ValueError("Result CSV lacks Date/HomeTeam/AwayTeam/FTHG/FTAG")
        if r["FTHG"]=="" and r["FTAG"]=="": continue
        goals=[]
        for k in ["FTHG","FTAG"]:
            g=float(r[k])
            if not np.isfinite(g) or g != int(g) or not 0 <= g <= 50: raise ValueError(f"Invalid final goals: {r[k]}")
            goals.append(int(g))
        hg,ag=goals; result="H" if hg>ag else "A" if hg<ag else "D"
        if r.get("FTR") and r["FTR"] != result: raise ValueError("Final score conflicts with result label")
        d=parse_date(r["Date"]); h,a=names.resolve(r["HomeTeam"]),names.resolve(r["AwayTeam"])
        if h==a: raise ValueError("A team cannot play itself")
        div=division or r.get("Div")
        if not div: raise ValueError("Missing competition")
        out.append({**r,"date":d,"div":div,"season":season or str(d.year),"home":h,"away":a,"hg":hg,"ag":ag,"y":{"H":0,"D":1,"A":2}[result],"match_id":match_id(div,d,h,a)})
    return out

def load_history(root, names, allow_partial=False):
    rows,manifest,missing=[],[],[]
    for season,div in sources():
        path=Path(root)/season/f"{div}.csv"
        if not path.exists(): missing.append(str(path)); continue
        body=path.read_bytes(); parsed=normalize_results(csv_records(body),names,div,season); rows.extend(parsed); manifest.append({"season":season,"division":div,"sha256":sha(body),"rows":len(parsed)})
    if missing and not allow_partial: raise ValueError(f"Missing {len(missing)} historical files; run the downloader. First: {missing[0]}")
    if not rows: raise ValueError("No completed matches were loaded")
    df=pd.DataFrame(rows).sort_values(["date","div","home","away"],kind="stable")
    for _,group in df.groupby("match_id"):
        if len(group[["hg","ag"]].drop_duplicates())>1: raise ValueError("Conflicting duplicate result")
    df=df.drop_duplicates("match_id",keep="last").reset_index(drop=True)
    players=pd.concat([df[["date","home"]].rename(columns={"home":"team"}),df[["date","away"]].rename(columns={"away":"team"})])
    if players.duplicated(["date","team"]).any(): raise ValueError("A club has multiple completed matches on the same date")
    provenance={"partial_history":bool(missing),"missing":missing,"files":manifest,"fingerprint":sha(json_bytes(manifest)),"completed_rows":len(df)}
    return df,provenance
