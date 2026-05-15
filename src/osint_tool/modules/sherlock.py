from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from importlib import resources

from ..http import HttpClient
from ..schema import Finding, Query
from ..username_gen import candidates_from_name
from .base import BaseModule


def _load_sites() -> dict:
    text = resources.files("osint_tool.data").joinpath("sherlock_sites.json").read_text()
    return json.loads(text)


class SherlockModule(BaseModule):
    name = "sherlock"
    category = "social"
    expansions = ("username_fanout",)

    def __init__(self) -> None:
        self.sites = _load_sites()

    def applicable(self, q: Query) -> bool:
        # When the user has named a specific source platform, fan-out username
        # probing is precisely what they want to avoid (it's the false-positive
        # generator). Pivot crawler handles the targeted lookup instead.
        if q.source_platform:
            return False
        return bool(q.username or q.name or q.email)

    def _usernames(self, q: Query) -> list[str]:
        out: list[str] = []
        if q.username:
            out.append(q.username)
        if q.email:
            local = q.email.split("@")[0]
            if local and local not in out:
                out.append(local)
        if q.name:
            for c in candidates_from_name(q.name, max_candidates=4):
                if c not in out:
                    out.append(c)
        return out[:6]

    async def _check(
        self, site: str, spec: dict, username: str, http: HttpClient
    ) -> Finding | None:
        check_url = spec["url_check"].format(username)
        display_url = spec["url"].format(username)
        method = spec["method"]
        try:
            r = await http.get(check_url, check_robots=True)
        except Exception:
            return None
        ok = False
        if method == "status_code":
            ok = r.status_code == spec.get("code", 200)
        elif method == "json_not_empty":
            try:
                ok = bool(r.json())
            except Exception:
                ok = False
        elif method == "json_not_null":
            try:
                ok = r.json() is not None
            except Exception:
                ok = False
        elif method == "json_field_not_null":
            try:
                ok = r.json().get(spec["field"]) is not None
            except Exception:
                ok = False
        elif method == "json_status_ok":
            try:
                ok = r.json().get("status", {}).get("code") == 0
            except Exception:
                ok = False
        if not ok:
            return None
        return Finding(
            module=self.name,
            category="social",
            type="profile",
            title=f"{site}: {username}",
            source_url=display_url,
            data={"site": site, "username": username},
            signals={"username": [username]},
        )

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        usernames = self._usernames(q)
        if not usernames:
            return
        # Bound the fan-out: usernames * sites = ~20 * sites; cap with a semaphore.
        sem = asyncio.Semaphore(8)

        async def _bound(site: str, spec: dict, u: str) -> Finding | None:
            async with sem:
                return await self._check(site, spec, u, http)

        tasks = [
            asyncio.create_task(_bound(site, spec, u))
            for u in usernames
            for site, spec in self.sites.items()
        ]
        for coro in asyncio.as_completed(tasks):
            try:
                f = await coro
            except Exception:
                continue
            if f is not None:
                yield f
