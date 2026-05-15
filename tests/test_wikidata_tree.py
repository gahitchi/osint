"""Tests for the Wikidata genealogy module — SPARQL responses mocked."""

import httpx
import pytest
import respx

from osint_tool.config import Config
from osint_tool.http import HttpClient
from osint_tool.modules.wikidata_tree import WikidataTreeModule
from osint_tool.schema import Query


def _cfg(tmp_path):
    return Config(
        user_agent="t",
        contact_email=None,
        host_rps=100.0,
        max_concurrency=20,
        reports_dir=tmp_path,
        hibp_api_key=None,
    )


def _sparql_bindings(rows):
    return {"results": {"bindings": rows}}


def _bind(value, var_type="uri"):
    return {"type": var_type, "value": value}


@pytest.mark.asyncio
@respx.mock
async def test_no_candidates_no_findings(tmp_path):
    respx.get("https://www.wikidata.org/w/api.php").mock(
        return_value=httpx.Response(200, json={"search": []})
    )
    http = HttpClient(_cfg(tmp_path))
    try:
        findings = []
        async for f in WikidataTreeModule().run(Query(name="No Such Person"), http):
            findings.append(f)
        assert findings == []
    finally:
        await http.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_candidate_without_family_data_skipped(tmp_path):
    respx.get("https://www.wikidata.org/w/api.php").mock(
        return_value=httpx.Response(200, json={
            "search": [{"id": "Q12345", "label": "Someone Random", "description": "n/a"}],
        })
    )
    # All SPARQL queries return empty rows
    respx.get("https://query.wikidata.org/sparql").mock(
        return_value=httpx.Response(200, json=_sparql_bindings([]))
    )
    http = HttpClient(_cfg(tmp_path))
    try:
        findings = [f async for f in WikidataTreeModule().run(Query(name="x"), http)]
        assert findings == []
    finally:
        await http.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_focal_with_parents_yields_tree(tmp_path):
    respx.get("https://www.wikidata.org/w/api.php").mock(
        return_value=httpx.Response(200, json={
            "search": [{
                "id": "Q11930", "label": "Donald Knuth",
                "description": "computer scientist",
            }],
        })
    )
    # 1st SPARQL: focal-only query (single row)
    focal_rows = _sparql_bindings([
        {
            "pLabel": _bind("Donald Knuth", "literal"),
            "desc": _bind("American computer scientist", "literal"),
            "dob": _bind("+1938-01-10T00:00:00Z", "literal"),
        },
    ])
    # 2nd SPARQL: peers (siblings + spouses)
    peer_rows = _sparql_bindings([
        {
            "p": _bind("http://www.wikidata.org/entity/Q200000"),
            "pLabel": _bind("Spouse Knuth", "literal"),
            "relation": _bind("spouse", "literal"),
        },
    ])
    # 3rd SPARQL: ancestors
    ancestor_rows = _sparql_bindings([
        {
            "p": _bind("http://www.wikidata.org/entity/Q100001"),
            "pLabel": _bind("Father Knuth", "literal"),
            "dob": _bind("+1900-01-01T00:00:00Z", "literal"),
            "dod": _bind("+1965-01-01T00:00:00Z", "literal"),
        },
    ])
    # 4th SPARQL: descendants (empty)
    descendants_rows = _sparql_bindings([])
    # 5th SPARQL: focal's direct parents
    direct_parents = _sparql_bindings([
        {
            "p": _bind("http://www.wikidata.org/entity/Q100001"),
            "pLabel": _bind("Father Knuth", "literal"),
        },
    ])
    # 6th: focal's direct children (empty)
    direct_children = _sparql_bindings([])

    call_count = {"n": 0}

    def sparql_router(_req):
        call_count["n"] += 1
        n = call_count["n"]
        responses = [
            focal_rows, peer_rows, ancestor_rows, descendants_rows,
            direct_parents, direct_children,
        ]
        return httpx.Response(200, json=responses[min(n - 1, len(responses) - 1)])

    respx.get("https://query.wikidata.org/sparql").mock(side_effect=sparql_router)

    http = HttpClient(_cfg(tmp_path))
    try:
        findings = [f async for f in WikidataTreeModule().run(Query(name="Donald Knuth"), http)]
    finally:
        await http.aclose()

    assert len(findings) == 1
    tree = findings[0].data["tree"]
    assert tree["focal_qid"] == "Q11930"
    assert tree["focal_label"] == "Donald Knuth"
    # Focal + spouse + father
    names = {n["name"] for n in tree["nodes"]}
    assert "Donald Knuth" in names
    assert "Spouse Knuth" in names
    assert "Father Knuth" in names
    # Generation assignment
    by_qid = {n["qid"]: n for n in tree["nodes"]}
    assert by_qid["Q11930"]["generation"] == 0
    assert by_qid["Q11930"]["relation"] == "focal"
    assert by_qid["Q100001"]["generation"] == -1
    # Edge from father → focal
    edge_set = {tuple(e) for e in tree["edges"]}
    assert ("Q100001", "Q11930") in edge_set


@pytest.mark.asyncio
@respx.mock
async def test_year_normalization(tmp_path):
    """+1938-01-10 → '1938'; -0050 → '-0050'."""
    respx.get("https://www.wikidata.org/w/api.php").mock(
        return_value=httpx.Response(200, json={
            "search": [{"id": "Q1", "label": "Test", "description": "x"}],
        })
    )
    focal = _sparql_bindings([
        {
            "pLabel": _bind("Test Person", "literal"),
            "dob": _bind("-0050-00-00T00:00:00Z", "literal"),
        },
    ])
    peers = _sparql_bindings([
        {
            "p": _bind("http://www.wikidata.org/entity/Q2"),
            "pLabel": _bind("Sibling", "literal"),
            "relation": _bind("sibling", "literal"),
        },
    ])
    empty = _sparql_bindings([])

    call_count = {"n": 0}

    def sparql_router(_req):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(200, json=focal)
        if call_count["n"] == 2:
            return httpx.Response(200, json=peers)
        return httpx.Response(200, json=empty)

    respx.get("https://query.wikidata.org/sparql").mock(side_effect=sparql_router)

    http = HttpClient(_cfg(tmp_path))
    try:
        findings = [f async for f in WikidataTreeModule().run(Query(name="x"), http)]
    finally:
        await http.aclose()

    tree = findings[0].data["tree"]
    focal = next(n for n in tree["nodes"] if n["qid"] == "Q1")
    assert focal["birth"] == "-0050"
