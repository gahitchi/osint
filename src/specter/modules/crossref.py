from __future__ import annotations

from collections.abc import AsyncIterator

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule


class CrossrefModule(BaseModule):
    name = "crossref"
    category = "academic"
    expansions = ("academic",)

    def applicable(self, q: Query) -> bool:
        return bool(q.name)

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        if not q.name:
            return
        params = {"query.author": q.name, "rows": "10"}
        try:
            r = await http.get("https://api.crossref.org/works", params=params)
        except Exception:
            return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except Exception:
            return
        family_target = q.name.split()[-1].lower() if q.name else None
        for item in data.get("message", {}).get("items", []):
            url = item.get("URL")
            if not url:
                continue
            title = (item.get("title") or ["(untitled)"])[0]
            author_list = item.get("author") or []
            authors = ", ".join(
                f"{a.get('given','')} {a.get('family','')}".strip()
                for a in author_list
            )
            # Strong cross-paper signal: same DOI-prefix + same surname authors
            # often indicate the same researcher across this set of works.
            orcids = [a.get("ORCID", "").rsplit("/", 1)[-1] for a in author_list if a.get("ORCID")]
            doi_pairs = []
            if family_target:
                for a in author_list:
                    if (a.get("family") or "").lower() == family_target:
                        given = (a.get("given") or "").lower().strip()
                        if given:
                            doi_pairs.append(f"{given}-{family_target}")
            signals = {}
            if orcids:
                signals["orcid"] = orcids
            if doi_pairs:
                signals["doi_author_pair"] = list(set(doi_pairs))
            yield Finding(
                module=self.name,
                category="academic",
                type="publication",
                title=title[:300],
                source_url=url,
                data={
                    "authors": authors,
                    "type": item.get("type"),
                    "doi": item.get("DOI"),
                    "published": item.get("issued", {}).get("date-parts"),
                    "container": (item.get("container-title") or [None])[0],
                },
                signals=signals,
            )
