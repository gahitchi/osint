"""Pure-function PII extractors. No network I/O.

Used by the pivot crawler to pull contact details out of profile JSON and HTML
pages. Patterns are conservative: they aim to minimize false positives over
maximizing recall.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import phonenumbers
from selectolax.parser import HTMLParser

# ---------- Emails ----------

# RFC-leaning, but conservative to dodge common false positives.
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b"
)

# foo (at) bar (dot) com  /  foo [at] bar [dot] com  /  foo at bar dot com
_OBFUSC_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*[\(\[\{]?\s*(?:at|@|AT)\s*[\)\]\}]?\s*"
    r"([A-Za-z0-9.\-]+?)\s*[\(\[\{]?\s*(?:dot|\.|DOT)\s*[\)\]\}]?\s*"
    r"([A-Za-z]{2,24})\b",
    re.IGNORECASE,
)

# Email-shaped strings inside image filenames etc. that we want to discard.
_BAD_EMAIL_TLDS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "pdf", "zip"}
# Suppress GitHub no-reply addresses — they identify nobody.
_NOREPLY_RE = re.compile(r"^[^@]+@(users\.noreply\.github\.com|noreply\.|no-reply\.)", re.I)


def find_emails(text: str) -> list[str]:
    if not text:
        return []
    out: set[str] = set()
    for m in _EMAIL_RE.findall(text):
        out.add(m.lower())
    for m in _OBFUSC_RE.finditer(text):
        candidate = f"{m.group(1)}@{m.group(2)}.{m.group(3)}".lower()
        # Re-validate via main regex
        if _EMAIL_RE.fullmatch(candidate):
            out.add(candidate)
    return sorted(
        e for e in out
        if e.split(".")[-1].lower() not in _BAD_EMAIL_TLDS
        and not _NOREPLY_RE.match(e)
    )


# ---------- Phones ----------

def find_phones(text: str, default_region: str = "US") -> list[str]:
    """Return E.164-formatted phone numbers detected in `text`.

    Uses phonenumbers' PhoneNumberMatcher which is much stricter than naive
    regex — won't match "1234567890" as a phone unless it's clearly formatted
    as one.
    """
    if not text:
        return []
    out: set[str] = set()
    try:
        for m in phonenumbers.PhoneNumberMatcher(text, default_region):
            if phonenumbers.is_valid_number(m.number):
                out.add(
                    phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.E164)
                )
    except Exception:
        pass
    return sorted(out)


# ---------- URLs ----------

_BAD_HOSTS = {
    "schema.org", "www.w3.org", "fonts.googleapis.com", "fonts.gstatic.com",
    "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "use.fontawesome.com",
    "ajax.googleapis.com", "googletagmanager.com", "google-analytics.com",
}


def extract_outbound_links(html: str, base_url: str | None = None) -> list[str]:
    """Return absolute http(s) URLs from `<a href>` tags, minus CDN/analytics."""
    if not html:
        return []
    tree = HTMLParser(html)
    out: list[str] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href")
        if not href:
            continue
        if base_url:
            href = urljoin(base_url, href)
        u = urlparse(href)
        if u.scheme not in ("http", "https"):
            continue
        host = (u.hostname or "").lower()
        if not host or host in _BAD_HOSTS:
            continue
        clean = f"{u.scheme}://{u.netloc}{u.path}"
        if clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


# ---------- HTML → text ----------

def html_to_text(html: str) -> str:
    """Strip scripts/styles and return the visible text."""
    if not html:
        return ""
    tree = HTMLParser(html)
    for sel in ("script", "style", "noscript", "template"):
        for node in tree.css(sel):
            node.decompose()
    body = tree.body or tree.root
    return (body.text(separator=" ") if body else "").strip()
