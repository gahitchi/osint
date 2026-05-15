from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import quote

from ..config import Config
from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule


class HibpBreachModule(BaseModule):
    name = "hibp_breach"
    category = "breach"
    requires_key = True
    expansions = ("targeted",)

    def applicable(self, q: Query) -> bool:
        return q.email is not None

    def skip_reason(self, cfg: Config) -> str | None:
        if not cfg.hibp_api_key:
            return "HIBP_API_KEY not set (paid). Module disabled."
        return None

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        if not q.email:
            return
        cfg = http.cfg
        url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote(q.email)}?truncateResponse=false"
        try:
            r = await http.get(
                url,
                headers={"hibp-api-key": cfg.hibp_api_key or "", "User-Agent": cfg.user_agent},
                check_robots=False,
            )
        except Exception:
            return
        if r.status_code == 404:
            return  # no breaches
        if r.status_code != 200:
            return
        for b in r.json():
            yield Finding(
                module=self.name,
                category="breach",
                type="breach",
                title=f"Breach: {b.get('Name')}",
                source_url=f"https://haveibeenpwned.com/PwnedWebsites#{b.get('Name')}",
                data={
                    "domain": b.get("Domain"),
                    "breach_date": b.get("BreachDate"),
                    "data_classes": b.get("DataClasses"),
                    "verified": b.get("IsVerified"),
                },
                confidence=0.9,
            )
