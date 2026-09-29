from __future__ import annotations

import re
import asyncio
from dataclasses import dataclass
from itertools import cycle
from urllib.parse import quote, urlparse

import httpx


HOST_RE = re.compile(r"^(?P<host>[a-zA-Z0-9.-]+)(?::(?P<port>\d{2,5}))$")
SCHEME_RE = re.compile(r"^(?P<scheme>https?|socks4|socks5)://(?P<body>.+)$", re.I)


@dataclass(slots=True)
class ParsedProxy:
    url: str
    source: str


def _clean(line: str) -> str:
    line = line.strip()
    line = line.split("#", 1)[0].strip()
    line = re.sub(r"[\s,;]+$", "", line)
    return line


def parse_proxy(line: str, default_scheme: str = "http") -> ParsedProxy | None:
    original = line
    line = _clean(line)
    if not line:
        return None
    scheme = default_scheme.lower()
    scheme_match = SCHEME_RE.match(line)
    if scheme_match:
        scheme, line = scheme_match.group("scheme").lower(), scheme_match.group("body")

    # Normalize the two @-separated forms first.
    if "@" in line:
        left, right = line.rsplit("@", 1)
        left_host = HOST_RE.match(left)
        right_host = HOST_RE.match(right)
        if left_host and not right_host:
            hostpart, auth = left, right
        elif right_host:
            hostpart, auth = right, left
        else:
            return None
        if ":" not in auth:
            return None
        user, password = auth.split(":", 1)
        if not user or not password:
            return None
        match = HOST_RE.match(hostpart)
        if not match:
            return None
        return ParsedProxy(
            f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{match.group('host')}:{match.group('port')}",
            original,
        )

    parts = line.split(":")
    if len(parts) == 2:
        match = HOST_RE.match(line)
        if not match:
            return None
        return ParsedProxy(f"{scheme}://{line}", original)
    if len(parts) == 4:
        # Accept both ip:port:user:pass and user:pass:ip:port by locating the host/port pair.
        candidates = [(parts[0], parts[1], parts[2], parts[3]), (parts[2], parts[3], parts[0], parts[1])]
        for host, port, user, password in candidates:
            if HOST_RE.match(f"{host}:{port}") and user and password:
                return ParsedProxy(
                    f"{scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}",
                    original,
                )
    return None


def parse_proxy_list(text: str, default_scheme: str = "http") -> tuple[list[ParsedProxy], int]:
    parsed: list[ParsedProxy] = []
    skipped = 0
    for line in text.splitlines():
        item = parse_proxy(line, default_scheme)
        if item:
            parsed.append(item)
        elif _clean(line):
            skipped += 1
    return parsed, skipped


class ProxyManager:
    def __init__(self, text: str = "", enabled: bool = False):
        self.proxies, self.skipped = parse_proxy_list(text)
        self.enabled = enabled and bool(self.proxies)
        self.failure_limit = min(len(self.proxies), 20)
        self._cycle = cycle(self.proxies) if self.proxies else None
        self.benched: set[str] = set()
        self.country_cache: dict[str, str] = {}
        self.country_tasks: dict[str, asyncio.Task] = {}

    def next(self) -> str | None:
        if not self.enabled or not self._cycle:
            return None
        for _ in range(len(self.proxies)):
            proxy = next(self._cycle)
            if proxy.url not in self.benched:
                return proxy.url
        return None

    def bench(self, proxy: str | None):
        if proxy:
            self.benched.add(proxy)
            if len(self.benched) >= self.failure_limit:
                self.enabled = False

    def details(self, proxy: str | None) -> dict[str, str | None]:
        if not proxy:
            return {"url": None, "host": "Direct connection", "country": "Direct"}
        parsed = urlparse(proxy)
        host = parsed.hostname or "Unknown proxy"
        return {"url": proxy, "host": host, "country": self.country_cache.get(host, "Unknown")}

    async def resolve_country(self, proxy: str | None) -> str:
        if not proxy:
            return "Direct"
        host = urlparse(proxy).hostname or ""
        if not host:
            return "Unknown"
        if host in self.country_cache:
            return self.country_cache[host]
        existing = self.country_tasks.get(host)
        if existing:
            return await existing

        async def lookup():
            country = "Unknown"
            try:
                async with httpx.AsyncClient(timeout=2.5, trust_env=False) as client:
                    response = await client.get(f"https://ipapi.co/{host}/json/")
                payload = response.json()
                country = payload.get("country_name") or payload.get("country_code") or "Unknown"
            except (httpx.HTTPError, ValueError, TypeError):
                pass
            self.country_cache[host] = country
            return country

        task = asyncio.create_task(lookup())
        self.country_tasks[host] = task
        try:
            return await task
        finally:
            self.country_tasks.pop(host, None)
