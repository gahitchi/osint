"""Tests for the deterministic interpretation layer."""

from datetime import UTC, datetime

from specter.interpret import interpret
from specter.schema import CoherenceFlag, CoherenceReport, Finding, Person


def _f(module: str, conf: float = 0.9) -> Finding:
    return Finding(
        module=module,
        category="academic",
        type="profile",
        title="x",
        source_url="https://example.com/x",
        confidence=conf,
        fetched_at=datetime.now(UTC),
    )


def _person(signals: dict[str, list[str]] | None = None) -> Person:
    return Person(
        id="p1",
        display_name="Test Person",
        signals=signals or {},
        finding_keys=[],
    )


def _report(flags: list[CoherenceFlag] | None = None, score: float = 1.0) -> CoherenceReport:
    return CoherenceReport(person_id="p1", score=score, flags=flags or [])


def test_empty_findings_returns_no_findings_marker():
    out = interpret(_person(), [], _report())
    assert out == "No findings."


def test_strong_match_two_signals_high_conf_no_flags():
    p = _person({"orcid": ["0000-0001"], "email": ["a@b.com"]})
    out = interpret(p, [_f("orcid", 0.95), _f("crossref", 0.9), _f("openalex", 0.85)], _report())
    assert out.startswith("Strong match.")
    assert "ORCID" in out
    assert "email" in out
    assert "3 sources" in out


def test_moderate_one_strong_signal():
    p = _person({"github_login": ["alice"]})
    out = interpret(p, [_f("github_user", 0.75)], _report())
    assert out.startswith("Moderate match.")
    assert "GitHub login" in out
    assert "single source" in out


def test_weak_no_strong_signal_low_conf():
    p = _person({})
    out = interpret(p, [_f("search_ddg", 0.55)], _report())
    assert out.startswith("Weak match.")
    assert "Name match only" in out


def test_tentative_no_strong_low_conf():
    p = _person({})
    out = interpret(p, [_f("search_ddg", 0.30)], _report())
    assert out.startswith("Tentative match.")


def test_coherence_flag_blocks_strong():
    """Even with strong signals + high conf, a coherence flag downgrades."""
    p = _person({"orcid": ["0000-0001"], "email": ["a@b.com"]})
    flag = CoherenceFlag(
        finding_key=("crossref", "https://example.com/x"),
        rule="name_mismatch",
        reason="x",
    )
    out = interpret(
        p,
        [_f("orcid", 0.95), _f("crossref", 0.95)],
        _report(flags=[flag], score=0.5),
    )
    assert not out.startswith("Strong match.")
    assert "flagged: name_mismatch" in out


def test_multiple_coherence_flags_downgrade_to_tentative():
    p = _person({"orcid": ["0000-0001"], "email": ["a@b.com"]})
    flags = [
        CoherenceFlag(finding_key=("a", "https://a/x"), rule="name_mismatch", reason="x"),
        CoherenceFlag(finding_key=("b", "https://b/x"), rule="geo_outlier", reason="y"),
    ]
    out = interpret(p, [_f("a", 0.95), _f("b", 0.9)], _report(flags=flags, score=0.2))
    assert out.startswith("Tentative match.")
    assert "geo_outlier" in out
    assert "name_mismatch" in out


def test_signal_label_order_is_stable():
    """ORCID comes before email in _STRONG_SIGNAL_KEYS — output preserves order."""
    p = _person({"email": ["a@b.com"], "orcid": ["0000-0001"]})  # input order swapped
    out = interpret(p, [_f("x", 0.9)], _report())
    assert out.index("ORCID") < out.index("email")


def test_summary_is_a_single_line():
    p = _person({"orcid": ["0000-0001"]})
    out = interpret(p, [_f("x", 0.9)], _report())
    assert "\n" not in out
