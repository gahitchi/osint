from __future__ import annotations

import re
import unicodedata


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def candidates_from_name(full_name: str, *, max_candidates: int = 12) -> list[str]:
    """Produce common username forms from a full name. Deterministic, ordered."""
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    if not parts:
        return []

    first = _slug(parts[0])
    last = _slug(parts[-1]) if len(parts) > 1 else ""
    middle = _slug("".join(parts[1:-1])) if len(parts) > 2 else ""

    out: list[str] = []
    if first and last:
        out += [
            f"{first}{last}",
            f"{first}.{last}",
            f"{first}_{last}",
            f"{first[0]}{last}",
            f"{first}{last[0]}",
            f"{last}.{first}",
            f"{first}-{last}",
        ]
    if first:
        out.append(first)
    if last:
        out.append(last)
    if first and middle and last:
        out += [f"{first}{middle}{last}", f"{first}.{middle}.{last}"]

    seen: set[str] = set()
    uniq: list[str] = []
    for u in out:
        if 2 <= len(u) <= 30 and u not in seen:
            seen.add(u)
            uniq.append(u)
        if len(uniq) >= max_candidates:
            break
    return uniq
