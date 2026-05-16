"""Cluster findings into Person identities via union-find over identity signals."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from .schema import Finding, Person, Query

# Signals that are strong enough to merge two findings into the same Person.
STRONG_SIGNALS = ("email", "orcid", "github_login", "gravatar_hash", "openalex_id")

# Usernames need extra care: short or generic usernames are weak.
USERNAME_STOPWORDS = {
    "admin", "user", "info", "test", "guest", "root", "support", "contact",
    "hello", "demo", "mail", "name", "main",
}


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        while self.parent.get(x, x) != x:
            self.parent[x] = self.parent.get(self.parent[x], self.parent[x])
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _username_is_strong(u: str) -> bool:
    return len(u) >= 4 and u.lower() not in USERNAME_STOPWORDS


def _strong_signal_keys(f: Finding) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    sig = f.signals or {}
    for k in STRONG_SIGNALS:
        for v in sig.get(k, []):
            out.append((k, v.lower()))
    for u in sig.get("username", []):
        if _username_is_strong(u):
            out.append(("username", u.lower()))
    # institution alone isn't enough to merge; we only merge institution +
    # surname pairs, which the modules can emit via "doi_author_pair".
    for pair in sig.get("doi_author_pair", []):
        out.append(("doi_author_pair", pair.lower()))
    return out


def _person_id(signal_keys: list[tuple[str, str]], fallback: str) -> str:
    canonical = (
        ",".join(sorted({f"{k}:{v}" for k, v in signal_keys})) if signal_keys else fallback
    )
    return hashlib.sha1(canonical.encode()).hexdigest()[:10]


def _pick_display_name(findings: list[Finding], query: Query) -> str:
    # Prefer ORCID/OpenAlex profile names; fall back to highest-confidence title;
    # finally the query name.
    for f in findings:
        if f.module in ("orcid", "openalex") and f.title:
            t = f.title.split(":", 1)[-1].strip()
            if t:
                return t
    profile_titles = sorted(
        (f for f in findings if f.type == "profile" and f.title),
        key=lambda f: -f.confidence,
    )
    if profile_titles:
        return profile_titles[0].title
    if query.name:
        return query.name
    return findings[0].title if findings else "(unidentified)"


def _merge_signals(findings: list[Finding]) -> dict[str, list[str]]:
    merged: dict[str, set[str]] = defaultdict(set)
    for f in findings:
        for k, vs in (f.signals or {}).items():
            for v in vs:
                merged[k].add(v)
    return {k: sorted(v) for k, v in merged.items()}


def cluster(findings: list[Finding], query: Query) -> list[Person]:  # noqa: PLR0912
    """Cluster findings → Person list. Findings with no strong signals fall into
    a single 'query-anchor' person if the query has a name, else become
    singleton persons."""
    if not findings:
        return []

    uf = _UnionFind()
    # Each finding starts as its own component.
    for i, _ in enumerate(findings):
        uf.parent[i] = i

    # Index strong signals -> finding indices.
    sig_to_idx: dict[tuple[str, str], list[int]] = defaultdict(list)
    has_strong: list[bool] = []
    for i, f in enumerate(findings):
        keys = _strong_signal_keys(f)
        has_strong.append(bool(keys))
        for k in keys:
            sig_to_idx[k].append(i)

    # Union all indices that share a strong signal.
    for idxs in sig_to_idx.values():
        for j in idxs[1:]:
            uf.union(idxs[0], j)

    # Findings without strong signals: collapse into one cluster per query name
    # (so loose "Donald Knuth" mentions become one Person, not 10).
    anchor_idx: int | None = None
    for i, strong in enumerate(has_strong):
        if strong:
            continue
        if anchor_idx is None:
            anchor_idx = i
        else:
            uf.union(anchor_idx, i)

    # Group by root.
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(findings)):
        groups[uf.find(i)].append(i)

    persons: list[Person] = []
    for root, idxs in groups.items():
        fs = [findings[i] for i in idxs]
        keys = []
        for f in fs:
            keys.extend(_strong_signal_keys(f))
        pid = _person_id(keys, fallback=f"anchor:{query.name or root}")
        avg = sum(f.confidence for f in fs) / max(1, len(fs))
        confidence = min(1.0, avg + 0.05 * (len(fs) - 1))
        persons.append(
            Person(
                id=pid,
                display_name=_pick_display_name(fs, query),
                confidence=round(min(1.0, confidence), 3),
                signals=_merge_signals(fs),
                finding_keys=[f.dedupe_key() for f in fs],
            )
        )
    # Stable order: highest confidence first.
    persons.sort(key=lambda p: -p.confidence)
    return persons
