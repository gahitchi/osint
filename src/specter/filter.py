"""Pre-cluster relevance filter.

Drops or demotes findings that don't actually refer to the query subject. Run
once per finding before it's added to the job state. Returns one of:

- "keep"   : finding is on-topic, store and surface in UI
- "demote" : finding might match but lacks corroboration. Store, but mark
             low confidence; cluster only if linked by strong signals.
- "drop"   : finding is almost certainly noise. Discard.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from .names import family_first, has_token_word_boundary
from .schema import Finding, Query

Verdict = Literal["keep", "demote", "drop"]

# Tokens of the query name must all appear as whole words within this many
# characters of each other in the blob for a "keep" verdict. Generous enough to
# tolerate middle names ("Donald Ervin Knuth") and inverted forms ("Knuth, Donald")
# but tight enough to reject coincidental co-occurrence elsewhere on a page.
NAME_CLUSTER_SPAN = 80


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower())


def _name_tokens(name: str | None) -> list[str]:
    if not name:
        return []
    return [t for t in _norm(name).split() if len(t) >= 2]


def _blob(f: Finding) -> str:
    """Searchable text for relevance checks. Only includes fields that describe
    the *content* of the result — never the search query that produced it."""
    parts = [f.title or "", str(f.source_url)]
    for k in ("snippet", "aboutMe", "about", "name", "authors", "container", "institution"):
        v = f.data.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.extend(x for x in v if isinstance(x, str))
    return _norm(" ".join(parts))


def _full_name_match(blob: str, name_toks: list[str]) -> bool:
    """All name tokens (or their variants — nicknames, transliterations,
    fuzzy near-matches) present as **whole words** within
    NAME_CLUSTER_SPAN characters in `blob`, in any order.

    Variants are computed by `specter.names`; this lifts the substring
    false-positive class ("Jane Doe" no longer matches "Janet Doersen")
    while still tolerating legitimate variants ("Bob Smith" matches
    "Robert Smith").
    """
    if not name_toks:
        return False
    if len(name_toks) == 1:
        return bool(has_token_word_boundary(blob, name_toks[0], fuzzy=True))

    positions_per_token: list[list[int]] = []
    for t in name_toks:
        ms = has_token_word_boundary(blob, t, fuzzy=True)
        if not ms:
            return False
        positions_per_token.append(ms)

    indices = [0] * len(positions_per_token)
    while True:
        current = [positions_per_token[i][indices[i]] for i in range(len(name_toks))]
        spread = max(current) - min(current)
        if spread < NAME_CLUSTER_SPAN:
            return True
        min_i = min(range(len(current)), key=lambda i: current[i])
        indices[min_i] += 1
        if indices[min_i] >= len(positions_per_token[min_i]):
            return False


def _username_overlaps_name(usernames: list[str], name_toks: list[str]) -> bool:
    """Salvage rule for profile-type findings: a username that *contains* a name
    token as a whole sub-sequence (still substring-style here, because usernames
    don't have whitespace and run tokens together: 'timbernerslee').

    To keep this conservative, require the token length be ≥3 — short ones
    ('li', 'wu') give too many spurious matches inside usernames."""
    for u in usernames:
        u_low = u.lower()
        for t in name_toks:
            if len(t) >= 3 and t in u_low:
                return True
    return False


def _has_strong_signal_match(f: Finding, q: Query) -> bool:
    """Does the finding carry a signal that *exactly* matches an input field?"""
    sig = f.signals or {}
    if q.email and any(e.lower() == q.email.lower() for e in sig.get("email", [])):
        return True
    if q.username and any(u.lower() == q.username.lower() for u in sig.get("username", [])):
        return True
    if q.email:
        local = q.email.split("@")[0].lower()
        if any(u.lower() == local for u in sig.get("username", [])):
            return True
    return False


def classify(f: Finding, q: Query) -> Verdict:  # noqa: PLR0911
    blob = _blob(f)
    name_toks = _name_tokens(q.name)

    # 1. Strong signal match → keep, regardless of name.
    if _has_strong_signal_match(f, q):
        return "keep"

    # 2. Require the full name (as whole words, clustered) when a name is given.
    if name_toks:
        if _full_name_match(blob, name_toks):
            return "keep"
        # Locale: try the family-first → given-last swap (e.g. user input
        # "Wang Xiaoming" but blob has "Xiaoming Wang").
        swapped = family_first(q.name or "")
        if swapped:
            swapped_toks = _name_tokens(swapped)
            if swapped_toks and _full_name_match(blob, swapped_toks):
                return "keep"
        # Profile-type findings can still be salvaged via the username, since
        # platform handles squash tokens ("timbernerslee").
        if f.type == "profile":
            usernames = [
                *f.signals.get("username", []),
                *f.signals.get("github_login", []),
            ]
            if _username_overlaps_name(usernames, name_toks):
                return "demote"
            return "drop"
        if f.type in ("mention", "article"):
            return "drop"
        return "demote"

    # 3. No name in query: rely on the strong-signal check above; with no name
    #    and no strong signal, the finding can't be tied — demote.
    return "demote"
