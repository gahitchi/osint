"""Derive tags for a clustered Person from their accumulated findings.

Pure rules + a tiny keyword dictionary. No model dependencies."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .schema import Finding, Person, Query

# Domain keywords → tag. Matched against title + snippet of every finding.
# Compact, conservative; expand as needed.
DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "computer-science": ("algorithm", "computing", "software", "compiler",
                         "data structure", "programming", "tex", "latex"),
    "ai-ml": ("machine learning", "deep learning", "neural network",
              "transformer", "llm", "reinforcement learning", "nlp"),
    "security": ("vulnerability", "exploit", "cve", "malware", "infosec",
                 "penetration", "cryptography", "cipher"),
    "web": ("javascript", "typescript", "react", "frontend", "css ",
            "html", "browser"),
    "data": ("dataset", "data analysis", "statistics", "regression",
             "bayesian"),
    "biology": ("genome", "protein", "rna", "dna", "cell", "biology"),
    "medicine": ("clinical", "patient", "therapy", "disease", "medical"),
    "physics": ("quantum", "particle", "photon", "relativity"),
    "math": ("theorem", "proof", "lemma", "combinator", "topology"),
    "law": ("court", "litigation", "lawsuit", "regulation", "compliance"),
    "business": ("ceo", "startup", "founder", "investor", "venture capital"),
    "music": ("album", "guitarist", "vocalist", "composer", "concerto"),
    "sports": ("athlete", "championship", "olympic", "league", "tournament"),
    "journalism": ("journalist", "reporter", "correspondent"),
    "academia": ("professor", "associate professor", "researcher",
                 "lecturer", "phd candidate"),
    "open-source": ("github", "pull request", "open source", "contributor"),
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower())


def _activity_tags(findings: list[Finding]) -> list[str]:
    tags: list[str] = []
    by_cat: Counter[str] = Counter(f.category for f in findings)
    by_type: Counter[str] = Counter(f.type for f in findings)
    by_mod: Counter[str] = Counter(f.module for f in findings)

    if by_cat["academic"] >= 1 or by_type["publication"] >= 1:
        tags.append("academic")
    if by_type["publication"] >= 5:
        tags.append("prolific-author")
    elif by_type["publication"] >= 1:
        tags.append("author")
    if by_mod.get("github_user", 0) >= 1:
        tags.append("developer")
    if by_cat["social"] >= 3:
        tags.append("active-online")
    if by_type["article"] >= 1:
        tags.append("in-the-news")
    if by_type["breach"] >= 1:
        tags.append("breach-exposed")
    if by_mod.get("orcid", 0) >= 1:
        tags.append("researcher")
    if by_type["contact"] >= 1:
        tags.append("contactable")
        kinds = {f.data.get("kind") for f in findings if f.type == "contact"}
        if "email" in kinds:
            tags.append("has-email")
        if "phone" in kinds:
            tags.append("has-phone")
    # If the user pinned a source platform, surface that.
    sources = {
        f.data.get("platform") for f in findings
        if f.module == "pivot_crawler" and f.data.get("verified")
    }
    for s in sorted(x for x in sources if x):
        tags.append(f"verified:{s}")
    return tags


def _institution_tags(findings: list[Finding]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for f in findings:
        raw = f.data.get("institution")
        if raw is None:
            continue
        institutions = raw if isinstance(raw, list) else [raw]
        for inst in institutions:
            if not isinstance(inst, str):
                continue
            key = inst.strip()
            if not key or key.lower() in seen:
                continue
            seen.add(key.lower())
            out.append(f"@{key}")
    return out[:3]


def _domain_tags(findings: list[Finding]) -> list[str]:
    blob = _norm(
        " ".join(
            (f.title or "") + " " + str(f.data.get("snippet", ""))
            for f in findings
        )
    )
    hits: list[str] = []
    for tag, kws in DOMAIN_KEYWORDS.items():
        if any(k in blob for k in kws):
            hits.append(tag)
    return hits


def _query_match_tags(person: Person, query: Query) -> list[str]:
    sig = person.signals or {}
    out: list[str] = []
    if query.location and any(
        query.location.lower() in inst.lower() for inst in sig.get("institution", [])
    ):
        out.append(f"based:{query.location}")
    if query.employer and any(
        query.employer.lower() in inst.lower() for inst in sig.get("institution", [])
    ):
        out.append(f"affiliated:{query.employer}")
    return out


def tag_person(person: Person, findings: list[Finding], query: Query) -> list[str]:
    tags: list[str] = []
    tags += _activity_tags(findings)
    tags += _institution_tags(findings)
    tags += _domain_tags(findings)
    tags += _query_match_tags(person, query)
    # Dedup while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:12]
