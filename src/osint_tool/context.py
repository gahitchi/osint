"""Context scoring + expansion proposer.

Given a Query, decide:
- how thin the context is (0..10 score)
- which expansions can run automatically (low risk, anchored on strong inputs)
- which expansions require the user's explicit approval (medium / high risk)

Discovery-first defaults: when the score is low, we *propose* broad scraping
rather than restricting it. The user is the one approving.
"""

from __future__ import annotations

import re
import unicodedata

from .schema import ContextAssessment, Expansion, Query

# ===========================================================================
# Expansion catalog. The single source of truth that the UI, pipeline, and
# tests all agree on.
# ===========================================================================

EXPANSION_CATALOG: dict[str, Expansion] = {
    "targeted": Expansion(
        id="targeted",
        label="Targeted lookup",
        risk="low",
        description=(
            "Fetch the specific platform profile you named, plus gravatar, "
            "PGP keyserver, and RDAP/WHOIS for the email's domain. "
            "Anchored on strong identifiers; precise."
        ),
        modules=[
            "pivot_crawler", "gravatar", "hibp_breach",
            "pgp_keys", "rdap_domain",
        ],
    ),
    "academic": Expansion(
        id="academic",
        label="Academic search",
        risk="low",
        description="ORCID, Crossref, OpenAlex by name. Author-disambiguating; low noise.",
        modules=["orcid", "crossref", "openalex"],
    ),
    "archive": Expansion(
        id="archive",
        label="Archive lookup",
        risk="low",
        description=(
            "Internet Archive snapshots for the username/email. Useful for "
            "tracking historical mentions."
        ),
        modules=["wayback"],
    ),
    "news": Expansion(
        id="news",
        label="News articles",
        risk="medium",
        description=(
            "Search global news archive (GDELT) by name + employer. "
            "Common names get noisy."
        ),
        modules=["news_gdelt"],
    ),
    "web_search": Expansion(
        id="web_search",
        label="Broad web search",
        risk="medium",
        description=(
            "DuckDuckGo search for the name in quotes. Noisy on common "
            "names but high recall."
        ),
        modules=["search_ddg"],
    ),
    "code_hosts": Expansion(
        id="code_hosts",
        label="Code-host search",
        risk="medium",
        description=(
            "GitHub user search by name/login + npm registry lookup by "
            "username. Returns extra hits when only a name is provided."
        ),
        modules=["github_user", "npm_user"],
    ),
    "forums": Expansion(
        id="forums",
        label="Q&A forums",
        risk="medium",
        description=(
            "Stack Exchange (StackOverflow, Math, Physics, Server Fault, "
            "Ask Ubuntu, Super User) user search by name/username. "
            "Surfaces self-published bios."
        ),
        modules=["stack_exchange"],
    ),
    "public_records": Expansion(
        id="public_records",
        label="Public records (US SEC)",
        risk="medium",
        description=(
            "SEC EDGAR full-text search for filings naming the person "
            "(Forms 3/4/5 = insider holdings, DEF 14A = proxy statements). "
            "Common names get noisy by design."
        ),
        modules=["sec_edgar"],
    ),
    "genealogy": Expansion(
        id="genealogy",
        label="Family tree (Wikidata)",
        risk="low",
        description=(
            "Build a generational tree from Wikidata: ancestors (3 generations), "
            "descendants (2 generations), siblings, spouses. Only works for "
            "individuals with Wikidata coverage (notable people)."
        ),
        modules=["wikidata_tree"],
    ),
    "username_fanout": Expansion(
        id="username_fanout",
        label="Cross-platform username probe",
        risk="high",
        description=(
            "Sherlock-style check of derived usernames across ~10 sites. "
            "KNOWN false-positive generator; skip if you trust a specific "
            "source platform."
        ),
        modules=["sherlock"],
    ),
}


# ===========================================================================
# Context scoring
# ===========================================================================

# Tiny embedded common-names list — penalize ambiguous queries. Deliberately
# small: only the very most common given/family names worldwide. Conservative
# so we don't flag legitimate names as ambiguous.
_COMMON_GIVEN = {
    "james", "john", "robert", "michael", "william", "david", "richard", "joseph",
    "thomas", "charles", "christopher", "daniel", "matthew", "anthony", "donald",
    "mark", "paul", "steven", "andrew", "kenneth", "jane", "mary", "patricia",
    "linda", "elizabeth", "barbara", "susan", "jennifer", "lisa", "karen",
    "li", "wei", "wang", "zhang", "chen", "yan", "yu", "ana", "maria", "juan",
    "jose", "luis", "carlos", "carmen",
}
_COMMON_FAMILY = {
    "smith", "johnson", "williams", "brown", "jones", "garcia", "miller",
    "davis", "rodriguez", "martinez", "hernandez", "lopez", "gonzalez",
    "wilson", "anderson", "thomas", "taylor", "moore", "jackson", "martin",
    "lee", "perez", "thompson", "white", "harris", "sanchez", "clark",
    "ramirez", "lewis", "robinson", "walker", "young", "allen", "king",
    "wright", "scott", "torres", "nguyen", "hill", "flores", "green",
    "li", "wang", "zhang", "chen", "liu", "yang", "huang", "zhao", "wu",
    "kim", "park", "rossi", "russo", "ferrari", "esposito", "bianchi",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z\s]", "", s)


def _is_uncommon(name: str) -> bool:
    toks = _norm(name).split()
    if not toks:
        return False
    given = toks[0]
    family = toks[-1] if len(toks) > 1 else ""
    if given in _COMMON_GIVEN and family in _COMMON_FAMILY:
        return False
    return not (not family and given in _COMMON_GIVEN)


def _looks_like_orcid(s: str | None) -> bool:
    return bool(s and re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", s))


def score(q: Query) -> int:
    s = 0
    if q.source_platform and q.username:
        s += 4
    if q.email:
        s += 3
    if q.username and _looks_like_orcid(q.username):
        s += 3
    if q.name and len(q.name.split()) >= 2:
        s += 2
    if q.name and _is_uncommon(q.name):
        s += 1
    for f in (q.location, q.employer, q.phone):
        if f:
            s += 1
    return s


# ===========================================================================
# Per-expansion triggers
# ===========================================================================

def _triggered(eid: str, q: Query) -> bool:  # noqa: PLR0911
    if eid == "targeted":
        return bool(
            (q.source_platform and q.username) or q.email
        )
    if eid == "academic":
        return bool(q.name)
    if eid == "archive":
        return bool(q.username or q.email)
    if eid == "news":
        return bool(q.name)
    if eid == "web_search":
        return bool(q.name or q.username or q.email or q.phone)
    if eid == "code_hosts":
        return bool(q.name or q.username or q.email)
    if eid == "forums":
        return bool(q.name or q.username)
    if eid == "public_records":
        return bool(q.name)
    if eid == "genealogy":
        return bool(q.name)
    if eid == "username_fanout":
        # The whole point of source_platform is to *avoid* fan-out.
        if q.source_platform:
            return False
        return bool(q.username or q.name)
    return False


def assess(q: Query) -> ContextAssessment:
    s = score(q)
    auto: list[Expansion] = []
    proposed: list[Expansion] = []
    for eid, exp in EXPANSION_CATALOG.items():
        if not _triggered(eid, q):
            continue
        # code_hosts becomes "auto" when a username is given (precise lookup)
        is_auto = exp.risk == "low" or (eid == "code_hosts" and q.username)
        (auto if is_auto else proposed).append(exp)
    return ContextAssessment(
        score=s,
        thin=(s < 4),
        auto_run=auto,
        proposed=proposed,
    )


def modules_for_expansions(expansion_ids: set[str]) -> set[str]:
    """Return the set of module names enabled by the given expansion ids."""
    out: set[str] = set()
    for eid in expansion_ids:
        exp = EXPANSION_CATALOG.get(eid)
        if exp:
            out.update(exp.modules)
    return out
