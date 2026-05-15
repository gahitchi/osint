from __future__ import annotations

from collections.abc import AsyncIterator

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule


class OrcidModule(BaseModule):
    name = "orcid"
    category = "academic"
    expansions = ("academic",)

    def applicable(self, q: Query) -> bool:
        return bool(q.name)

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        if not q.name:
            return
        parts = q.name.split()
        given = parts[0]
        family = parts[-1] if len(parts) > 1 else None
        qstr = f'given-names:"{given}"'
        if family:
            qstr += f' AND family-name:"{family}"'
        try:
            r = await http.get(
                "https://pub.orcid.org/v3.0/expanded-search/",
                params={"q": qstr, "rows": "10"},
                headers={"Accept": "application/json"},
            )
        except Exception:
            return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except Exception:
            return
        for res in data.get("expanded-result") or []:
            oid = res.get("orcid-id")
            if not oid:
                continue
            inst = res.get("institution-name")
            inst_list = [inst] if isinstance(inst, str) else (list(inst) if inst else [])
            yield Finding(
                module=self.name,
                category="academic",
                type="profile",
                title=f"ORCID: {res.get('given-names','')} {res.get('family-names','')}".strip(),
                source_url=f"https://orcid.org/{oid}",
                data={
                    "orcid_id": oid,
                    "given_names": res.get("given-names"),
                    "family_names": res.get("family-names"),
                    "institution": inst_list,
                },
                signals={
                    "orcid": [oid],
                    "institution": [i for i in inst_list if isinstance(i, str)],
                },
            )
