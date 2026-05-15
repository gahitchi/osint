from __future__ import annotations

import asyncio
import time
from collections import defaultdict


class HostRateLimiter:
    """Per-host token bucket. Capacity = 1; refill = `rps` tokens/sec."""

    def __init__(self, rps: float) -> None:
        self.rps = max(rps, 0.1)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._next_ok: dict[str, float] = {}

    async def acquire(self, host: str) -> None:
        async with self._locks[host]:
            now = time.monotonic()
            next_ok = self._next_ok.get(host, 0.0)
            wait = next_ok - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_ok[host] = now + (1.0 / self.rps)
