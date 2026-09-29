from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser


@dataclass(slots=True)
class ValidatedSite:
    url: str
    final_url: str
    title: str
    description: str
    status: int
    body_text: str


def is_blocked(url: str, blocklist: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(token.lower().strip() in host for token in blocklist if token.strip())


async def validate_url(url: str, timeout: float = 12, proxy: str | None = None) -> ValidatedSite | None:
    if not url.startswith(("http://", "https://")):
        return None
    headers = {"User-Agent": "SiteFinder/1.0 (+local research tool)"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, proxy=proxy, headers=headers) as client:
            response = await client.get(url)
        content_type = response.headers.get("content-type", "").lower()
        if response.status_code != 200 or (content_type and "text/html" not in content_type):
            return None
        parser = HTMLParser(response.text)
        body = parser.body.text(separator=" ", strip=True) if parser.body else ""
        if len(body) < 80:
            return None
        title = parser.css_first("title")
        description = parser.css_first('meta[name="description"]')
        return ValidatedSite(url=url, final_url=str(response.url), title=title.text(strip=True) if title else "",
                             description=description.attributes.get("content", "") if description else "",
                             status=response.status_code, body_text=body[:100_000])
    except (httpx.HTTPError, ValueError):
        return None

