from osint_tool.config import Config
from osint_tool.pipeline import Job
from osint_tool.schema import Finding, Query


def _cfg(tmp_path):
    return Config(
        user_agent="t",
        contact_email=None,
        host_rps=10.0,
        max_concurrency=20,
        reports_dir=tmp_path,
        hibp_api_key=None,
    )


def _finding(**kw):
    base = dict(
        module="m", category="social", type="profile",
        title="t", source_url="https://e.com/x", data={}, signals={},
    )
    base.update(kw)
    return Finding(**base)


def test_followups_extracts_novel_email(tmp_path):
    q = Query(username="someone", source_platform="github")
    job = Job(q, _cfg(tmp_path))
    job.findings = [
        _finding(module="pivot_crawler", signals={"email": ["new@example.com"]}),
    ]
    items = job._compute_followups()
    assert any(
        it["anchor"].get("email") == "new@example.com" for it in items
    )


def test_followups_skips_existing_input_email(tmp_path):
    q = Query(email="known@example.com")
    job = Job(q, _cfg(tmp_path))
    job.findings = [
        _finding(signals={"email": ["KNOWN@example.com"]}),
    ]
    items = job._compute_followups()
    assert all(it["anchor"].get("email", "").lower() != "known@example.com" for it in items)


def test_followups_extracts_github_login(tmp_path):
    q = Query(name="Some Author")
    job = Job(q, _cfg(tmp_path))
    job.findings = [
        _finding(signals={"github_login": ["someauthor"]}),
    ]
    items = job._compute_followups()
    gh = [it for it in items if it["anchor"].get("source_platform") == "github"]
    assert gh and gh[0]["anchor"]["username"] == "someauthor"


def test_followups_skips_anchor_already_in_query(tmp_path):
    q = Query(username="someauthor", source_platform="github")
    job = Job(q, _cfg(tmp_path))
    job.findings = [
        _finding(signals={"github_login": ["someauthor"]}),
    ]
    items = job._compute_followups()
    assert not any(it["label"] == "github:someauthor" for it in items)
