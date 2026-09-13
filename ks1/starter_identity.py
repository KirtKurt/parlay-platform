"""Pregame starter IDs from already published KS1 prediction rows.

Outcomes and p_home from those files are ignored so this cannot leak labels.
Only rows stored at or before T-10 are admitted.
"""
from datetime import timedelta

from ks1.features import utc


def published_starter_index(rows):
    latest = {}
    for row in rows or []:
        pk = str(row.get("game_id") or "")
        as_of, start = row.get("as_of"), row.get("commence_time")
        if not pk or not as_of or not start:
            continue
        if utc(as_of) > utc(start) - timedelta(minutes=10):
            continue
        entry = {"as_of": as_of, "source": row.get("source_key") or "published_predictions"}
        for side in ("home", "away"):
            pid = row.get(side + "_starter_id")
            if pid in (None, "", "None"):
                continue
            entry[side] = {"id": str(pid), "name": row.get(side + "_starter_name")}
        if "home" in entry or "away" in entry:
            prev = latest.get(pk)
            if not prev or utc(as_of) >= utc(prev["as_of"]):
                latest[pk] = entry
    return latest
