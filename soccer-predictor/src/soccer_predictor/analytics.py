from __future__ import annotations

import io
import logging
import os
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .bbd import weekly_card
from .io import TeamNames

LOG = logging.getLogger(__name__)
CLUBELO_BASE_URL = "http://api.clubelo.com"


def fetch_clubelo_ratings(
    as_of: date | datetime | str | None = None,
    *,
    names: TeamNames | None = None,
    timeout: int = 20,
) -> dict[str, float]:
    """Fetch ClubElo ratings as an optional prior.

    Returns an empty mapping on network/provider failure so ClubElo never
    becomes a hard dependency. Team names are canonicalized when possible.
    """
    names = names or TeamNames()
    if as_of is None:
        stamp = datetime.now(timezone.utc).date().isoformat()
    elif isinstance(as_of, datetime):
        stamp = as_of.date().isoformat()
    elif isinstance(as_of, date):
        stamp = as_of.isoformat()
    else:
        stamp = str(as_of)

    url = f"{CLUBELO_BASE_URL}/{stamp}"
    req = Request(
        url,
        headers={
            "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.8",
            "User-Agent": "soccer-predictor/1.0",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        frame = pd.read_csv(io.StringIO(body))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, pd.errors.ParserError) as exc:
        LOG.warning("ClubElo unavailable; continuing without ClubElo priors: %s", exc)
        return {}

    club_col = next((c for c in ("Club", "club", "Team", "team") if c in frame.columns), None)
    elo_col = next((c for c in ("Elo", "elo", "Rating", "rating") if c in frame.columns), None)
    if club_col is None or elo_col is None:
        LOG.warning("ClubElo response missing club/rating columns; skipping")
        return {}

    out: dict[str, float] = {}
    for raw_team, raw_elo in zip(frame[club_col], frame[elo_col]):
        try:
            elo = float(raw_elo)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(elo):
            continue
        team = str(raw_team).strip()
        if not team:
            continue
        try:
            team = names.resolve(team)
        except ValueError:
            pass
        out[team] = elo
    return out


def fetch_bbd_match_features(
    *,
    names: TeamNames | None = None,
    api_key: str | None = None,
) -> list[dict]:
    """Return optional BBD xG/lineup features for the current EPL/UCL card.

    The key is read only from the explicit argument or BBD_API_KEY. Missing
    credentials return an empty list and do not interrupt the core pipeline.
    """
    key = api_key if api_key is not None else os.getenv("BBD_API_KEY", "")
    if not key:
        LOG.info("BBD_API_KEY unset; continuing without BBD analytics")
        return []

    rows = weekly_card(names=names or TeamNames(), api_key=key)
    return [
        {
            "date": row.get("date"),
            "home": row.get("home"),
            "away": row.get("away"),
            "xg_h_bbd": row.get("xg_h_bbd", np.nan),
            "xg_a_bbd": row.get("xg_a_bbd", np.nan),
            "lineup_h_bbd": row.get("lineup_h_bbd", np.nan),
            "lineup_a_bbd": row.get("lineup_a_bbd", np.nan),
        }
        for row in rows
    ]
