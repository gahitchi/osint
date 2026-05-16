from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule


class GravatarModule(BaseModule):
    name = "gravatar"
    category = "social"
    expansions = ("targeted",)

    def applicable(self, q: Query) -> bool:
        return q.email is not None

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        if not q.email:
            return
        h = hashlib.md5(q.email.strip().lower().encode()).hexdigest()
        profile_url = f"https://www.gravatar.com/{h}.json"
        try:
            r = await http.get(profile_url, check_robots=False)
        except Exception:
            return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except Exception:
            return
        entries = data.get("entry", [])
        for entry in entries:
            yield Finding(
                module=self.name,
                category="social",
                type="profile",
                title=entry.get("displayName") or f"Gravatar: {h}",
                source_url=entry.get("profileUrl") or f"https://www.gravatar.com/{h}",
                data={
                    "hash": h,
                    "name": entry.get("name"),
                    "about": entry.get("aboutMe"),
                    "urls": entry.get("urls", []),
                    "accounts": entry.get("accounts", []),
                },
                signals={
                    "gravatar_hash": [h],
                    "email": [q.email],
                    "username": [
                        a.get("username") for a in entry.get("accounts", [])
                        if isinstance(a, dict) and a.get("username")
                    ],
                },
            )
