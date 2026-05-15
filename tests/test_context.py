from osint_tool.context import assess, modules_for_expansions
from osint_tool.schema import Query


def test_thin_name_only():
    a = assess(Query(name="Jane Doe"))
    assert a.thin is True
    ids = {e.id for e in a.auto_run}
    assert "academic" in ids  # name → academic auto
    proposed = {e.id for e in a.proposed}
    assert "web_search" in proposed
    assert "news" in proposed
    assert "username_fanout" in proposed


def test_username_only_no_platform_offers_fanout():
    a = assess(Query(username="janed"))
    proposed = {e.id for e in a.proposed}
    assert "username_fanout" in proposed
    assert "archive" in {e.id for e in a.auto_run}


def test_source_platform_kills_fanout():
    a = assess(Query(username="janed", source_platform="github"))
    proposed = {e.id for e in a.proposed}
    assert "username_fanout" not in proposed
    auto = {e.id for e in a.auto_run}
    assert "targeted" in auto


def test_high_context_email_plus_platform():
    a = assess(Query(username="torvalds", source_platform="github", email="t@example.com"))
    assert a.thin is False
    auto = {e.id for e in a.auto_run}
    assert "targeted" in auto


def test_common_name_does_not_get_uncommon_point():
    common = assess(Query(name="John Smith"))
    uncommon = assess(Query(name="Aleksandr Karpovetsky"))
    assert uncommon.score >= common.score


def test_modules_for_expansions():
    mods = modules_for_expansions({"academic", "archive"})
    assert "orcid" in mods
    assert "crossref" in mods
    assert "openalex" in mods
    assert "wayback" in mods
    assert "sherlock" not in mods
