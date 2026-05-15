"""Dedupe + confidence scoring across accumulated findings."""

from __future__ import annotations

from .schema import Finding, Query


def _name_tokens(q: Query) -> set[str]:
    if not q.name:
        return set()
    return {t.lower() for t in q.name.split() if len(t) >= 2}


def matches_query_fields(f: Finding, q: Query) -> list[str]:
    """Return which Query fields the finding's blob corroborates."""
    blob = " ".join(
        [f.title or "", str(f.source_url), str(f.data)]
    ).lower()
    matched: list[str] = []
    if q.name:
        toks = _name_tokens(q)
        if toks and all(t in blob for t in toks):
            matched.append("name")
    if q.email and q.email.lower() in blob:
        matched.append("email")
    if q.username and q.username.lower() in blob:
        matched.append("username")
    if q.location and q.location.lower() in blob:
        matched.append("location")
    if q.employer and q.employer.lower() in blob:
        matched.append("employer")
    if q.phone and (q.phone in blob or q.phone.lstrip("+") in blob):
        matched.append("phone")
    return matched


def rescore(f: Finding, q: Query, accumulated: list[Finding]) -> Finding:
    """Recompute confidence given query and prior findings."""
    matched = matches_query_fields(f, q)
    f.matched_fields = matched

    base = {
        "profile": 0.55,
        "article": 0.35,
        "publication": 0.55,
        "repo": 0.55,
        "mention": 0.30,
        "breach": 0.70,
        "image": 0.40,
    }.get(f.type, 0.4)

    boost = 0.0
    boost += 0.10 * sum(1 for x in matched if x != "name")
    if "name" in matched and q.name and len(q.name.split()) >= 2:
        boost += 0.10

    # Cross-source corroboration
    matched_set = set(matched)
    distinct_modules_with_match = {
        a.module
        for a in accumulated
        if a.module != f.module and set(a.matched_fields) & matched_set
    }
    boost += min(0.15, 0.05 * len(distinct_modules_with_match))

    f.confidence = round(min(1.0, base + boost), 3)
    return f


def is_duplicate(f: Finding, seen: set[tuple[str, str]]) -> bool:
    key = f.dedupe_key()
    if key in seen:
        return True
    seen.add(key)
    return False
