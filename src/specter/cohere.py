"""Per-cluster coherence pass.

After clustering produces a list of `Person`, this module cross-validates
the findings inside each cluster. Findings that disagree with the
majority signal get flagged with a `CoherenceRule` reason. They are
hidden by default in the UI but kept in the JSON for audit.

Rules (deliberately conservative — discovery-first):

1. name_mismatch   — declared name has zero token overlap with the canonical
                     token set, AND the canonical token set is established by
                     ≥2 findings.
2. geo_outlier     — declared location is on a different continent than the
                     majority, AND ≥2 findings agree on the majority continent.
3. century_gap     — finding's activity year is >80y from the cluster median.
4. domain_outlier  — finding's derived domain tags share zero overlap with
                     the cluster's dominant domain tag, AND there are ≥3
                     findings to establish a dominant tag.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from collections.abc import Iterable

from .schema import CoherenceFlag, CoherenceReport, Finding, Person
from .tagging import _domain_tags  # type: ignore[attr-defined]

# Coarse continent table; only needed to detect *gross* geographic disagreement.
# We default to "other" when unknown, which means we won't flag uncertain cases.
_CONTINENT_KEYWORDS = {
    "americas": ("usa", "united states", "us ", " ny", "california", "texas",
                 "canada", "mexico", "brazil", "argentina", "chile", "colombia"),
    "europe": ("uk", "england", "scotland", "ireland", "germany", "berlin",
               "france", "paris", "spain", "italy", "rome", "netherlands",
               "amsterdam", "sweden", "norway", "denmark", "switzerland",
               "poland", "portugal", "greece", "austria", "belgium"),
    "asia": ("china", "beijing", "shanghai", "japan", "tokyo", "korea", "seoul",
             "india", "delhi", "mumbai", "singapore", "thailand", "vietnam",
             "indonesia", "philippines", "malaysia", "taiwan", "hong kong"),
    "africa": ("nigeria", "egypt", "kenya", "south africa", "morocco",
               "ghana", "ethiopia"),
    "oceania": ("australia", "sydney", "melbourne", "new zealand", "auckland"),
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9\s]", " ", s)


def _continent_of(loc: str) -> str:
    n = _norm(loc)
    for cont, keys in _CONTINENT_KEYWORDS.items():
        if any(k in n for k in keys):
            return cont
    return "other"


def _name_tokens(s: str | None) -> set[str]:
    if not s:
        return set()
    return {t for t in _norm(s).split() if len(t) >= 2}


def _years_of(f: Finding) -> list[int]:
    """Pull years from finding.data. Best-effort, never throws."""
    out: list[int] = []
    pub = f.data.get("published")
    # Crossref "issued.date-parts": [[2021, 3, 14]]
    if isinstance(pub, list):
        for part in pub:
            if (
                isinstance(part, list)
                and part
                and isinstance(part[0], int)
                and 1500 <= part[0] <= 2100
            ):
                out.append(part[0])
    seen = f.data.get("seendate")
    if isinstance(seen, str):
        m = re.search(r"(1[5-9]|20)\d{2}", seen)
        if m:
            out.append(int(m.group(0)))
    for k in ("year", "first_seen", "created_at"):
        v = f.data.get(k)
        if isinstance(v, str):
            m = re.search(r"(1[5-9]|20)\d{2}", v)
            if m:
                out.append(int(m.group(0)))
        elif isinstance(v, int) and 1500 <= v <= 2100:
            out.append(v)
    return out


def _names_of(f: Finding) -> set[str]:
    """Names declared by the finding (vs the input query)."""
    out: set[str] = set()
    for k in ("name", "display_name", "given_names", "family_names", "authors"):
        v = f.data.get(k)
        if isinstance(v, str):
            out |= _name_tokens(v)
    return out


def _locations_of(f: Finding) -> list[str]:
    out: list[str] = []
    for k in ("location", "institution", "company", "country"):
        v = f.data.get(k)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            out.extend(x for x in v if isinstance(x, str))
    return out


# ===========================================================================
# Rule implementations
# ===========================================================================

def _flag_name_mismatch(findings: list[Finding]) -> Iterable[CoherenceFlag]:
    sets = [_names_of(f) for f in findings]
    populated = [s for s in sets if s]
    if len(populated) < 2:
        return
    # Build canonical set from token frequency.
    counter: Counter[str] = Counter()
    for s in populated:
        for t in s:
            counter[t] += 1
    canonical = {t for t, c in counter.items() if c >= 2}
    if not canonical:
        return
    for f, s in zip(findings, sets, strict=True):
        if s and not (s & canonical):
            yield CoherenceFlag(
                finding_key=f.dedupe_key(),
                rule="name_mismatch",
                reason=f"declared name has no overlap with canonical {sorted(canonical)}",
            )


def _flag_geo_outlier(findings: list[Finding]) -> Iterable[CoherenceFlag]:
    by_finding: list[set[str]] = [
        {_continent_of(loc) for loc in _locations_of(f)} - {"other"}
        for f in findings
    ]
    flat = [c for cs in by_finding for c in cs]
    if not flat:
        return
    counts = Counter(flat)
    if not counts:
        return
    majority, top = counts.most_common(1)[0]
    if top < 2:
        return
    for f, cs in zip(findings, by_finding, strict=True):
        if cs and majority not in cs:
            yield CoherenceFlag(
                finding_key=f.dedupe_key(),
                rule="geo_outlier",
                reason=f"declared continent {sorted(cs)} differs from majority '{majority}'",
            )


def _flag_century_gap(findings: list[Finding]) -> Iterable[CoherenceFlag]:
    all_years: list[tuple[Finding, list[int]]] = [(f, _years_of(f)) for f in findings]
    flat = [y for _, ys in all_years for y in ys]
    if len(flat) < 3:
        return
    median = statistics.median(flat)
    for f, ys in all_years:
        if not ys:
            continue
        if any(abs(y - median) > 80 for y in ys):
            yield CoherenceFlag(
                finding_key=f.dedupe_key(),
                rule="century_gap",
                reason=f"year(s) {ys} > 80y from cluster median {int(median)}",
            )


def _flag_domain_outlier(findings: list[Finding]) -> Iterable[CoherenceFlag]:
    if len(findings) < 3:
        return
    per_finding: list[set[str]] = []
    for f in findings:
        per_finding.append(set(_domain_tags([f])))
    populated = [s for s in per_finding if s]
    if len(populated) < 3:
        return
    counter: Counter[str] = Counter()
    for s in populated:
        for t in s:
            counter[t] += 1
    if not counter:
        return
    dominant, top_count = counter.most_common(1)[0]
    if top_count < 2:
        return
    for f, s in zip(findings, per_finding, strict=True):
        if s and dominant not in s:
            yield CoherenceFlag(
                finding_key=f.dedupe_key(),
                rule="domain_outlier",
                reason=f"derived tags {sorted(s)} share no overlap with dominant '{dominant}'",
            )


# ===========================================================================
# Top-level entry
# ===========================================================================

_RULES = (
    _flag_name_mismatch,
    _flag_geo_outlier,
    _flag_century_gap,
    _flag_domain_outlier,
)


def evaluate(person: Person, findings: list[Finding]) -> CoherenceReport:
    """Run every coherence rule over the cluster's findings. Return a single
    CoherenceReport for this person. The pipeline writes the flagged keys back
    onto `person.incoherent_finding_keys` and decays `person.coherence`."""
    flags: list[CoherenceFlag] = []
    for rule in _RULES:
        flags.extend(rule(findings))
    n = max(1, len(findings))
    unique_flagged = {(fk[0], fk[1]) for fk in (f.finding_key for f in flags)}
    penalty = min(0.6, 0.15 * len(unique_flagged))
    score = round(max(0.0, 1.0 - penalty), 3) if n >= 2 else 1.0
    return CoherenceReport(person_id=person.id, score=score, flags=flags)
