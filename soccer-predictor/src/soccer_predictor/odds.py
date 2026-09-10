import numpy as np
from .io import day


def devig(values):
    try:
        odds = np.asarray([float(x) for x in values], dtype=float)
    except (ValueError, TypeError):
        return None
    if not np.isfinite(odds).all() or np.any(odds <= 1):
        return None
    inv = 1.0/odds
    return inv/inv.sum()


def _extract(row, suffixes, binary=False):
    for base in ["AvgC", "B365C", "BWC", "WHC", "VCC", "PSC", "PC", "Avg", "BbAv", "B365", "BW", "IW", "WH", "VC", "PS", "P"]:
        if base.startswith(("PS", "P")) and day(row["date"]) >= day("2025-07-23"):
            continue
        p = devig([row.get(base+s) for s in suffixes])
        if p is not None:
            return p, base, "closing" if base.endswith("C") else "preclosing_snapshot"
    return None, None, None


def bookmaker(row, prediction):
    selected = int(np.argmax([prediction["p_home"], prediction["p_draw"], prediction["p_away"]]))
    market = None
    try:
        candidate = np.asarray([row.get("mkt_h"), row.get("mkt_d"), row.get("mkt_a")], dtype=float)
        if np.isfinite(candidate).all() and np.all(candidate >= 0) and np.isclose(candidate.sum(), 1, atol=1e-3):
            market = candidate / candidate.sum()
    except (TypeError, ValueError):
        market = None
    if market is not None:
        p, base, kind = market, row.get("mkt_book") or "the_odds_api", "cached_or_live_snapshot"
    else:
        p, base, kind = _extract(row, ["H", "D", "A"])
    selected_key = ["p_home", "p_draw", "p_away"][selected]
    gap = None if p is None else float(prediction[selected_key] - p[selected])
    out = {"clv": gap,
           "clv_note": "Model probability minus market implied probability; probability-gap baseline, not achieved ROI",
           "bookmaker_source": base, "bookmaker_snapshot": kind}
    for i, key in enumerate(["home", "draw", "away"]):
        out[f"book_p_{key}"] = float(p[i]) if p is not None else None
    out["model_minus_book_selected_pp"] = None if gap is None else 100*gap
    out["snapshot_movement_selected_pp"] = None
    if market is None and base and base.endswith("C"):
        early = devig([row.get(base[:-1] + s) for s in ["H", "D", "A"]])
        if early is not None:
            out["snapshot_movement_selected_pp"] = float(100*(p[selected] - early[selected]))
    if market is not None and row.get("mkt_ou25") is not None:
        try:
            mkt_ou = float(row.get("mkt_ou25"))
        except (TypeError, ValueError):
            mkt_ou = float("nan")
        out["book_p_over25"] = mkt_ou if np.isfinite(mkt_ou) else None
        out["book_ou25_source"] = base
    else:
        ou, oubase, _ = _extract(row, [">2.5", "<2.5"], True)
        out["book_p_over25"] = None if ou is None else float(ou[0])
        out["book_ou25_source"] = oubase
    btts = devig([row.get("BTTSYes"), row.get("BTTSNo")])
    out["book_p_btts_yes"] = None if btts is None else float(btts[0])
    out["book_btts_source"] = "BTTSYes/BTTSNo" if btts is not None else None
    return out
