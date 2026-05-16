from specter.cluster import cluster
from specter.schema import Finding, Query


def _f(module, url, **kw):
    base = dict(
        category="academic", type="profile", title="t",
        source_url=url, data={}, signals={},
    )
    base.update(kw)
    return Finding(module=module, **base)


def test_merge_by_orcid():
    fs = [
        _f("orcid", "https://orcid.org/0000-1", signals={"orcid": ["0000-1"]}),
        _f("openalex", "https://api.openalex.org/A1", signals={"orcid": ["0000-1"]}),
        _f("crossref", "https://doi.org/10/x", type="publication",
           signals={"orcid": ["0000-1"]}),
    ]
    persons = cluster(fs, Query(name="Some Author"))
    assert len(persons) == 1


def test_anchor_collapses_unsigned():
    # Three findings with no strong signals: they collapse into one anchor person.
    fs = [
        _f("search_ddg", "https://example.com/a", type="mention"),
        _f("news_gdelt", "https://news.com/b", type="article"),
        _f("wayback", "https://web.archive.org/x", type="mention"),
    ]
    persons = cluster(fs, Query(name="Donald Knuth"))
    assert len(persons) == 1


def test_distinct_orcid_distinct_person():
    fs = [
        _f("orcid", "https://orcid.org/0000-1", signals={"orcid": ["0000-1"]}),
        _f("orcid", "https://orcid.org/0000-2", signals={"orcid": ["0000-2"]}),
    ]
    persons = cluster(fs, Query(name="Common Name"))
    assert len(persons) == 2


def test_short_username_does_not_merge():
    # 'ti' is too short to be a strong signal — should not collapse separate accts
    fs = [
        _f("sherlock", "https://a.test/ti", signals={"username": ["ti"]}),
        _f("github_user", "https://github.com/ti", signals={"github_login": ["ti"]}),
    ]
    persons = cluster(fs, Query(name="Some Name"))
    # github_login is strong; username 'ti' is not → 1 cluster via github_login? No:
    # only github_user has github_login; sherlock has weak username → goes into anchor.
    # Result: two clusters (one for github, one anchor).
    assert len(persons) == 2
