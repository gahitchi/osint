"""npm registry user lookup. Free, no auth.

The CouchDB-style user document at registry.npmjs.org/-/user/org.couchdb.user:{u}
exposes the npm username and frequently a real name + the email the user
opted to publish.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule


class NpmUserModule(BaseModule):
    name = "npm_user"
    category = "social"
    expansions = ("code_hosts",)

    def applicable(self, q: Query) -> bool:
        return bool(q.username)

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        if not q.username:
            return
        url = f"https://registry.npmjs.org/-/user/org.couchdb.user:{q.username}"
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

        email = data.get("email")
        name = data.get("name") or q.username
        signals: dict[str, list[str]] = {"username": [q.username]}
        if email:
            signals["email"] = [email]

        yield Finding(
            module=self.name,
            category="social",
            type="profile",
            title=f"npm: {name}",
            source_url=f"https://www.npmjs.com/~{q.username}",
            data={
                "username": q.username,
                "name": name,
                "email": email,
            },
            signals=signals,
            confidence=0.7,
        )

        if email and email.lower() != (q.email or "").lower():
            yield Finding(
                module=self.name,
                category="social",
                type="contact",
                title=f"Email (npm profile): {email}",
                source_url=f"https://www.npmjs.com/~{q.username}",
                data={"kind": "email", "value": email, "source": "npm profile"},
                signals={"email": [email], "username": [q.username]},
                confidence=0.7,
            )
