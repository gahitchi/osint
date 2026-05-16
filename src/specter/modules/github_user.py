from __future__ import annotations

from collections.abc import AsyncIterator

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule


class GithubUserModule(BaseModule):
    """GitHub public search by full name (60 req/hr unauthenticated)."""

    name = "github_user"
    category = "social"
    expansions = ("code_hosts",)

    def applicable(self, q: Query) -> bool:
        # Pivot crawler covers GitHub in much more depth; don't double-query.
        if q.source_platform == "github":
            return False
        return bool(q.name or q.username or q.email)

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        queries: list[str] = []
        if q.username:
            queries.append(f"user:{q.username}")
        if q.name:
            queries.append(f'"{q.name}" in:fullname')
        if q.email:
            queries.append(f"{q.email} in:email")

        for query in queries[:3]:
            try:
                r = await http.get(
                    "https://api.github.com/search/users",
                    params={"q": query, "per_page": "5"},
                    headers={"Accept": "application/vnd.github+json"},
                )
            except Exception:
                continue
            if r.status_code != 200:
                continue
            for item in r.json().get("items", []):
                login = item.get("login")
                if not login:
                    continue
                yield Finding(
                    module=self.name,
                    category="social",
                    type="profile",
                    title=f"GitHub: {login}",
                    source_url=item.get("html_url"),
                    data={
                        "login": login,
                        "avatar_url": item.get("avatar_url"),
                        "api_url": item.get("url"),
                    },
                    signals={"github_login": [login], "username": [login]},
                )
