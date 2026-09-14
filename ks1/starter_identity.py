"""Fixture-bound starter IDs from immutable, pre-T-10 KS1 publications.

Accept the version-proven entries from sources.load_existing, never raw rows.
The table's existing historical_starters bridge remains the canonical reader.
"""
from ks1.features import utc
from ks1.historical_starters import published_starter_index as _proven_index


def published_starter_index(entries, *, fixtures):
    """Return the latest proven identity matching each target date and start.

    Fixtures require game_id, date and commence_time. Requiring the target
    prevents an identity from an earlier schedule being joined by game ID alone.
    """
    targets = {}
    for fixture in fixtures:
        pk = str(fixture["game_id"])
        if pk in targets:
            raise ValueError("duplicate target fixture game_id")
        targets[pk] = (fixture["date"], utc(fixture["commence_time"]))
    matching = []
    for entry in entries or []:
        row = entry.get("row") or {}
        pk = str(row.get("game_id") or "")
        if pk not in targets or not row.get("commence_time"):
            continue
        if (row.get("date"), utc(row["commence_time"])) == targets[pk]:
            matching.append(entry)
    return {
        pk: {"game_id": pk, "date": targets[pk][0],
             "commence_time": value["commence_time"], "as_of": value["as_of"],
             "source": value["source"], **value["sides"]}
        for pk, value in _proven_index(matching).items()
    }
