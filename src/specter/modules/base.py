from __future__ import annotations

from collections.abc import AsyncIterator

from ..config import Config
from ..http import HttpClient
from ..schema import Category, Finding, Query


class BaseModule:
    name: str = ""
    category: Category = "search"
    requires_key: bool = False
    expansions: tuple[str, ...] = ()  # one or more expansion ids from context.py

    def applicable(self, q: Query) -> bool:
        """Whether the query has enough info for this module to do useful work."""
        return True

    def skip_reason(self, cfg: Config) -> str | None:
        """Return a string if the module can't run, e.g. missing key."""
        return None

    async def run(self, q: Query, http: HttpClient) -> AsyncIterator[Finding]:
        if False:
            yield  # type: ignore[unreachable]
        return
