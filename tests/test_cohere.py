from osint_tool.cohere import evaluate
from osint_tool.schema import Finding, Person


def _f(module, url, **kw):
    base = dict(
        category="academic", type="profile",
        title="t", source_url=url, data={}, signals={},
    )
    base.update(kw)
    return Finding(module=module, **base)


def _person(*finds):
    return Person(
        id="p1",
        display_name="X",
        finding_keys=[f.dedupe_key() for f in finds],
    )


def test_name_mismatch_fires():
    f1 = _f("orcid", "https://orcid.org/0000-1",
            data={"given_names": "Donald", "family_names": "Knuth"})
    f2 = _f("openalex", "https://openalex.org/A1",
            data={"display_name": "Donald Knuth"})
    f3 = _f("crossref", "https://doi.org/10/x", type="publication",
            data={"authors": "Mary Shelley"})
    p = _person(f1, f2, f3)
    rep = evaluate(p, [f1, f2, f3])
    flagged = {fl.finding_key for fl in rep.flags if fl.rule == "name_mismatch"}
    assert f3.dedupe_key() in flagged
    assert f1.dedupe_key() not in flagged


def test_geo_outlier_needs_two_majority():
    a = _f("m1", "https://e.com/1", data={"location": "Stanford, California"})
    b = _f("m2", "https://e.com/2", data={"location": "San Francisco, USA"})
    c = _f("m3", "https://e.com/3", data={"location": "Berlin, Germany"})
    p = _person(a, b, c)
    rep = evaluate(p, [a, b, c])
    flagged = {fl.finding_key for fl in rep.flags if fl.rule == "geo_outlier"}
    assert c.dedupe_key() in flagged


def test_geo_no_majority_no_flag():
    # Two findings on different continents — neither is the "majority".
    a = _f("m1", "https://e.com/1", data={"location": "Berlin"})
    b = _f("m2", "https://e.com/2", data={"location": "USA"})
    p = _person(a, b)
    rep = evaluate(p, [a, b])
    flagged = {fl.finding_key for fl in rep.flags if fl.rule == "geo_outlier"}
    assert not flagged


def test_century_gap():
    a = _f("crossref", "https://doi.org/1", type="publication",
           data={"published": [[2020, 1, 1]]})
    b = _f("crossref", "https://doi.org/2", type="publication",
           data={"published": [[2018, 1, 1]]})
    c = _f("crossref", "https://doi.org/3", type="publication",
           data={"published": [[1820, 1, 1]]})  # 200 years off
    p = _person(a, b, c)
    rep = evaluate(p, [a, b, c])
    flagged = {fl.finding_key for fl in rep.flags if fl.rule == "century_gap"}
    assert c.dedupe_key() in flagged
    assert a.dedupe_key() not in flagged


def test_clean_cluster_has_score_one():
    a = _f("orcid", "https://orcid.org/1", data={"given_names": "A", "family_names": "B"})
    b = _f("openalex", "https://openalex.org/1", data={"display_name": "A B"})
    p = _person(a, b)
    rep = evaluate(p, [a, b])
    assert rep.score == 1.0
    assert rep.flags == []
