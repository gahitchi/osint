"""Verify the pipeline only schedules modules whose expansions are approved."""

from specter.config import Config
from specter.pipeline import Job
from specter.schema import Query


def _cfg(tmp_path):
    return Config(
        user_agent="t",
        contact_email=None,
        host_rps=10.0,
        max_concurrency=20,
        reports_dir=tmp_path,
        hibp_api_key=None,
    )


def test_only_approved_modules_selected(tmp_path):
    job = Job(
        Query(name="Jane Doe", username="janed"),
        _cfg(tmp_path),
        approved_expansions={"academic"},
    )
    chosen = {m.name for m in job._select_modules()}
    assert chosen <= {"orcid", "crossref", "openalex"}
    assert "search_ddg" not in chosen
    assert "sherlock" not in chosen


def test_modules_without_any_approved_expansion_skipped(tmp_path):
    job = Job(
        Query(name="Jane Doe"),
        _cfg(tmp_path),
        approved_expansions=set(),
    )
    chosen = {m.name for m in job._select_modules()}
    assert chosen == set()


def test_targeted_includes_pivot_crawler(tmp_path):
    job = Job(
        Query(username="torvalds", source_platform="github"),
        _cfg(tmp_path),
        approved_expansions={"targeted"},
    )
    chosen = {m.name for m in job._select_modules()}
    assert "pivot_crawler" in chosen
    assert "sherlock" not in chosen
