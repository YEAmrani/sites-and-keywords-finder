from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    engines: list[str] = field(default_factory=list)


class SearchProvider:
    name = "provider"

    async def search(self, query: str, page: int = 1, proxy: str | None = None) -> list[SearchHit]:
        raise NotImplementedError

