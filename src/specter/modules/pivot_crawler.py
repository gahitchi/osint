"""Pivot crawler: given a verified (platform, username) pair, fetch that one
profile via its free public API, extract PII, then crawl 1 hop of the
user-published outbound links for more contact details.

Designed to be the *targeted* alternative to broad username fan-out (Sherlock),
which spawns false positives on common usernames.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from urllib.parse import urlparse

from ..config import Config
from ..extract import extract_outbound_links, find_emails, find_phones, html_to_text
from ..http import HttpClient
from ..schema import SUPPORTED_PLATFORMS, Finding, Query
from .base import BaseModule

MAX_HOPS = 5  # outbound URLs followed per profile


class PivotCrawlerModule(BaseModule):
    name = "pivot_crawler"
    category = "social"
    expansions = ("targeted",)

    def applicable(self, q: Query) -> bool:
        return bool(
            q.username
            and q.source_platform
            and q.source_platform in SUPPORTED_PLATFORMS
        )

    def skip_reason(self, cfg: Config) -> str | None:
        return None

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        platform = q.source_platform
        username = q.username
        fetcher = _FETCHERS.get(platform)
        if not fetcher:
            return
        profile = await fetcher(username, http)
        if not profile:
            return

        # 1) The verified profile itself.
        sig = {"username": [username]}
        if profile.get("email"):
            sig["email"] = [profile["email"]]
        yield Finding(
            module=self.name,
            category="social",
            type="profile",
            title=f"{platform}: {profile.get('display_name') or username}",
            source_url=profile.get("profile_url") or f"https://{platform}/{username}",
            data={
                "platform": platform,
                "username": username,
                "name": profile.get("name"),
                "bio": profile.get("bio"),
                "location": profile.get("location"),
                "company": profile.get("company"),
                "verified": True,
            },
            signals=sig,
            confidence=0.95,
        )

        # 2) Contact details extracted from the profile payload.
        for email in profile.get("emails", []):
            yield Finding(
                module=self.name,
                category="social",
                type="contact",
                title=f"Email (from {platform} profile): {email}",
                source_url=profile.get("profile_url") or f"https://{platform}/{username}",
                data={"platform": platform, "kind": "email", "value": email,
                      "source": f"{platform} profile"},
                signals={"email": [email], "username": [username]},
                confidence=0.9,
            )
        for phone in profile.get("phones", []):
            yield Finding(
                module=self.name,
                category="social",
                type="contact",
                title=f"Phone (from {platform} profile): {phone}",
                source_url=profile.get("profile_url") or f"https://{platform}/{username}",
                data={"platform": platform, "kind": "phone", "value": phone,
                      "source": f"{platform} profile"},
                signals={"username": [username]},
                confidence=0.85,
            )

        # 3) Follow user-published outbound links (1 hop).
        links = profile.get("links") or []
        for url in links[:MAX_HOPS]:
            async for f in _crawl_link(url, platform, username, http):
                yield f


# ===========================================================================
# Per-platform fetchers. Each returns a dict with these optional keys:
#   profile_url, display_name, name, bio, location, company,
#   email, emails, phones, links
# ===========================================================================

async def _safe_json(http: HttpClient, url: str, **kw):
    try:
        r = await http.get(url, **kw)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _scan_text_for_pii(*chunks: str | None) -> tuple[list[str], list[str], list[str]]:
    """Pull emails, phones, urls out of a set of text chunks."""
    text = " ".join(c for c in chunks if c)
    emails = find_emails(text)
    phones = find_phones(text)
    # Bare URL detection: anything that looks like one in the text.
    urls = [u.rstrip(".,);:!?") for u in re.findall(r"https?://[^\s\"'<>)]+", text)]
    return emails, phones, urls


async def _fetch_github(username: str, http: HttpClient) -> dict | None:
    user = await _safe_json(
        http, f"https://api.github.com/users/{username}",
        headers={"Accept": "application/vnd.github+json"},
    )
    if not user:
        return None
    bio = user.get("bio") or ""
    emails, phones, urls = _scan_text_for_pii(bio, user.get("email"))
    if user.get("email"):
        emails = sorted(set(emails) | {user["email"].lower()})
    links = list(urls)
    for k in ("blog", "html_url"):
        v = user.get(k)
        if v and v.startswith("http"):
            links.append(v)
    if user.get("twitter_username"):
        links.append(f"https://twitter.com/{user['twitter_username']}")

    # Mine commit author emails from recent public events.
    commit_emails: set[str] = set()
    events = await _safe_json(
        http, f"https://api.github.com/users/{username}/events/public",
        params={"per_page": "100"},
        headers={"Accept": "application/vnd.github+json"},
    )
    if isinstance(events, list):
        for ev in events:
            for c in (ev.get("payload") or {}).get("commits", []) or []:
                e = (c.get("author") or {}).get("email")
                if e:
                    commit_emails.add(e.lower())
    if commit_emails:
        emails = sorted(set(emails) | set(find_emails(" ".join(commit_emails))))

    return {
        "profile_url": user.get("html_url"),
        "display_name": user.get("name") or username,
        "name": user.get("name"),
        "bio": bio,
        "location": user.get("location"),
        "company": user.get("company"),
        "email": user.get("email"),
        "emails": emails,
        "phones": phones,
        "links": _dedupe(links),
    }


async def _fetch_gitlab(username: str, http: HttpClient) -> dict | None:
    arr = await _safe_json(http, f"https://gitlab.com/api/v4/users?username={username}")
    if not isinstance(arr, list) or not arr:
        return None
    user = arr[0]
    bio = user.get("bio") or ""
    emails, phones, urls = _scan_text_for_pii(bio, user.get("public_email"))
    if user.get("public_email"):
        emails = sorted(set(emails) | {user["public_email"].lower()})
    links = list(urls)
    if user.get("website_url"):
        links.append(user["website_url"])
    return {
        "profile_url": user.get("web_url"),
        "display_name": user.get("name") or username,
        "name": user.get("name"),
        "bio": bio,
        "location": user.get("location"),
        "company": user.get("organization"),
        "email": user.get("public_email"),
        "emails": emails,
        "phones": phones,
        "links": _dedupe(links),
    }


async def _fetch_reddit(username: str, http: HttpClient) -> dict | None:
    data = await _safe_json(http, f"https://www.reddit.com/user/{username}/about.json")
    if not data or not isinstance(data, dict):
        return None
    inner = (data.get("data") or {})
    desc = ((inner.get("subreddit") or {}).get("public_description") or "")
    emails, phones, urls = _scan_text_for_pii(desc)
    return {
        "profile_url": f"https://www.reddit.com/user/{username}",
        "display_name": inner.get("name") or username,
        "name": None,
        "bio": desc,
        "location": None,
        "company": None,
        "email": None,
        "emails": emails,
        "phones": phones,
        "links": _dedupe(urls),
    }


async def _fetch_hackernews(username: str, http: HttpClient) -> dict | None:
    data = await _safe_json(
        http, f"https://hacker-news.firebaseio.com/v0/user/{username}.json"
    )
    if not data or not isinstance(data, dict):
        return None
    about_html = data.get("about") or ""
    about_text = html_to_text(f"<html><body>{about_html}</body></html>")
    emails, phones, _ = _scan_text_for_pii(about_text)
    links = extract_outbound_links(f"<html><body>{about_html}</body></html>")
    return {
        "profile_url": f"https://news.ycombinator.com/user?id={username}",
        "display_name": username,
        "name": None,
        "bio": about_text,
        "location": None,
        "company": None,
        "email": None,
        "emails": emails,
        "phones": phones,
        "links": _dedupe(links),
    }


async def _fetch_mastodon(username: str, http: HttpClient) -> dict | None:
    # Assume mastodon.social unless username includes a host (user@server.tld).
    host = "mastodon.social"
    acct = username
    if "@" in username:
        acct, host = username.split("@", 1)
    data = await _safe_json(
        http, f"https://{host}/api/v1/accounts/lookup",
        params={"acct": acct},
    )
    if not data:
        return None
    note_text = html_to_text(f"<html><body>{data.get('note','')}</body></html>")
    fields = data.get("fields") or []
    field_text = " ".join((f.get("value") or "") for f in fields)
    emails, phones, _ = _scan_text_for_pii(note_text, field_text)
    links = extract_outbound_links(f"<html><body>{field_text} {data.get('note','')}</body></html>")
    return {
        "profile_url": data.get("url"),
        "display_name": data.get("display_name") or acct,
        "name": data.get("display_name"),
        "bio": note_text,
        "location": None,
        "company": None,
        "email": None,
        "emails": emails,
        "phones": phones,
        "links": _dedupe(links),
    }


async def _fetch_dev(username: str, http: HttpClient) -> dict | None:
    user = await _safe_json(http, f"https://dev.to/api/users/by_username?url={username}")
    if not user or not isinstance(user, dict):
        return None
    summary = user.get("summary") or ""
    emails, phones, _ = _scan_text_for_pii(summary, user.get("email"))
    links = []
    for k in ("website_url", "github_username", "twitter_username"):
        v = user.get(k)
        if not v:
            continue
        if k == "github_username":
            links.append(f"https://github.com/{v}")
        elif k == "twitter_username":
            links.append(f"https://twitter.com/{v}")
        elif v.startswith("http"):
            links.append(v)
    return {
        "profile_url": f"https://dev.to/{username}",
        "display_name": user.get("name") or username,
        "name": user.get("name"),
        "bio": summary,
        "location": user.get("location"),
        "company": None,
        "email": user.get("email"),
        "emails": emails,
        "phones": phones,
        "links": _dedupe(links),
    }


async def _fetch_keybase(username: str, http: HttpClient) -> dict | None:
    data = await _safe_json(
        http,
        f"https://keybase.io/_/api/1.0/user/lookup.json?usernames={username}",
    )
    if not data or data.get("status", {}).get("code") != 0:
        return None
    them = (data.get("them") or [None])[0]
    if not them:
        return None
    basics = them.get("basics") or {}
    profile = them.get("profile") or {}
    bio = profile.get("bio") or ""
    emails, phones, _ = _scan_text_for_pii(bio)
    # Keybase explicit proofs = cryptographically-verified links.
    links = []
    for p in (them.get("proofs_summary") or {}).get("all", []):
        url = p.get("service_url") or p.get("proof_url")
        if url:
            links.append(url)
    return {
        "profile_url": f"https://keybase.io/{username}",
        "display_name": profile.get("full_name") or basics.get("username") or username,
        "name": profile.get("full_name"),
        "bio": bio,
        "location": profile.get("location"),
        "company": None,
        "email": None,
        "emails": emails,
        "phones": phones,
        "links": _dedupe(links),
    }


async def _fetch_lichess(username: str, http: HttpClient) -> dict | None:
    user = await _safe_json(http, f"https://lichess.org/api/user/{username}")
    if not user:
        return None
    profile = user.get("profile") or {}
    bio = profile.get("bio") or ""
    emails, phones, _ = _scan_text_for_pii(bio)
    links_text = profile.get("links") or ""
    links = [u.rstrip(".,);:") for u in re.findall(r"https?://\S+", links_text)]
    return {
        "profile_url": f"https://lichess.org/@/{username}",
        "display_name": user.get("username") or username,
        "name": (profile.get("firstName", "") + " " + profile.get("lastName", "")).strip() or None,
        "bio": bio,
        "location": profile.get("location") or profile.get("country"),
        "company": None,
        "email": None,
        "emails": emails,
        "phones": phones,
        "links": _dedupe(links),
    }


async def _fetch_orcid(username: str, http: HttpClient) -> dict | None:
    # Username should be an ORCID iD like 0000-0001-9220-2154
    if not all(c.isdigit() or c in "-X" for c in username):
        return None
    person = await _safe_json(
        http,
        f"https://pub.orcid.org/v3.0/{username}/person",
        headers={"Accept": "application/json"},
    )
    if not person:
        return None
    name_node = person.get("name") or {}
    given = (name_node.get("given-names") or {}).get("value", "")
    family = (name_node.get("family-name") or {}).get("value", "")
    full = f"{given} {family}".strip() or username
    bio = (person.get("biography") or {}).get("content") or ""
    emails_block = (person.get("emails") or {}).get("email") or []
    declared_emails = [e.get("email") for e in emails_block if e.get("email")]
    addrs = (person.get("addresses") or {}).get("address") or []
    country = next((a.get("country", {}).get("value") for a in addrs), None)
    extra_emails, phones, _ = _scan_text_for_pii(bio)
    return {
        "profile_url": f"https://orcid.org/{username}",
        "display_name": full,
        "name": full,
        "bio": bio,
        "location": country,
        "company": None,
        "email": declared_emails[0] if declared_emails else None,
        "emails": sorted({*declared_emails, *extra_emails}),
        "phones": phones,
        "links": [],
    }


# ---- platforms scraped from public HTML (OGP / embedded JSON) ----

async def _safe_html(http: HttpClient, url: str, **kw):
    try:
        r = await http.get(url, **kw)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    if "text/html" not in r.headers.get("content-type", ""):
        return None
    return r.text[:300_000]


def _ogp(html: str) -> dict[str, str]:
    """Extract OpenGraph meta tags. Cheap regex; selectolax is overkill here."""
    out: dict[str, str] = {}
    for m in re.finditer(
        r'<meta\s+property=["\']og:([a-z_]+)["\']\s+content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    ):
        out.setdefault(m.group(1).lower(), m.group(2))
    return out


async def _fetch_telegram(username: str, http: HttpClient) -> dict | None:
    """Telegram public preview at t.me/{username}. Works for users, channels,
    and bots that have set a public username. Returns minimal info; the page
    contains OGP title + description + image."""
    html = await _safe_html(http, f"https://t.me/{username}", check_robots=False)
    if not html:
        return None
    # Negative signal: tg-me page for unknown handle shows a generic CTA.
    if "If you have Telegram, you can contact" in html and "@" + username.lower() in html.lower():
        # Channel/user exists but no public preview content. Still a valid finding.
        pass
    ogp = _ogp(html)
    title = ogp.get("title", "")
    desc = ogp.get("description", "")
    if not title and not desc:
        return None
    emails, phones, _ = _scan_text_for_pii(desc)
    return {
        "profile_url": f"https://t.me/{username}",
        "display_name": title or username,
        "name": None,
        "bio": desc,
        "location": None,
        "company": None,
        "email": None,
        "emails": emails,
        "phones": phones,
        "links": [],
    }


async def _fetch_tiktok(username: str, http: HttpClient) -> dict | None:
    """TikTok public profile. TikTok actively challenges scrapers, so this is
    best-effort: we just lift OGP if the page renders. If TikTok serves us a
    challenge page, we'll return None and move on."""
    handle = username.lstrip("@")
    html = await _safe_html(
        http, f"https://www.tiktok.com/@{handle}", check_robots=False
    )
    if not html:
        return None
    ogp = _ogp(html)
    title = ogp.get("title", "")
    desc = ogp.get("description", "")
    if not title:
        return None
    emails, phones, _ = _scan_text_for_pii(desc)
    return {
        "profile_url": f"https://www.tiktok.com/@{handle}",
        "display_name": title.split("|")[0].strip() or handle,
        "name": None,
        "bio": desc,
        "location": None,
        "company": None,
        "email": None,
        "emails": emails,
        "phones": phones,
        "links": [],
    }


async def _fetch_youtube(username: str, http: HttpClient) -> dict | None:
    """YouTube public channel via @handle (preferred) or legacy /user/{name}."""
    handle = username.lstrip("@")
    html = await _safe_html(
        http, f"https://www.youtube.com/@{handle}", check_robots=False
    )
    if not html:
        # Fallback: legacy user URL
        html = await _safe_html(
            http, f"https://www.youtube.com/user/{handle}", check_robots=False
        )
        if not html:
            return None
    ogp = _ogp(html)
    title = ogp.get("title", "")
    desc = ogp.get("description", "")
    if not title:
        return None
    emails, phones, urls = _scan_text_for_pii(desc)
    return {
        "profile_url": f"https://www.youtube.com/@{handle}",
        "display_name": title,
        "name": None,
        "bio": desc,
        "location": None,
        "company": None,
        "email": None,
        "emails": emails,
        "phones": phones,
        "links": _dedupe(urls),
    }


_FETCHERS = {
    "github": _fetch_github,
    "gitlab": _fetch_gitlab,
    "reddit": _fetch_reddit,
    "hackernews": _fetch_hackernews,
    "mastodon": _fetch_mastodon,
    "dev": _fetch_dev,
    "keybase": _fetch_keybase,
    "lichess": _fetch_lichess,
    "orcid": _fetch_orcid,
    "telegram": _fetch_telegram,
    "tiktok": _fetch_tiktok,
    "youtube": _fetch_youtube,
}


# ===========================================================================
# 1-hop link follower
# ===========================================================================

async def _crawl_link(
    url: str, platform: str, username: str, http: HttpClient
) -> AsyncIterator[Finding]:
    """Fetch a user-listed outbound URL, extract emails/phones, emit Findings."""
    try:
        r = await http.get(url)
    except Exception:
        return
    if r.status_code != 200:
        return
    ctype = r.headers.get("content-type", "")
    if "text/html" not in ctype and "text/plain" not in ctype:
        return
    html = r.text[:200_000]
    text = html_to_text(html)
    emails = find_emails(text)
    phones = find_phones(text)
    if not emails and not phones:
        return

    host = urlparse(url).hostname or url
    for email in emails:
        yield Finding(
            module="pivot_crawler",
            category="social",
            type="contact",
            title=f"Email (from {host}): {email}",
            source_url=url,
            data={"platform": platform, "kind": "email", "value": email,
                  "source": f"{host} (linked from {platform})"},
            signals={"email": [email], "username": [username]},
            confidence=0.75,
        )
    for phone in phones:
        yield Finding(
            module="pivot_crawler",
            category="social",
            type="contact",
            title=f"Phone (from {host}): {phone}",
            source_url=url,
            data={"platform": platform, "kind": "phone", "value": phone,
                  "source": f"{host} (linked from {platform})"},
            signals={"username": [username]},
            confidence=0.7,
        )

    # Allow yielding control between hops.
    await asyncio.sleep(0)


# ===========================================================================
# Helpers
# ===========================================================================

def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out
