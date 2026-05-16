"""Tests for the PDF export endpoint + render_pdf function."""

import json

from fastapi.testclient import TestClient

from specter import main as main_mod
from specter.report_pdf import render_pdf


def _minimal_doc(job_id: str = "test-job-1") -> dict:
    return {
        "job_id": job_id,
        "query": {"name": "Test Person", "email": "t@example.com"},
        "approved_expansions": ["targeted", "academic"],
        "started_at": "2026-05-16T10:00:00+00:00",
        "finished_at": "2026-05-16T10:00:05+00:00",
        "statuses": {},
        "people": [
            {
                "id": "p1",
                "display_name": "Test Person",
                "tags": ["academic", "has-email"],
                "confidence": 0.9,
                "signals": {"orcid": ["0000-0001"], "email": ["t@example.com"]},
                "finding_keys": [["orcid", "https://orcid.org/0000-0001"]],
                "coherence": 1.0,
                "incoherent_finding_keys": [],
                "summary": "Strong match. Shared ORCID + email; corroborated across 2 sources.",
            }
        ],
        "findings": [
            {
                "module": "orcid",
                "category": "academic",
                "type": "profile",
                "title": "ORCID profile",
                "source_url": "https://orcid.org/0000-0001",
                "fetched_at": "2026-05-16T10:00:01+00:00",
                "data": {},
                "confidence": 0.95,
                "matched_fields": ["name", "email"],
                "signals": {"orcid": ["0000-0001"]},
            }
        ],
        "coherence_reports": {"p1": {"person_id": "p1", "score": 1.0, "flags": []}},
        "followups": [],
        "trees": [],
        "dropped_count": 0,
    }


def test_render_pdf_returns_pdf_bytes():
    out = render_pdf(_minimal_doc())
    assert isinstance(out, bytes)
    assert out.startswith(b"%PDF")
    # A populated PDF is not trivially small.
    assert len(out) > 1000


def test_render_pdf_empty_people():
    doc = _minimal_doc()
    doc["people"] = []
    out = render_pdf(doc)
    assert out.startswith(b"%PDF")


def test_render_pdf_escapes_html_in_user_data():
    doc = _minimal_doc()
    doc["people"][0]["display_name"] = "Eve <script>alert(1)</script>"
    # Should not raise even though the data contains < and >.
    out = render_pdf(doc)
    assert out.startswith(b"%PDF")


def test_render_pdf_with_coherence_flags():
    doc = _minimal_doc()
    doc["coherence_reports"]["p1"] = {
        "person_id": "p1",
        "score": 0.5,
        "flags": [
            {
                "finding_key": ["orcid", "https://orcid.org/0000-0001"],
                "rule": "name_mismatch",
                "reason": "Cluster name disagrees with finding name.",
            }
        ],
    }
    out = render_pdf(doc)
    assert out.startswith(b"%PDF")


def test_pdf_endpoint_404_for_unknown_job(tmp_path):
    # Isolate reports_dir per test — the Config is frozen so we rebuild it.
    original = main_mod._cfg
    main_mod._cfg = type(original)(
        user_agent=original.user_agent,
        contact_email=original.contact_email,
        host_rps=original.host_rps,
        max_concurrency=original.max_concurrency,
        reports_dir=tmp_path,
        hibp_api_key=original.hibp_api_key,
    )
    try:
        client = TestClient(main_mod.app)
        r = client.get("/reports/nonexistent.pdf")
        assert r.status_code == 404
    finally:
        main_mod._cfg = original


def test_pdf_endpoint_returns_pdf(tmp_path):
    original = main_mod._cfg
    main_mod._cfg = type(original)(
        user_agent=original.user_agent,
        contact_email=original.contact_email,
        host_rps=original.host_rps,
        max_concurrency=original.max_concurrency,
        reports_dir=tmp_path,
        hibp_api_key=original.hibp_api_key,
    )
    job_id = "pdf-export-test"
    (tmp_path / f"{job_id}.json").write_text(json.dumps(_minimal_doc(job_id)))
    try:
        client = TestClient(main_mod.app)
        r = client.get(f"/reports/{job_id}.pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")
        assert "attachment" in r.headers["content-disposition"]
    finally:
        main_mod._cfg = original
        main_mod._jobs.pop(job_id, None)
