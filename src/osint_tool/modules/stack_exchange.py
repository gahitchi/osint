"""Stack Exchange network user search.

Free API, generous unauthenticated daily quota (300 req/IP). We hit six top
sites in parallel for the same query.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import quote_plus

from ..extract import find_emails, find_phones, html_to_text
from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule

SITES = ("stackoverflow", "math", "physics", "serverfault", "askubuntu", "superuser")


class StackExchangeModule(BaseModule):
    name = "stack_exchange"
    category = "social"
    expansions = ("forums",)

    def applicable(self, q: Query) -> bool:
        return bool(q.name or q.username)

    async def _one_site(
        self, site: str, q: Query, http: HttpClient,
    ) -> list[dict]:
        params = "pagesize=5&order=desc&sort=reputation"
        if q.username:
            url = (
                f"https://api.stackexchange.com/2.3/users?site={site}"
                f"&inname={quote_plus(q.username)}&{params}"
            )
        else:
            url = (
                f"https://api.stackexchange.com/2.3/users?site={site}"
                f"&inname={quote_plus(q.name or '')}&{params}"
            )
        try:
            r = await http.get(url, check_robots=False)
        except Exception:
            return []
        if r.status_code != 200:
            return []
        try:
            return (r.json() or {}).get("items", [])
        except Exception:
            return []

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        results = await asyncio.gather(
            *(self._one_site(s, q, http) for s in SITES), return_exceptions=True,
        )
        for site, items in zip(SITES, results, strict=True):
            if isinstance(items, BaseException) or not items:
                continue
            for u in items:
                display = u.get("display_name") or ""
                about_text = html_to_text(
                    f"<html><body>{u.get('about_me','') or ''}</body></html>"
                )
                emails = find_emails(about_text)
                phones = find_phones(about_text)
                profile_url = u.get("link") or ""
                yield Finding(
                    module=self.name,
                    category="social",
                    type="profile",
                    title=f"{site}: {display}",
                    source_url=profile_url,
                    data={
                        "site": site,
                        "display_name": display,
                        "location": u.get("location"),
                        "reputation": u.get("reputation"),
                        "about": about_text[:500],
                    },
                    signals={
                        "username": [str(u.get("user_id", ""))],
                        **({"email": emails} if emails else {}),
                    },
                    confidence=0.65,
                )
                for em in emails:
                    yield Finding(
                        module=self.name,
                        category="social",
                        type="contact",
                        title=f"Email (from {site} bio): {em}",
                        source_url=profile_url,
                        data={"kind": "email", "value": em, "source": f"{site} bio"},
                        signals={"email": [em]},
                        confidence=0.55,
                    )
                for ph in phones:
                    yield Finding(
                        module=self.name,
                        category="social",
                        type="contact",
                        title=f"Phone (from {site} bio): {ph}",
                        source_url=profile_url,
                        data={"kind": "phone", "value": ph, "source": f"{site} bio"},
                        signals={},
                        confidence=0.5,
                    )
