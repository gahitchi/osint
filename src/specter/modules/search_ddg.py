from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule


class SearchDdgModule(BaseModule):
    name = "search_ddg"
    category = "search"
    expansions = ("web_search",)

    def applicable(self, q: Query) -> bool:
        return bool(q.name or q.username or q.email or q.phone)

    def _queries(self, q: Query) -> list[str]:
        out: list[str] = []
        if q.name:
            quoted = f'"{q.name}"'
            out.append(quoted)
            for extra in (q.location, q.employer):
                if extra:
                    out.append(f'{quoted} "{extra}"')
        if q.email:
            out.append(f'"{q.email}"')
        if q.username:
            out.append(f'"{q.username}"')
        if q.phone:
            out.append(f'"{q.phone}"')
        return out[:5]

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        # duckduckgo-search is sync; run in a worker thread.
        from duckduckgo_search import DDGS  # noqa: PLC0415 - lazy import keeps cold-start fast

        def _search(query: str) -> list[dict]:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=10))

        for query in self._queries(q):
            try:
                results = await asyncio.to_thread(_search, query)
            except Exception:
                continue
            for r in results:
                url = r.get("href") or r.get("url")
                if not url:
                    continue
                yield Finding(
                    module=self.name,
                    category="search",
                    type="mention",
                    title=r.get("title", "")[:300] or url,
                    source_url=url,
                    data={"snippet": r.get("body", "")[:500]},
                )
