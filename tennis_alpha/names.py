from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_PUNCT = re.compile(r"[^a-z0-9\s]")
_SPACES = re.compile(r"\s+")

ALIASES = {
    "jannik sinner": "jannik sinner",
    "carlos alcaraz": "carlos alcaraz",
    "novak djokovic": "novak djokovic",
    "n djokovic": "novak djokovic",
    "c alcaraz": "carlos alcaraz",
    "j sinner": "jannik sinner",
    "i swiatek": "iga swiatek",
    "iga swiatek": "iga swiatek",
    "a sabalenka": "aryna sabalenka",
    "aryna sabalenka": "aryna sabalenka",
}


def normalize(name: str) -> str:
    raw = unicodedata.normalize("NFKD", name or "")
    ascii_only = raw.encode("ascii", "ignore").decode("ascii")
    ascii_only = ascii_only.replace(",", " ")
    ascii_only = _PUNCT.sub(" ", ascii_only.lower())
    return _SPACES.sub(" ", ascii_only).strip()


def tokens(name: str) -> list[str]:
    return [t for t in normalize(name).split(" ") if t]


def _key(name: str) -> str:
    parts = tokens(name)
    return " ".join(sorted(parts))


def match_name(query: str, catalog: Iterable[str]) -> str | None:
    qn = normalize(query)
    if not qn:
        return None
    if qn in ALIASES:
        qn = ALIASES[qn]
    exact = {normalize(n): n for n in catalog}
    if qn in exact:
        return exact[qn]
    qk = _key(qn)
    keyed = {_key(n): n for n in catalog}
    if qk in keyed:
        return keyed[qk]
    q_tokens = tokens(qn)
    if not q_tokens:
        return None
    best = None
    best_score = 0.0
    q_last = q_tokens[-1]
    for name in catalog:
        nt = tokens(name)
        if not nt or q_last != nt[-1]:
            continue
        shared = len(set(q_tokens) & set(nt))
        score = shared / max(len(set(q_tokens) | set(nt)), 1)
        if q_tokens[0][0] == nt[0][0]:
            score += 0.15
        if score > best_score:
            best_score = score
            best = name
    return best if best_score >= 0.45 else None
