import json
import os

import inqsi_pull_history as history

MIN_PULLS = int(os.environ.get("INQSI_MIN_PARLAY_PULLS", "12"))
MIN_STRONG = int(os.environ.get("INQSI_MIN_STRONG_LEGS", "2"))
MAX_COIN = int(os.environ.get("INQSI_MAX_COIN_FLIP_LEGS", "1"))
STRONG_GRADES = {"STRONG_SOLID", "SOLID"}
COIN = "COIN_FLIP"


def unique(rows):
    seen, out = set(), []
    for row in rows:
        gid = row.get("gameId")
        if gid and gid not in seen:
            seen.add(gid)
            out.append(row)
    return out


def strict_result(sport):
    sig = history.signals({"sport": sport})
    pull_count = int(sig.get("pullCount") or 0)
    base = {"ok": True, "sport": sig.get("sport"), "slate_date": sig.get("slate_date"), "pullCount": pull_count, "minimumParlayPulls": MIN_PULLS, "minimumStrongLegs": MIN_STRONG, "maximumCoinFlipLegs": MAX_COIN, "structure": "STRICT_2_STRONG_MAX_1_COIN_FLIP"}
    if pull_count < MIN_PULLS:
        return {**base, "buildStatus": "NO_BUILD", "reason": "WAITING_FOR_12TH_PULL", "message": "Refused before minimum pull depth."}
    eligible = unique([s for s in sig.get("signals", []) if s.get("grade") in STRONG_GRADES or s.get("grade") == COIN])
    strong = unique([s for s in eligible if s.get("grade") in STRONG_GRADES])
    coins = unique([s for s in eligible if s.get("grade") == COIN])
    if len(strong) < MIN_STRONG:
        return {**base, "buildStatus": "NO_BUILD", "reason": "MISSING_TWO_STRONG_LEGS", "eligibleCount": len(eligible), "strongCount": len(strong), "coinFlipCount": len(coins), "message": "Refused all-coin-flip or weak build."}
    selected, used = [], set()
    for row in strong:
        if len(selected) < MIN_STRONG and row.get("gameId") not in used:
            selected.append(row); used.add(row.get("gameId"))
    for row in [x for x in strong if x.get("gameId") not in used] + [x for x in coins if x.get("gameId") not in used]:
        if len(selected) < 3:
            selected.append(row); used.add(row.get("gameId"))
    selected_strong = sum(1 for s in selected if s.get("grade") in STRONG_GRADES)
    selected_coin = sum(1 for s in selected if s.get("grade") == COIN)
    if len(selected) < 3 or selected_strong < MIN_STRONG or selected_coin > MAX_COIN:
        return {**base, "buildStatus": "NO_BUILD", "reason": "STRICT_STRUCTURE_NOT_MET", "selectedStrongCount": selected_strong, "selectedCoinFlipCount": selected_coin, "eligibleCount": len(eligible), "strongCount": len(strong), "coinFlipCount": len(coins), "message": "Refused structure that is not 2 strong plus max 1 coin flip."}
    combos = []
    for mask in range(8):
        legs, score = [], 0
        for i, s in enumerate(selected):
            home_pick = bool(mask & (1 << i))
            side = "home" if home_pick else "away"
            side_sig = s["homeSignal"] if home_pick else s["awaySignal"]
            legs.append({"gameId": s["gameId"], "selection": s["homeTeam"] if home_pick else s["awayTeam"], "side": side, "grade": side_sig["grade"], "tags": side_sig["tags"]})
            score += side_sig["score"]
        combos.append({"rank": 0, "score": round(score / 3, 2), "legs": legs})
    combos.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(combos, 1):
        row.update({"rank": i, "top3": i <= 3})
    return {**base, "buildStatus": "BUILT", "selectedStrongCount": selected_strong, "selectedCoinFlipCount": selected_coin, "legs": selected, "rankedCombos": combos}


def main():
    sports = os.environ.get("INQSI_AUTO_BUILD_SPORTS", "mlb,wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis")
    sports_list = [s.strip() for s in sports.split(",") if s.strip()]
    store_fn = getattr(history, "store_" + "parlay_build")
    latest_fn = getattr(history, "latest_" + "parlay_build")
    results = []
    for sport in sports_list:
        result = strict_result(sport)
        try:
            result["stored"] = store_fn(result, mode="strict_auto_after_live_pull")
        except Exception as exc:
            result["storeError"] = str(exc)
        results.append(result)
    built = [row.get("sport") for row in results if row.get("buildStatus") == "BUILT"]
    report = {"ok": bool(built), "sports": sports_list, "builtSports": built, "mlbBuilt": "mlb" in built, "mlbLatestBuild": latest_fn({"sport": "mlb"}), "result": {"ok": True, "autoBuild": True, "builtCount": len(built), "results": results}}
    os.makedirs("runtime_reports", exist_ok=True)
    with open("runtime_reports/parlay_latest.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
        f.write("\n")
    print(json.dumps({"ok": report["ok"], "builtSports": built, "mlbBuilt": report["mlbBuilt"]}, indent=2))


if __name__ == "__main__":
    main()
