from datetime import UTC, datetime

from osint_tool.cross_ref import is_duplicate, matches_query_fields, rescore
from osint_tool.schema import Finding, Query


def _f(**kw):
    base = dict(
        module="m1",
        category="search",
        type="mention",
        title="t",
        source_url="https://example.com/a",
        fetched_at=datetime.now(UTC),
        data={},
    )
    base.update(kw)
    return Finding(**base)


def test_matches_name_and_employer():
    q = Query(name="Tim Berners-Lee", employer="W3C")
    f = _f(title="Sir Tim Berners-Lee at W3C")
    assert set(matches_query_fields(f, q)) == {"name", "employer"}


def test_rescore_cross_source_boost():
    q = Query(name="Jane Doe", username="janedoe")
    # f2 alone (no priors) vs f2 with a corroborating prior — second should score higher.
    f1 = _f(module="m1", title="Jane Doe profile", data={"u": "janedoe"})
    rescore(f1, q, [])
    f2_alone = _f(module="m2", source_url="https://other.com/a", title="Jane Doe again")
    rescore(f2_alone, q, [])
    f2_corroborated = _f(module="m2", source_url="https://other.com/b", title="Jane Doe again")
    rescore(f2_corroborated, q, [f1])
    assert f2_corroborated.confidence > f2_alone.confidence
    assert f2_corroborated.matched_fields == ["name"]


def test_dedup():
    f = _f()
    seen = set()
    assert not is_duplicate(f, seen)
    assert is_duplicate(f, seen)
