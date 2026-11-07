"""Build a pitcher-adjusted Elo book from already-graded KS1 games.

Only games that already have an official grade are admitted. No future
slate row is allowed to update the book before first pitch.
"""
from __future__ import annotations

from stack2mlb.elo import EloBook


def _lock_row(grade: dict) -> dict:
    lock = grade.get("lock_row") or grade.get("row") or {}
    evidence = grade.get("lock_evidence") or {}
    if isinstance(evidence, dict) and "row" in evidence:
        lock = evidence["row"] or lock
    return lock


def grade_identity(grade: dict) -> dict | None:
    lock = _lock_row(grade)
    home_id = grade.get("home_id") or lock.get("home_id")
    away_id = grade.get("away_id") or lock.get("away_id")
    if home_id is None or away_id is None:
        return None
    if grade.get("home_win") is None:
        return None
    home_score = grade.get("home_score")
    away_score = grade.get("away_score")
    margin = None
    if home_score is not None and away_score is not None:
        margin = abs(float(home_score) - float(away_score))
    return {
        "game_id": str(grade.get("game_id") or lock.get("game_id")),
        "home_id": str(home_id),
        "away_id": str(away_id),
        "home_starter": lock.get("home_starter_id") or grade.get("home_starter_id"),
        "away_starter": lock.get("away_starter_id") or grade.get("away_starter_id"),
        "home_win": bool(int(grade["home_win"])),
        "margin": margin,
        "commence_time": lock.get("commence_time") or grade.get("locked_at") or grade.get("graded_at") or "",
    }


def merge_locks(grades: list[dict], locks: list[dict]) -> list[dict]:
    """Attach KS1 prediction rows onto ledger grades so Elo can see team IDs."""
    by_id = {str(r.get("game_id")): r for r in locks}
    merged = []
    for grade in grades:
        row = dict(grade)
        lock = by_id.get(str(grade.get("game_id")))
        if lock:
            row.setdefault("lock_row", lock)
            row.setdefault("home_id", lock.get("home_id"))
            row.setdefault("away_id", lock.get("away_id"))
        merged.append(row)
    return merged


def book_from_grades(grades: list[dict], before: str | None = None) -> EloBook:
    """Walk graded games in commence order. `before` is an ISO cutoff (exclusive)."""
    identities = []
    for grade in grades:
        item = grade_identity(grade)
        if item is None:
            continue
        if before and item["commence_time"] >= before:
            continue
        identities.append(item)
    identities.sort(key=lambda r: (r["commence_time"], r["game_id"]))
    book = EloBook()
    for item in identities:
        book.update(
            item["home_id"],
            item["away_id"],
            item["home_win"],
            item["home_starter"],
            item["away_starter"],
            margin=item["margin"],
        )
    book._graded = len(identities)
    return book
