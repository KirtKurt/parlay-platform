"""Audit-driven MLB strength gate.

This patch prevents official MLB builds from being created only because three
scored games exist. It is intentionally conservative after the prior baseline
card produced too many weak/fragile legs.
"""

BASELINE_MIN_SCORE = 52.0
BASELINE_MIN_LEAN_OR_STRONG = 1
BASELINE_MAX_FRAGILE = 1


def _grade_count(rows, grade):
    return len([row for row in rows if row.get("grade") == grade])


def _eligible_baseline_leg(row):
    grade = row.get("grade")
    tags = set(row.get("tags") or [])
    try:
        score = float(row.get("score") or 0)
    except Exception:
        score = 0.0
    if score < BASELINE_MIN_SCORE:
        return False
    if grade == "FRAGILE":
        return False
    if tags == {"BOOK_AGREEMENT"}:
        return False
    if "LATE_INSTABILITY" in tags or "BOOK_DIVERGENCE" in tags:
        return False
    return grade in {"MLB_STRONG", "MLB_LEAN", "COIN_FLIP"}


def _looks_weak_baseline(result):
    if not isinstance(result, dict):
        return False
    if result.get("buildStatus") != "BUILT":
        return False
    if result.get("buildQuality") not in {"MLB_BASELINE_AVAILABLE_HISTORY", "MLB_BASELINE_STRENGTHENED"}:
        return False
    legs = result.get("legs") or []
    if len(legs) < 3:
        return False
    fragile = _grade_count(legs, "FRAGILE")
    lean_or_strong = len([row for row in legs if row.get("grade") in {"MLB_STRONG", "MLB_LEAN"}])
    all_book_agreement_only = all(set(row.get("tags") or []) == {"BOOK_AGREEMENT"} for row in legs)
    if fragile > BASELINE_MAX_FRAGILE:
        return True
    if lean_or_strong < BASELINE_MIN_LEAN_OR_STRONG:
        return True
    if all_book_agreement_only:
        return True
    if any(not _eligible_baseline_leg(row) for row in legs):
        return True
    return False


def _weakened_reason(result):
    legs = result.get("legs") or []
    return {
        "reason": "NO_BUILD_BASELINE_STRENGTH_GATE",
        "message": "MLB baseline build refused after audit. Official MLB builds now require at least one MLB_LEAN/MLB_STRONG, no more than one FRAGILE leg, minimum score 52, and more than BOOK_AGREEMENT alone.",
        "baselinePolicy": {
            "minScore": BASELINE_MIN_SCORE,
            "minLeanOrStrong": BASELINE_MIN_LEAN_OR_STRONG,
            "maxFragile": BASELINE_MAX_FRAGILE,
            "bookAgreementAloneQualifies": False,
            "allFragileBuildAllowed": False,
        },
        "rejectedLegs": [
            {
                "gameId": row.get("gameId"),
                "selection": row.get("selection"),
                "grade": row.get("grade"),
                "score": row.get("score"),
                "tags": row.get("tags"),
            }
            for row in legs
        ],
    }


def apply(module):
    if getattr(module, "_INQSI_MLB_STRENGTH_GATE_APPLIED", False):
        return module
    original_build = module.build

    def guarded_build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        if not _looks_weak_baseline(result):
            if isinstance(result, dict):
                result["strengthGate"] = {"applied": True, "rejected": False}
            return result
        rejection = _weakened_reason(result)
        cleaned = dict(result)
        cleaned.update({
            "buildStatus": "NO_BUILD",
            "buildQuality": None,
            "officialStrength": "REFUSED_WEAK_BASELINE",
            "strengthGate": {"applied": True, "rejected": True, **rejection},
            "reason": rejection["reason"],
            "message": rejection["message"],
            "rankedCombos": [],
            "legs": [],
        })
        return cleaned

    module.build = guarded_build
    module._INQSI_MLB_STRENGTH_GATE_APPLIED = True
    return module
