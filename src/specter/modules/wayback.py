from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import quote_plus

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule


class WaybackModule(BaseModule):
    """Search the Internet Archive for snapshots whose URL contains the username
    or email (e.g. profile pages that may no longer exist on the live web)."""

    name = "wayback"
    category = "search"
    expansions = ("archive",)

    def applicable(self, q: Query) -> bool:
        return bool(q.username or q.email)

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        needle = q.username or (q.email.split("@")[0] if q.email else None)
        if not needle:
            return
        url = (
            "https://web.archive.org/cdx/search/cdx"
            f"?url=*{quote_plus(needle)}*&output=json&limit=15&filter=statuscode:200"
        )
        try:
            r = await http.get(url, check_robots=False)
        except Exception:
            return
        if r.status_code != 200:
            return
        try:
            rows = r.json()
        except Exception:
            return
        # First row is the header
        for row in rows[1:]:
            if len(row) < 3:
                continue
            ts, original = row[1], row[2]
            snap = f"https://web.archive.org/web/{ts}/{original}"
            yield Finding(
                module=self.name,
                category="search",
                type="mention",
                title=f"Archived snapshot: {original}"[:300],
                source_url=snap,
                data={"timestamp": ts, "original_url": original},
            )
