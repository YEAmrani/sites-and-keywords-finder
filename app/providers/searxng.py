from __future__ import annotations

import httpx

from .base import SearchHit, SearchProvider


class SearXNGProvider(SearchProvider):
    name = "SearXNG"

    def __init__(self, base_url: str, timeout: float = 12, engine: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.engine = engine
        self.name = engine or "SearXNG"

    async def search(self, query: str, page: int = 1, proxy: str | None = None) -> list[SearchHit]:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, proxy=proxy) as client:
            params = {"q": query, "format": "json", "pageno": page}
            if self.engine:
                params["engines"] = self.engine.casefold()
            response = await client.get(f"{self.base_url}/search", params=params)
            response.raise_for_status()
            payload = response.json()
        return [SearchHit(url=row.get("url", ""), title=row.get("title", ""), snippet=row.get("content", ""),
                          engines=row.get("engines", []) or [self.name])
                for row in payload.get("results", []) if row.get("url")]
