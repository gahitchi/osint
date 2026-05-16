"""Deterministic per-Person interpretation.

Turns the raw Person + Findings + CoherenceReport into a single human-
readable sentence the analyst can read at a glance, like:

    "Strong match. Shared ORCID + email; corroborated across 4 sources."
    "Weak match. Name only; single source; flagged: name_mismatch."

No LLM, no heuristics tuned on a held-out set — just rules over the
same signals the rest of the pipeline already computes. The output is
purely a *summary* of state, not a new claim, so it is reproducible
from the JSON report alone.
"""

from __future__ import annotations

from .schema import CoherenceReport, Finding, Person

# Signals that carry strong identity-equivalence weight (an ORCID match is
# nearly proof-of-identity; a shared name alone is not).
_STRONG_SIGNAL_KEYS = (
    "orcid",
    "github_login",
    "email",
    "gravatar_hash",
    "wikidata_qid",
    "openalex_id",
    "doi_author_pair",
)

# Human-readable labels for the strong signals (in the same order they
# appear in the Person.signals dict — the joiner preserves insertion).
_SIGNAL_LABEL = {
    "orcid": "ORCID",
    "github_login": "GitHub login",
    "email": "email",
    "gravatar_hash": "gravatar",
    "wikidata_qid": "Wikidata QID",
    "openalex_id": "OpenAlex ID",
    "doi_author_pair": "co-authored DOI",
}


def _strong_signals_present(person: Person) -> list[str]:
    """Returns the strong-signal *keys* the Person has at least one value for,
    in a stable order (the order they're listed in _STRONG_SIGNAL_KEYS)."""
    return [k for k in _STRONG_SIGNAL_KEYS if person.signals.get(k)]


def _strength(
    person: Person,
    findings: list[Finding],
    coherence: CoherenceReport | None,
) -> str:
    """Bucket the cluster into one of four strength labels.

    The thresholds intentionally err on the conservative side: 'Strong'
    requires multiple independent strong signals AND high confidence AND
    no coherence flags. Anything less drops a tier."""
    strong = _strong_signals_present(person)
    max_conf = max((f.confidence for f in findings), default=0.0)
    flags = list(coherence.flags) if coherence else []

    if flags and len(flags) >= 2:
        # Multiple coherence flags overrule signal strength.
        return "Tentative match"
    if len(strong) >= 2 and max_conf >= 0.85 and not flags:
        return "Strong match"
    if len(strong) >= 1 and max_conf >= 0.70:
        return "Moderate match"
    if max_conf >= 0.50 or len(strong) >= 1:
        return "Weak match"
    return "Tentative match"


def _why_clause(
    person: Person,
    findings: list[Finding],
    coherence: CoherenceReport | None,
) -> str:
    """Build the descriptive clause: which signals corroborated, across how
    many sources, and what (if anything) coherence flagged."""
    parts: list[str] = []

    strong = _strong_signals_present(person)
    if strong:
        labels = [_SIGNAL_LABEL[k] for k in strong]
        parts.append(f"shared {' + '.join(labels)}")
    else:
        parts.append("name match only")

    n_sources = len({f.module for f in findings})
    if n_sources >= 2:
        parts.append(f"corroborated across {n_sources} sources")
    elif n_sources == 1:
        parts.append("single source")

    if coherence and coherence.flags:
        rules = sorted({fl.rule for fl in coherence.flags})
        parts.append(f"flagged: {', '.join(rules)}")

    return "; ".join(parts)


def interpret(
    person: Person,
    findings: list[Finding],
    coherence: CoherenceReport | None = None,
) -> str:
    """Return a one-line interpretation of a Person cluster.

    `findings` should be the subset of findings owned by this Person
    (matching `person.finding_keys`). `coherence` is the report from
    `cohere.evaluate` for this Person, or None if coherence didn't run."""
    if not findings:
        return "No findings."
    strength = _strength(person, findings, coherence)
    why = _why_clause(person, findings, coherence)
    # Uppercase only the first character — .capitalize() would lowercase the
    # rest and ruin signal labels like "ORCID".
    why_sentence = why[:1].upper() + why[1:] if why else ""
    return f"{strength}. {why_sentence}."
