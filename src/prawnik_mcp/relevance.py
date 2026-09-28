"""Crude lexical relevance for Polish queries: stopwords, prefix stems, share of query stems in a text.

Used where a source returns matches in date order (EUREKA) and to report how well a live snippet matches.
No lemmatisation: a stem drops the last three letters of a long word (keeping at least four) or the last
letter of a short one.
"""

from __future__ import annotations

import re

STOPWORDS = set(
    "a aby ale albo ani by być czy do dla go i ich jak jaki jest jeśli już lub ma może na nie nie o od oraz po "
    "przez przy się są ta tak te to tu w we z za ze że jako który która które co czy mój moja mnie mi".split()
)


def stem(word: str) -> str:
    if len(word) >= 6:
        return word[: max(4, len(word) - 3)]
    return word[:-1] if len(word) >= 4 else word  # ulga -> ulg (ulgi, ulgę), umowa -> umow


def query_stems(query: str, *, max_terms: int = 12) -> list[str]:
    out: list[str] = []
    for w in re.findall(r"\w+", query.lower()):
        if w in STOPWORDS or len(w) < 3 or w.isdigit():
            continue
        s = stem(w)
        if s not in out:
            out.append(s)
    return out[:max_terms]


def match_ratio(stems: list[str], text: str) -> float:
    """Share of query stems that start some word of `text` (1.0 when the query has no stems)."""
    if not stems:
        return 1.0
    words = set(re.findall(r"\w+", text.lower()))
    return sum(1 for s in stems if any(w.startswith(s) for w in words)) / len(stems)
