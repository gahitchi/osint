"""Compliance-aware async HTTP client.

All outbound requests in this project go through `HttpClient.get`. It enforces:
- a single identifying User-Agent
- per-host robots.txt (cached)
- per-host token-bucket rate limiting
- refusal of redirects that land on a login page
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .config import Config
from .rate_limit import HostRateLimiter

log = logging.getLogger(__name__)

LOGIN_HINTS = ("/login", "/signin", "/sign-in", "accounts.google", "auth0", "oauth")


class RobotsDenied(Exception):
    pass


class LoginWallDetected(Exception):
    pass


class HttpClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client = httpx.AsyncClient(
            headers={"User-Agent": cfg.user_agent, "Accept": "*/*"},
            timeout=cfg.request_timeout,
            follow_redirects=True,
            http2=False,
        )
        self.limiter = HostRateLimiter(cfg.host_rps)
        self._robots: dict[str, RobotFileParser | None] = {}

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _robots_for(self, host: str, scheme: str) -> RobotFileParser | None:
        if host in self._robots:
            return self._robots[host]
        rp = RobotFileParser()
        url = f"{scheme}://{host}/robots.txt"
        try:
            r = await self.client.get(url, timeout=5.0)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            else:
                self._robots[host] = None
                return None
        except httpx.HTTPError:
            self._robots[host] = None
            return None
        self._robots[host] = rp
        return rp

    async def _check_robots(self, url: str) -> None:
        u = urlparse(url)
        if not u.hostname:
            return
        rp = await self._robots_for(u.hostname, u.scheme or "https")
        if rp is None:
            return
        if not rp.can_fetch(self.cfg.user_agent, url):
            raise RobotsDenied(url)

    async def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        check_robots: bool = True,
        allow_login_redirect: bool = False,
    ) -> httpx.Response:
        if check_robots:
            await self._check_robots(url)
        host = urlparse(url).hostname or ""
        await self.limiter.acquire(host)
        r = await self.client.get(url, params=params, headers=headers)
        final = str(r.url).lower()
        if not allow_login_redirect and any(h in final for h in LOGIN_HINTS):
            raise LoginWallDetected(final)
        return r

    async def head(self, url: str, *, check_robots: bool = True) -> httpx.Response:
        if check_robots:
            await self._check_robots(url)
        host = urlparse(url).hostname or ""
        await self.limiter.acquire(host)
        return await self.client.head(url)
