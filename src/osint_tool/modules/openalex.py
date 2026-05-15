from __future__ import annotations

from collections.abc import AsyncIterator

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule


class OpenAlexModule(BaseModule):
    name = "openalex"
    category = "academic"
    expansions = ("academic",)

    def applicable(self, q: Query) -> bool:
        return bool(q.name)

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        if not q.name:
            return
        parts = q.name.lower().split()
        family = parts[-1] if parts else None
        given = parts[0] if parts else None
        try:
            r = await http.get(
                "https://api.openalex.org/authors",
                params={"search": q.name, "per-page": "10"},
            )
        except Exception:
            return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except Exception:
            return
        for a in data.get("results", []):
            url = a.get("id")
            if not url:
                continue
            inst = (a.get("last_known_institution") or {}).get("display_name")
            signals = {"openalex_id": [url]}
            if a.get("orcid"):
                # OpenAlex returns full URL; normalize to bare ID for cross-module merge.
                orcid_id = a["orcid"].rsplit("/", 1)[-1]
                signals["orcid"] = [orcid_id]
            if inst:
                signals["institution"] = [inst]
            # Cross-module bridge: if this OpenAlex author's display_name contains
            # both query name tokens, emit the same doi_author_pair Crossref emits.
            disp = (a.get("display_name") or "").lower()
            if given and family and given in disp and family in disp:
                signals["doi_author_pair"] = [f"{given}-{family}"]
            yield Finding(
                module=self.name,
                category="academic",
                type="profile",
                title=f"OpenAlex: {a.get('display_name','')}",
                source_url=url,
                data={
                    "openalex_id": url,
                    "orcid": a.get("orcid"),
                    "works_count": a.get("works_count"),
                    "cited_by_count": a.get("cited_by_count"),
                    "institution": inst,
                },
                signals=signals,
            )
