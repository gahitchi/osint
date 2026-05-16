"""Name-aware token matching.

Three problems this module solves:

1. **Nicknames.** "Bob Smith" and "Robert Smith" probably refer to the same
   person; "Bob" must word-match "Robert" in title text.
2. **Transliteration & accents.** "Müller" and "Mueller" and "Muller" should
   match; "Aleksandr" and "Alexander" should match. ASCII fold via
   `anyascii` plus a small variant table.
3. **Locale-aware family-name order.** "Wang Xiaoming" and "Xiaoming Wang"
   refer to the same person; we accept both orderings when the leading token
   is a known East Asian / Hungarian family name.

Plus a 4th fallback: Jaro-Winkler fuzzy match for typos, capped at tokens of
length ≥ 4 to avoid pathological pairings on 2-letter names.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources

from anyascii import anyascii
from rapidfuzz.distance import JaroWinkler

# Tokens shorter than this never trigger fuzzy matching (false positives on
# short common names like "Li", "Wu", "An", "El").
MIN_FUZZY_LEN = 4
FUZZY_THRESHOLD = 0.92

# Small set of well-known family-name-first locales. Names where the *first*
# token is in this set imply the second is the given name; we accept the
# swapped ordering for matching.
_FAMILY_FIRST_NAMES = {
    # East Asian (top 30-ish)
    "wang", "li", "zhang", "liu", "chen", "yang", "huang", "zhao", "wu", "zhou",
    "xu", "sun", "ma", "zhu", "hu", "guo", "he", "gao", "lin", "luo",
    "song", "zheng", "xie", "han", "tang", "feng", "yu", "dong",
    "kim", "park", "choi", "jung", "kang", "cho", "yoon",
    "nguyen", "tran", "le", "pham", "hoang", "phan", "vu", "dang", "bui",
    "sato", "suzuki", "takahashi", "tanaka", "watanabe", "ito", "yamamoto",
    "nakamura", "kobayashi", "kato", "yoshida", "yamada",
    # Hungarian common family names
    "szabo", "nagy", "kovacs", "horvath", "toth", "varga", "kiss", "molnar",
    "papp", "balogh", "farkas",
}


@lru_cache(maxsize=1)
def _nicknames() -> dict[str, set[str]]:
    raw = json.loads(
        resources.files("specter.data").joinpath("nicknames.json").read_text()
    )
    # Bidirectional: each variant maps back to its canonical, plus all sibling
    # variants. Stored lowercase.
    table: dict[str, set[str]] = {}
    for canonical, variants in raw.items():
        family = {canonical.lower(), *(v.lower() for v in variants)}
        for member in family:
            table.setdefault(member, set()).update(family)
    return table


# Hand-rolled supplementary transliteration table for variants that anyascii
# alone won't catch (since they're spelling differences, not character
# differences). Each entry: canonical_lower → set of accepted spellings.
_SPELL_VARIANTS: dict[str, set[str]] = {
    "alexander": {"aleksandr", "aleksander", "alexandr", "alexandre", "oleksandr"},
    "alexandra": {"aleksandra", "alexandre"},
    "yuri": {"iury", "iurii", "yury", "yuriy"},
    "dmitri": {"dmitry", "dmitriy", "dimitri"},
    "muhammad": {"mohammad", "mohamed", "mohammed", "muhammed", "mohamad"},
    "ahmed": {"ahmad"},
    "muller": {"mueller", "moeller", "moller"},
    "schmidt": {"schmitt", "schmid"},
    "weber": {"webber"},
    "koh": {"ko", "go"},
    "li": {"lee", "ly", "ri"},
    "joon": {"jun", "jung"},
    "isabella": {"izabela", "isabela"},
    "philippe": {"philip", "phillip", "filip", "phillipe"},
}
# Make it bidirectional too
_SPELL_VARIANTS_BIDI: dict[str, set[str]] = {}
for canon, alts in _SPELL_VARIANTS.items():
    members = {canon, *alts}
    for m in members:
        _SPELL_VARIANTS_BIDI.setdefault(m, set()).update(members)


def _ascii_fold(s: str) -> str:
    """Aggressive ASCII fold using anyascii (handles diacritics, CJK, cyrillic,
    arabic, etc.) and lowercases. Trims to letters and digits."""
    return re.sub(r"[^a-z0-9]+", "", anyascii(s).lower())


def variants(token: str) -> set[str]:
    """Lowercase variant set of `token`: itself, ASCII fold, nickname
    expansions, common spelling variants. All lowercase, alphanumeric-only."""
    base = token.lower()
    folded = _ascii_fold(token)
    out: set[str] = {base}
    if folded:
        out.add(folded)
    out |= _nicknames().get(base, set())
    out |= _nicknames().get(folded, set())
    out |= _SPELL_VARIANTS_BIDI.get(folded, set())
    return out


def has_token_word_boundary(
    blob: str, token: str, *, fuzzy: bool = False
) -> list[int]:
    """Return the start positions in `blob` where `token` (or any variant)
    appears as a whole word. `blob` should already be lowercased and
    ASCII-folded — same normalization used by the filter."""
    if not token or not blob:
        return []
    starts: list[int] = []
    seen: set[int] = set()
    for v in variants(token):
        if not v:
            continue
        for m in re.finditer(rf"\b{re.escape(v)}\b", blob):
            if m.start() not in seen:
                seen.add(m.start())
                starts.append(m.start())
    if not starts and fuzzy and len(_ascii_fold(token)) >= MIN_FUZZY_LEN:
        # Jaro-Winkler against each whole word in the blob.
        canon = _ascii_fold(token)
        for m in re.finditer(r"\b[a-z][a-z0-9]{2,}\b", blob):
            word = m.group(0)
            if len(word) < MIN_FUZZY_LEN:
                continue
            if JaroWinkler.normalized_similarity(canon, word) >= FUZZY_THRESHOLD:
                starts.append(m.start())
    starts.sort()
    return starts


def family_first(name: str) -> str | None:
    """If `name` looks like family-name-first (East Asian / Hungarian), return
    a swapped version. Otherwise None."""
    toks = re.split(r"\s+", name.strip())
    if len(toks) != 2:
        return None
    if _ascii_fold(toks[0]) in _FAMILY_FIRST_NAMES:
        return f"{toks[1]} {toks[0]}"
    return None
