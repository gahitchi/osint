"""RDAP — domain registration data lookup.

Free via rdap.org which proxies the authoritative registry. Useful when the
target's email is on a non-free domain — the WHOIS-equivalent record often
leaks names/emails/phones/addresses (especially for older registrations not
behind privacy services).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..http import HttpClient
from ..schema import Finding, Query
from .base import BaseModule

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "yahoo.fr",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "proton.me", "protonmail.com", "pm.me",
    "icloud.com", "me.com", "mac.com",
    "aol.com", "fastmail.com", "tutanota.com", "zoho.com",
    "yandex.com", "yandex.ru", "mail.ru",
    "qq.com", "163.com", "126.com", "sina.com",
    "gmx.com", "gmx.de", "web.de", "t-online.de",
}


def _domain_from_query(q: Query) -> str | None:
    if q.email and "@" in q.email:
        d = q.email.rsplit("@", 1)[-1].lower()
        if d and d not in FREE_EMAIL_DOMAINS:
            return d
    return None


class RdapDomainModule(BaseModule):
    name = "rdap_domain"
    category = "social"
    expansions = ("targeted",)

    def applicable(self, q: Query) -> bool:
        return _domain_from_query(q) is not None

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:  # noqa: PLR0912
        domain = _domain_from_query(q)
        if not domain:
            return
        try:
            r = await http.get(f"https://rdap.org/domain/{domain}", check_robots=False)
        except Exception:
            return
        if r.status_code != 200:
            return
        try:
            data = r.json()
        except Exception:
            return

        emails: set[str] = set()
        names: set[str] = set()
        phones: set[str] = set()
        for ent in data.get("entities", []) or []:
            vcard = ent.get("vcardArray") or []
            if not (isinstance(vcard, list) and len(vcard) >= 2):
                continue
            for prop in vcard[1]:
                if not isinstance(prop, list) or len(prop) < 4:
                    continue
                key = prop[0]
                val = prop[3]
                if key == "fn" and isinstance(val, str) and val.strip():
                    names.add(val.strip())
                elif key == "email" and isinstance(val, str):
                    emails.add(val.strip().lower())
                elif key == "tel" and isinstance(val, str):
                    phones.add(val.strip())

        if not (emails or names or phones):
            return

        yield Finding(
            module=self.name,
            category="social",
            type="profile",
            title=f"RDAP record for {domain}",
            source_url=f"https://rdap.org/domain/{domain}",
            data={
                "domain": domain,
                "names": sorted(names),
                "emails": sorted(emails),
                "phones": sorted(phones),
            },
            signals={
                "email": sorted(emails),
            } if emails else {},
            confidence=0.6,
        )
        for em in emails - {(q.email or "").lower()}:
            yield Finding(
                module=self.name,
                category="social",
                type="contact",
                title=f"Email (from {domain} RDAP): {em}",
                source_url=f"https://rdap.org/domain/{domain}",
                data={"kind": "email", "value": em, "source": f"{domain} RDAP"},
                signals={"email": [em]},
                confidence=0.55,
            )
        for ph in phones:
            yield Finding(
                module=self.name,
                category="social",
                type="contact",
                title=f"Phone (from {domain} RDAP): {ph}",
                source_url=f"https://rdap.org/domain/{domain}",
                data={"kind": "phone", "value": ph, "source": f"{domain} RDAP"},
                signals={},
                confidence=0.5,
            )
