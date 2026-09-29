from __future__ import annotations

import asyncio

from .base import SearchHit, SearchProvider


class DuckDuckGoProvider(SearchProvider):
    name = "DuckDuckGo"

    def __init__(self, timeout: float = 12):
        self.timeout = timeout

    async def search(self, query: str, page: int = 1, proxy: str | None = None) -> list[SearchHit]:
        def run():
            from ddgs import DDGS
            kwargs = {"proxy": proxy} if proxy else {}
            with DDGS(timeout=self.timeout, **kwargs) as client:
                return list(client.text(query, safesearch="moderate", max_results=10, page=page))

        rows = await asyncio.to_thread(run)
        return [SearchHit(url=row.get("href", ""), title=row.get("title", ""), snippet=row.get("body", ""), engines=[self.name])
                for row in rows if row.get("href")]
