"""SEC EDGAR full-text search — US securities filings by name.

Free, no auth. Forms 3/4/5 capture insider holdings; DEF 14A is proxy
statements naming officers/directors. Each hit is a real filing with a
permanent URL on the SEC's site.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import quote_plus

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule


class SecEdgarModule(BaseModule):
    name = "sec_edgar"
    category = "academic"  # closest existing category — public records of professional activity
    expansions = ("public_records",)

    def applicable(self, q: Query) -> bool:
        return bool(q.name)

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        if not q.name:
            return
        # The search-index API expects URL-encoded quoted name; forms filter is
        # comma-separated.
        url = (
            "https://efts.sec.gov/LATEST/search-index"
            f"?q=%22{quote_plus(q.name)}%22&forms=4,3,5,DEF+14A&dateRange=custom"
        )
        try:
            r = await http.get(url, check_robots=False)
        except Exception:
            return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except Exception:
            return

        hits = (data.get("hits") or {}).get("hits") or []
        for h in hits[:15]:
            src = h.get("_source") or {}
            adsh = h.get("_id", "")  # accession-no, e.g. "0000123-22-000001:doc"
            adsh_clean = adsh.split(":")[0].replace("-", "")
            ciks = src.get("ciks") or []
            primary_cik = ciks[0] if ciks else None
            display = src.get("display_names") or [""]
            title = ", ".join(display) or src.get("form", "filing")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{primary_cik}/{adsh_clean}/"
                if primary_cik
                else "https://efts.sec.gov/"
            )
            yield Finding(
                module=self.name,
                category="academic",
                type="article",
                title=f"SEC {src.get('form','')}: {title}"[:300],
                source_url=filing_url,
                data={
                    "form": src.get("form"),
                    "filed": src.get("file_date"),
                    "ciks": ciks,
                    "display_names": display,
                },
                confidence=0.6,
            )
