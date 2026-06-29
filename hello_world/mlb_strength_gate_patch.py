"""MLB Anchor + Lean + Controlled Variable build gate.

This patch changes MLB baseline behavior from "top 3 available" into a
structured card:
  1 Anchor
  1 Lean
  1 Controlled Variable

It still refuses random weak baseline cards.
"""

ANCHOR_MIN_SCORE = 62.0
LEAN_MIN_SCORE = 55.0
VARIABLE_MIN_SCORE = 52.0


def _score(row):
    try:
        return float(row.get("score") or 0)
    except Exception:
        return 0.0


def _tags(row):
    return set(row.get("tags") or [])


def _role(row):
    grade = row.get("grade")
    tags = _tags(row)
    score = _score(row)
    if "LATE_INSTABILITY" in tags or "BOOK_DIVERGENCE" in tags:
        return None
    if score >= ANCHOR_MIN_SCORE and grade in {"MLB_STRONG", "MLB_LEAN", "COIN_FLIP"} and tags != {"BOOK_AGREEMENT"}:
        return "ANCHOR"
    if score >= LEAN_MIN_SCORE and grade in {"MLB_LEAN", "COIN_FLIP"} and tags != {"BOOK_AGREEMENT"}:
        return "LEAN"
    controlled_tags = {"STEAM", "RUN_LINE_CONFIRMATION", "RUN_LINE_MOVEMENT", "REVERSAL", "COMPRESSED_MARKET"}
    if score >= VARIABLE_MIN_SCORE and grade in {"COIN_FLIP", "MLB_LEAN"} and bool(tags & controlled_tags) and tags != {"BOOK_AGREEMENT"}:
        return "CONTROLLED_VARIABLE"
    return None


def _select_structured_card(legs):
    pool = sorted(legs or [], key=_score, reverse=True)
    by_game = set()
    anchor = lean = variable = None

    for row in pool:
        if row.get("gameId") in by_game:
            continue
        if _role(row) == "ANCHOR":
            anchor = row
            by_game.add(row.get("gameId"))
            break

    for row in pool:
        if row.get("gameId") in by_game:
            continue
        if _role(row) in {"LEAN", "ANCHOR"}:
            lean = row
            by_game.add(row.get("gameId"))
            break

    for row in pool:
        if row.get("gameId") in by_game:
            continue
        if _role(row) == "CONTROLLED_VARIABLE":
            variable = row
            by_game.add(row.get("gameId"))
            break

    if anchor and lean and variable:
        selected = [dict(anchor), dict(lean), dict(variable)]
        selected[0]["structureRole"] = "ANCHOR"
        selected[1]["structureRole"] = "LEAN"
        selected[2]["structureRole"] = "CONTROLLED_VARIABLE"
        return selected
    return None


def _ranked_combos(selected):
    combos = []
    for mask in range(8):
        legs = []
        total = 0.0
        for i, row in enumerate(selected):
            home_pick = bool(mask & (1 << i))
            home_sig = row.get("homeSignal") or {}
            away_sig = row.get("awaySignal") or {}
            sig = home_sig if home_pick else away_sig
            selection = row.get("homeTeam") if home_pick else row.get("awayTeam")
            legs.append({
                "gameId": row.get("gameId"),
                "selection": selection,
                "side": "home" if home_pick else "away",
                "grade": sig.get("grade"),
                "tags": sig.get("tags") or [],
                "score": sig.get("score"),
                "structureRole": row.get("structureRole"),
                "commenceTime": row.get("commenceTime"),
                "cutoffTime": row.get("cutoffTime"),
            })
            total += _score(sig)
        combos.append({"rank": 0, "score": round(total / 3.0, 2), "legs": legs})
    combos.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(combos, 1):
        row.update({"rank": i, "top3": i <= 3})
    return combos


def _refusal(result):
    legs = result.get("legs") or []
    return {
        "reason": "NO_BUILD_ANCHOR_LEAN_VARIABLE_GATE",
        "message": "MLB build refused: structure requires 1 Anchor, 1 Lean, and 1 Controlled Variable. Controlled Variable must have a real movement/market signal, not BOOK_AGREEMENT alone.",
        "structurePolicy": {
            "required": ["ANCHOR", "LEAN", "CONTROLLED_VARIABLE"],
            "anchorMinScore": ANCHOR_MIN_SCORE,
            "leanMinScore": LEAN_MIN_SCORE,
            "controlledVariableMinScore": VARIABLE_MIN_SCORE,
            "bookAgreementAloneQualifies": False,
        },
        "availableLegs": [
            {"gameId": row.get("gameId"), "selection": row.get("selection"), "grade": row.get("grade"), "score": row.get("score"), "tags": row.get("tags"), "role": _role(row)}
            for row in legs
        ],
    }


def apply(module):
    if getattr(module, "_INQSI_MLB_STRENGTH_GATE_APPLIED", False):
        return module
    original_build = module.build

    def guarded_build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        if not isinstance(result, dict):
            return result
        if result.get("buildStatus") != "BUILT" or result.get("buildQuality") not in {"MLB_BASELINE_AVAILABLE_HISTORY", "MLB_BASELINE_STRENGTHENED"}:
            result["structureGate"] = {"applied": True, "structure": "ANCHOR_LEAN_CONTROLLED_VARIABLE", "rejected": False, "reason": "STRICT_OR_NO_BUILD_UNCHANGED"}
            return result
        selected = _select_structured_card(result.get("legs") or [])
        if selected:
            cleaned = dict(result)
            cleaned.update({
                "buildStatus": "BUILT",
                "buildQuality": "MLB_ANCHOR_LEAN_CONTROLLED_VARIABLE",
                "officialStrength": "STRUCTURE_GATED",
                "structure": "1_ANCHOR_1_LEAN_1_CONTROLLED_VARIABLE",
                "structureGate": {"applied": True, "rejected": False, "roles": ["ANCHOR", "LEAN", "CONTROLLED_VARIABLE"]},
                "reason": "BUILT_MLB_ANCHOR_LEAN_CONTROLLED_VARIABLE",
                "message": "MLB card built using 1 Anchor, 1 Lean, and 1 Controlled Variable.",
                "legs": selected,
                "rankedCombos": _ranked_combos(selected),
            })
            return cleaned
        refusal = _refusal(result)
        cleaned = dict(result)
        cleaned.update({
            "buildStatus": "NO_BUILD",
            "buildQuality": None,
            "officialStrength": "REFUSED_STRUCTURE_NOT_MET",
            "structure": "1_ANCHOR_1_LEAN_1_CONTROLLED_VARIABLE",
            "structureGate": {"applied": True, "rejected": True, **refusal},
            "reason": refusal["reason"],
            "message": refusal["message"],
            "rankedCombos": [],
            "legs": [],
        })
        return cleaned

    module.build = guarded_build
    module._INQSI_MLB_STRENGTH_GATE_APPLIED = True
    return module
