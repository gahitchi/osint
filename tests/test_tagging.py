from osint_tool.schema import Finding, Person, Query
from osint_tool.tagging import tag_person


def _f(**kw):
    base = dict(
        module="m", category="academic", type="publication",
        title="A study of algorithms and compilers",
        source_url="https://example.com/x", data={}, signals={},
    )
    base.update(kw)
    return Finding(**base)


def test_publication_yields_academic_and_author():
    fs = [_f(), _f(source_url="https://example.com/y")]
    p = Person(id="x", display_name="N")
    tags = tag_person(p, fs, Query(name="X Y"))
    assert "academic" in tags
    assert "author" in tags


def test_domain_keyword_from_blob():
    fs = [_f(title="Deep learning for NLP")]
    p = Person(id="x", display_name="N")
    tags = tag_person(p, fs, Query(name="X Y"))
    assert "ai-ml" in tags


def test_institution_tag():
    fs = [_f(data={"institution": "Stanford University"})]
    p = Person(id="x", display_name="N")
    tags = tag_person(p, fs, Query(name="X Y", employer="Stanford"))
    assert any(t.startswith("@Stanford") for t in tags)


def test_prolific_author():
    fs = [_f(source_url=f"https://e.com/{i}") for i in range(6)]
    p = Person(id="x", display_name="N")
    tags = tag_person(p, fs, Query(name="X Y"))
    assert "prolific-author" in tags
    assert "author" not in tags  # the more-specific tag replaces the generic one
