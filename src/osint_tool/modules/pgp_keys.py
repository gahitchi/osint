"""keys.openpgp.org — public PGP key lookup by email.

Free, unauthenticated. Returns an ASCII-armored key block when a key is
published for the email. Key UIDs commonly take the form
`Name (comment) <email>` and frequently include *other* emails the same
person controls, which is exactly the OSINT pivot we want.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from urllib.parse import quote

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule

UID_RE = re.compile(
    r"uid\s+(?P<name>[^<\n]+?)\s*(?:\(([^)]*)\))?\s*<(?P<email>[^>]+)>",
    re.IGNORECASE,
)


class PgpKeysModule(BaseModule):
    name = "pgp_keys"
    category = "social"
    expansions = ("targeted",)

    def applicable(self, q: Query) -> bool:
        return bool(q.email)

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        if not q.email:
            return
        url = f"https://keys.openpgp.org/vks/v1/by-email/{quote(q.email)}"
        try:
            r = await http.get(url, check_robots=False)
        except Exception:
            return
        if r.status_code != 200:
            return
        body = r.text
        if "BEGIN PGP PUBLIC KEY BLOCK" not in body:
            return

        # The armored block itself doesn't decode without gpg, but
        # keys.openpgp.org also serves the UID list inline above the block on
        # the HTML version. We re-request the HTML profile URL for that, which
        # has the parsed UIDs.
        try:
            html = await http.get(
                f"https://keys.openpgp.org/search?q={quote(q.email)}",
                check_robots=False,
            )
        except Exception:
            html = None

        text = (html.text if html is not None and html.status_code == 200 else "")
        uids = list(UID_RE.finditer(text))
        seen_emails: set[str] = set()
        for u in uids:
            uid_name = u.group("name").strip()
            uid_email = u.group("email").strip().lower()
            if not uid_email or uid_email in seen_emails:
                continue
            seen_emails.add(uid_email)
            yield Finding(
                module=self.name,
                category="social",
                type="contact" if uid_email != q.email.lower() else "profile",
                title=f"PGP UID: {uid_name} <{uid_email}>",
                source_url=f"https://keys.openpgp.org/search?q={quote(uid_email)}",
                data={
                    "kind": "email",
                    "value": uid_email,
                    "source": "keys.openpgp.org PGP UID",
                    "name": uid_name,
                    "anchor_email": q.email,
                },
                signals={
                    "email": [uid_email, q.email],
                    "username": [q.email.split("@")[0]],
                },
                confidence=0.85,
            )
        # If we couldn't parse UIDs but the key exists, emit a bare profile
        # finding (still useful — proves the email anchors a key).
        if not seen_emails:
            yield Finding(
                module=self.name,
                category="social",
                type="profile",
                title=f"PGP key published for {q.email}",
                source_url=url,
                data={"anchor_email": q.email},
                signals={"email": [q.email]},
                confidence=0.7,
            )
