import csv
import io
import json

from fastapi.testclient import TestClient

from specter import main as main_mod


def test_csv_endpoint_serves_rows(tmp_path, monkeypatch):
    # Point the reports dir at tmp_path and write a small fixture. Restore
    # the original cfg via monkeypatch so we don't leak state to other tests.
    original = main_mod._cfg
    swapped = type(original)(  # type: ignore[call-arg]
        user_agent=original.user_agent,
        contact_email=original.contact_email,
        host_rps=original.host_rps,
        max_concurrency=original.max_concurrency,
        reports_dir=tmp_path,
        hibp_api_key=original.hibp_api_key,
    )
    monkeypatch.setattr(main_mod, "_cfg", swapped)
    report = {
        "job_id": "abc",
        "people": [{
            "id": "p1",
            "display_name": "Jane Doe",
            "tags": ["academic", "@MIT"],
            "finding_keys": [["orcid", "https://orcid.org/0000-1"]],
        }],
        "findings": [{
            "module": "orcid",
            "category": "academic",
            "type": "profile",
            "title": "ORCID: Jane Doe",
            "source_url": "https://orcid.org/0000-1",
            "confidence": 0.8,
            "matched_fields": ["name"],
            "fetched_at": "2026-05-15T00:00:00Z",
        }],
    }
    (tmp_path / "abc.json").write_text(json.dumps(report))

    client = TestClient(main_mod.app)
    r = client.get("/reports/abc.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")

    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][0] == "person_id"
    assert rows[1][0] == "p1"
    assert rows[1][1] == "Jane Doe"
    assert "academic" in rows[1][2]
    assert rows[1][3] == "orcid"
    assert rows[1][7] == "https://orcid.org/0000-1"
