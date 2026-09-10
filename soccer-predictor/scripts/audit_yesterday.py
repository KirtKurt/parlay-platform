#!/usr/bin/env python3
"""Grade every stored soccer card whose kickoff fell on the prior America/New_York day."""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from _common import ROOT, fail_main
from soccer_predictor.io import TeamNames, day, load_history, write_json

NY = ZoneInfo("America/New_York")
CARDS = ROOT / "artifacts" / "cards"
AUDITS = ROOT / "artifacts" / "audits"


def _ny_date(value):
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert(NY).date()


def _prior_day(value=None):
    now = pd.Timestamp(value or datetime.now(NY))
    if now.tzinfo is None:
        now = now.tz_localize(NY)
    return (now.tz_convert(NY) - pd.Timedelta(days=1)).date()


def _load_cards():
    rows = []
    if not CARDS.exists():
        return rows
    for path in sorted(CARDS.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        preds = payload.get("predictions", payload if isinstance(payload, list) else [])
        for row in preds:
            item = dict(row)
            item["card_file"] = path.name
            rows.append(item)
    return rows


def _one_x_two(row):
    probs = [(row.get("p_home"), "H"), (row.get("p_draw"), "D"), (row.get("p_away"), "A")]
    if any(p is None for p, _ in probs):
        return None
    return max(probs, key=lambda item: item[0])[1]


def _hit(pred, actual):
    if pred is None or actual is None:
        return None
    return int(str(pred) == str(actual))


def main():
    parser = argparse.ArgumentParser(description="Audit prior-day soccer cards against completed results")
    parser.add_argument("--date", help="America/New_York calendar date to audit, default yesterday")
    parser.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    args = parser.parse_args()
    target = pd.Timestamp(args.date).date() if args.date else _prior_day()
    names = TeamNames.load(ROOT / "config" / "teams.yaml")
    history, _ = load_history(args.raw, names, True)
    cards = _load_cards()
    selected = [r for r in cards if r.get("kickoff") and _ny_date(r["kickoff"]) == target]
    results = {}
    for rec in history.to_dict("records"):
        results[(day(rec["date"]).date(), rec["home"], rec["away"])] = rec

    graded = []
    for row in selected:
        home = names.resolve(row["home"])
        away = names.resolve(row["away"])
        kick = pd.Timestamp(row["kickoff"])
        utc_day = day(kick).date()
        result = results.get((utc_day, home, away))
        pick = _one_x_two(row)
        actual = None
        hg = ag = None
        if result is not None:
            hg, ag = int(result["hg"]), int(result["ag"])
            actual = "H" if hg > ag else "A" if hg < ag else "D"
        btts_pred = "Y" if float(row.get("p_btts_yes") or 0) >= 0.5 else "N"
        ou_pred = "O" if float(row.get("p_over25") or 0) >= 0.5 else "U"
        dc_pred = row.get("double_chance_pick")
        score_pred = str(row.get("score") or "")
        btts_act = ou_act = dc_act = score_act = None
        if hg is not None:
            btts_act = "Y" if hg > 0 and ag > 0 else "N"
            ou_act = "O" if hg + ag > 2 else "U"
            score_act = f"{hg}-{ag}"
            covers = {"1X": {"H", "D"}, "12": {"H", "A"}, "X2": {"D", "A"}}
            dc_act = actual if dc_pred in covers and actual in covers[dc_pred] else None
            dc_hit = int(dc_pred in covers and actual in covers[dc_pred]) if dc_pred else None
        else:
            dc_hit = None
        graded.append({
            "audit_date": target.isoformat(),
            "kickoff": kick.isoformat(),
            "div": row.get("div"),
            "home": home,
            "away": away,
            "card_file": row.get("card_file"),
            "status": "graded" if result is not None else "pending_result",
            "score_actual": score_act,
            "result_actual": actual,
            "pick_1x2": pick,
            "hit_1x2": _hit(pick, actual),
            "double_chance": dc_pred,
            "hit_dc": dc_hit,
            "btts_pick": btts_pred,
            "hit_btts": _hit(btts_pred, btts_act),
            "ou25_pick": ou_pred,
            "hit_ou25": _hit(ou_pred, ou_act),
            "score_pick": score_pred,
            "hit_score": _hit(score_pred, score_act),
            "p_home": row.get("p_home"),
            "p_draw": row.get("p_draw"),
            "p_away": row.get("p_away"),
            "p_over25": row.get("p_over25"),
            "p_btts_yes": row.get("p_btts_yes"),
            "confidence_tier": row.get("confidence_tier"),
        })

    AUDITS.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(graded)
    csv_path = AUDITS / f"{target.isoformat()}.csv"
    md_path = AUDITS / f"{target.isoformat()}.md"
    ledger_path = AUDITS / "ledger.csv"
    if frame.empty:
        summary = {
            "audit_date": target.isoformat(),
            "n_cards": 0,
            "n_graded": 0,
            "n_pending": 0,
            "acc_1x2": None,
            "acc_dc": None,
            "acc_btts": None,
            "acc_ou25": None,
            "acc_score": None,
        }
        lines = [
            f"# Soccer audit {target.isoformat()}",
            "",
            "No stored card rows kicked off on this America/New_York date.",
            "Put predictions in soccer-predictor/artifacts/cards/*.json",
        ]
    else:
        graded_rows = frame[frame.status == "graded"]
        def rate(col):
            s = graded_rows[col].dropna()
            return None if s.empty else float(s.mean())
        summary = {
            "audit_date": target.isoformat(),
            "n_cards": int(len(frame)),
            "n_graded": int(len(graded_rows)),
            "n_pending": int((frame.status == "pending_result").sum()),
            "acc_1x2": rate("hit_1x2"),
            "acc_dc": rate("hit_dc"),
            "acc_btts": rate("hit_btts"),
            "acc_ou25": rate("hit_ou25"),
            "acc_score": rate("hit_score"),
        }
        frame.to_csv(csv_path, index=False)
        if ledger_path.exists():
            old = pd.read_csv(ledger_path)
            keep = old[old.get("audit_date") != target.isoformat()] if "audit_date" in old.columns else old
            pd.concat([keep, frame], ignore_index=True).to_csv(ledger_path, index=False)
        else:
            frame.to_csv(ledger_path, index=False)
        def pct(v):
            return "n/a" if v is None else f"{v:.1%}"
        lines = [
            f"# Soccer audit {target.isoformat()} (America/New_York prior day)",
            "",
            f"Cards: {summary['n_cards']}  Graded: {summary['n_graded']}  Pending: {summary['n_pending']}",
            "",
            f"- 1X2: {pct(summary['acc_1x2'])}",
            f"- Double chance: {pct(summary['acc_dc'])}",
            f"- BTTS: {pct(summary['acc_btts'])}",
            f"- O/U 2.5: {pct(summary['acc_ou25'])}",
            f"- Exact score: {pct(summary['acc_score'])}",
            "",
            "| Kickoff | Match | Pred 1X2 | Result | 1X2 | DC | BTTS | O/U | Score | Status |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        mark = lambda v: "" if pd.isna(v) else ("HIT" if int(v) == 1 else "MISS")
        for r in graded:
            lines.append(
                f"| {r['kickoff']} | {r['home']} – {r['away']} | {r['pick_1x2']} | {r['score_actual'] or '—'} | "
                f"{mark(r['hit_1x2'])} | {mark(r['hit_dc'])} | {mark(r['hit_btts'])} | {mark(r['hit_ou25'])} | {mark(r['hit_score'])} | {r['status']} |"
            )
        lines.extend(["", "Not betting advice. Pending rows wait for football-data.co.uk scores."])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(AUDITS / f"{target.isoformat()}.json", {"summary": summary, "rows": graded})
    write_json(AUDITS / "latest.json", {"summary": summary, "rows": graded})
    print("\n".join(lines))


if __name__ == "__main__":
    fail_main(main)
