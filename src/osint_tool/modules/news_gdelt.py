from __future__ import annotations

from collections.abc import AsyncIterator

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class NewsGdeltModule(BaseModule):
    name = "news_gdelt"
    category = "search"
    expansions = ("news",)

    def applicable(self, q: Query) -> bool:
        return bool(q.name or q.employer)

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        terms = []
        if q.name:
            terms.append(f'"{q.name}"')
        if q.employer:
            terms.append(f'"{q.employer}"')
        if not terms:
            return
        params = {
            "query": " ".join(terms),
            "mode": "ArtList",
            "maxrecords": "25",
            "format": "JSON",
            "sort": "DateDesc",
        }
        try:
            r = await http.get(GDELT_URL, params=params, check_robots=False)
        except Exception:
            return
        if r.status_code != 200 or not r.text.strip().startswith("{"):
            return
        data = r.json().get("articles", [])
        for art in data:
            url = art.get("url")
            if not url:
                continue
            yield Finding(
                module=self.name,
                category="search",
                type="article",
                title=art.get("title", "")[:300] or url,
                source_url=url,
                data={
                    "domain": art.get("domain"),
                    "language": art.get("language"),
                    "seendate": art.get("seendate"),
                    "socialimage": art.get("socialimage"),
                },
            )
