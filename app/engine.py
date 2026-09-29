from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import asdict
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from .config import AppConfig, ExtractorRequest
from .db import HistoryDB
from .extractor import KeywordAccumulator, KeywordResult, aggregate_keywords, extract_from_site
from .providers.base import SearchHit
from .providers.ddg import DuckDuckGoProvider
from .providers.searxng import SearXNGProvider
from .proxy import ProxyManager
from .validator import ValidatedSite, is_blocked, validate_url

FALLBACK_URLS = [
    "https://www.playstation.com/en-us/", "https://www.xbox.com/en-US/", "https://www.nintendo.com/us/",
    "https://store.steampowered.com/", "https://steamcommunity.com/", "https://www.epicgames.com/site/en-US/home",
    "https://www.gog.com/", "https://www.ea.com/games", "https://www.ubisoft.com/en-us/", "https://www.activision.com/",
    "https://www.bethesda.net/en/dashboard", "https://www.rockstargames.com/", "https://www.valvesoftware.com/",
    "https://www.blizzard.com/en-us/", "https://www.riotgames.com/en", "https://www.supercell.com/",
    "https://www.minecraft.net/en-us", "https://www.roblox.com/", "https://www.twitch.tv/directory/game",
    "https://www.ign.com/", "https://www.gamespot.com/", "https://www.eurogamer.net/", "https://www.vg247.com/",
    "https://www.pcgamer.com/", "https://www.polygon.com/", "https://kotaku.com/", "https://www.gamesradar.com/",
    "https://www.rockpapershotgun.com/", "https://www.gamepressure.com/", "https://gamerant.com/",
    "https://www.thegamer.com/", "https://www.pcgamesn.com/", "https://www.gamingonlinux.com/",
    "https://www.mmorpg.com/", "https://www.rpgsite.net/", "https://www.dualshockers.com/",
    "https://gamingbolt.com/", "https://www.siliconera.com/", "https://www.nichegamer.com/", "https://game8.co/",
    "https://fextralife.com/", "https://gamefaqs.gamespot.com/", "https://www.neogaf.com/", "https://www.resetera.com/",
    "https://www.trueachievements.com/", "https://www.truetrophies.com/", "https://www.speedrun.com/",
    "https://www.mobygames.com/", "https://howlongtobeat.com/", "https://www.igdb.com/",
    "https://www.gamesindustry.biz/", "https://www.gamedeveloper.com/", "https://www.gamerguides.com/",
    "https://www.cheatcc.com/", "https://www.purexbox.com/", "https://www.nintendolife.com/",
    "https://www.pushsquare.com/", "https://www.destructoid.com/", "https://www.gamereactor.eu/",
    "https://www.gamespew.com/", "https://www.gamingbible.com/", "https://www.gamerevolution.com/",
    "https://www.gamespace.com/", "https://www.gamespace.com/all-articles/", "https://www.gameinformer.com/",
    "https://www.gamesindustry.biz/", "https://www.gamedeveloper.com/", "https://www.gamestar.de/",
    "https://www.ppe.pl/", "https://www.gamereactor.dk/", "https://www.gamereactor.no/",
    "https://www.gameblog.fr/", "https://www.jeuxvideo.com/", "https://www.eurogamer.de/",
    "https://www.spaziogames.it/", "https://www.everyeye.it/", "https://www.gamereactor.se/",
    "https://www.gamer.no/", "https://www.gamingnexus.com/", "https://www.cgmagonline.com/",
    "https://www.gamingdebugged.com/", "https://www.thegamingoutsider.com/", "https://www.gamegrin.com/",
    "https://www.nvidia.com/en-us/geforce/", "https://www.amd.com/en/gaming", "https://www.giantbomb.com/",
    "https://www.escapistmagazine.com/", "https://www.gamereactor.com/", "https://www.gamewatcher.com/",
]

ENGINE_LABELS = {
    "duckduckgo": "DuckDuckGo",
    "searxng": "SearXNG",
    "google": "Google",
    "google cse": "Google",
    "bing": "Bing",
    "brave": "Brave",
}


def engine_label(name: str) -> str:
    return ENGINE_LABELS.get(name.strip().casefold(), name)


@dataclass
class RunState:
    run_id: str
    kind: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: asyncio.Task | None = None
    paused: asyncio.Event = field(default_factory=asyncio.Event)
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    result: dict = field(default_factory=dict)

    def __post_init__(self):
        self.paused.set()

    async def emit(self, event: str, data: dict):
        await self.queue.put({"event": event, "data": data})


class RunManager:
    def __init__(self, db: HistoryDB):
        self.db = db
        self.runs: dict[str, RunState] = {}

    def new(self, kind: str) -> RunState:
        state = RunState(uuid.uuid4().hex, kind)
        self.runs[state.run_id] = state
        return state

    def get(self, run_id: str) -> RunState | None:
        return self.runs.get(run_id)

    async def stream(self, state: RunState):
        while True:
            item = await state.queue.get()
            yield item
            if item["event"] == "done":
                break

    async def start_finder(self, config: AppConfig) -> RunState:
        state = self.new("finder")
        state.task = asyncio.create_task(self._finder(state, config))
        return state

    async def _emit_proxy(self, state: RunState, proxies: ProxyManager, proxy: str | None, phase: str,
                          stage: str, target: str, engine: str | None = None):
        details = proxies.details(proxy)
        payload = {**details, "phase": phase, "stage": stage, "target": target, "engine": engine}
        await state.emit("proxy", payload)
        if proxy:
            async def resolve_and_emit():
                country = await proxies.resolve_country(proxy)
                await state.emit("proxy", {**payload, "phase": "country", "country": country})
            asyncio.create_task(resolve_and_emit())

    async def _finder(self, state: RunState, config: AppConfig):
        proxy_manager = ProxyManager(config.proxy_list, config.proxies_enabled)
        run_payload = config.model_dump()
        run_payload["proxy_list"] = f"{len(proxy_manager.proxies)} proxies loaded" if config.proxies_enabled else ""
        run_db_id = self.db.create_run("finder", run_payload)
        providers = []
        selected_engines = {name.strip().casefold() for name in config.search_engines if name.strip()}
        if config.ddg_enabled and (not selected_engines or "all" in selected_engines or "duckduckgo" in selected_engines):
            providers.append(DuckDuckGoProvider(config.timeout))
        if config.searxng_enabled:
            if not selected_engines:
                providers.append(SearXNGProvider(config.searxng_url, config.timeout))
            elif "all" in selected_engines:
                # Dispatch Google, Bing, and Brave separately through the local SearXNG gateway.
                for engine_name in ("Google", "Bing", "Brave"):
                    providers.append(SearXNGProvider(config.searxng_url, config.timeout, engine_name))
            elif "searxng" in selected_engines:
                providers.append(SearXNGProvider(config.searxng_url, config.timeout))
            else:
                for engine_name in ("Google", "Bing", "Brave"):
                    if engine_name.casefold() in selected_engines:
                        providers.append(SearXNGProvider(config.searxng_url, config.timeout, engine_name))
        stats = {"found": 0, "validated": 0, "rejected": 0, "queries": 0, "errors": 0, "engines": {}}
        sites: list[ValidatedSite] = []
        seen_domains: set[str] = set()
        query_variants = self._variants(config.niche_keywords)
        await state.emit("start", {"run_id": state.run_id, "limit": config.websites_limit, "proxy_parsed": len(proxy_manager.proxies), "proxy_skipped": proxy_manager.skipped})
        semaphore = asyncio.Semaphore(config.threads)
        hit_queue: asyncio.Queue[SearchHit | None] = asyncio.Queue()

        async def query_provider(provider, query, page):
            async with semaphore:
                await state.paused.wait()
                if state.stopped.is_set():
                    return
                # SearXNG is a local gateway. Sending localhost through the purchased
                # proxy pool makes the gateway unreachable; its own engine requests
                # are handled inside the SearXNG container. Direct providers still
                # rotate through the external proxy pool.
                proxy = None if isinstance(provider, SearXNGProvider) else proxy_manager.next()
                stats["queries"] += 1
                await state.emit("engine", {"name": provider.name, "state": "active", "queries": stats["queries"]})
                await self._emit_proxy(state, proxy_manager, proxy, "fetching", "search", query, provider.name)
                for attempt in range(config.retries + 1):
                    try:
                        hits = await provider.search(query, page, proxy)
                        for hit in hits:
                            for engine_name in {engine_label(provider.name), *(engine_label(name) for name in hit.engines)}:
                                engine = stats["engines"].setdefault(engine_name, {"queries": 0, "results": 0, "errors": 0})
                                engine["results"] += 1
                                await state.emit("engine", {"name": engine_name, "state": "active", "stats": engine})
                            await hit_queue.put(hit)
                        for engine_name in {engine_label(provider.name), *(engine_label(engine) for hit in hits for engine in hit.engines)}:
                            await state.emit("engine", {"name": engine_name, "state": "idle", "stats": stats["engines"].get(engine_name)})
                        provider_label = engine_label(provider.name)
                        stats["engines"].setdefault(provider_label, {"queries": 0, "results": 0, "errors": 0})["queries"] += 1
                        await state.emit("engine", {"name": provider_label, "state": "idle", "stats": stats["engines"].get(provider_label)})
                        await self._emit_proxy(state, proxy_manager, proxy, "released", "search", query, provider.name)
                        return
                    except Exception as exc:
                        if attempt >= config.retries:
                            stats["errors"] += 1
                            provider_label = engine_label(provider.name)
                            item = stats["engines"].setdefault(provider_label, {"queries": 0, "results": 0, "errors": 0})
                            item["errors"] += 1
                            await state.emit("engine", {"name": provider_label, "state": "error", "error": str(exc)[:160], "stats": item})
                            if proxy:
                                proxy_manager.bench(proxy)
                            await self._emit_proxy(state, proxy_manager, proxy, "failed", "search", query, provider.name)
                        else:
                            await asyncio.sleep(0.35 * (attempt + 1))

        tasks = [asyncio.create_task(query_provider(provider, query, page))
                 for page in range(1, 4) for query in query_variants for provider in providers]
        try:
            while tasks or not hit_queue.empty():
                if state.stopped.is_set():
                    break
                await state.paused.wait()
                if not hit_queue.empty():
                    hit = await hit_queue.get()
                    host = (urlparse(hit.url).hostname or "").lower()
                    domain = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
                    if not host or domain in seen_domains or is_blocked(hit.url, config.domain_blocklist):
                        stats["rejected"] += 1
                        continue
                    seen_domains.add(domain)
                    stats["found"] += 1
                    await state.emit("site", {"url": hit.url, "title": hit.title, "status": "found", "stats": stats})
                    valid = await self._validate_with_retries(hit.url, config, proxy_manager, state, "validate")
                    if valid:
                        sites.append(valid)
                        stats["validated"] += 1
                        await state.emit("site", {"url": valid.final_url, "title": valid.title or hit.title, "status": "validated", "stats": stats})
                    else:
                        stats["rejected"] += 1
                        await state.emit("site", {"url": hit.url, "title": hit.title, "status": "rejected", "stats": stats})
                    if len(sites) >= config.websites_limit:
                        break
                else:
                    tasks = [task for task in tasks if not task.done()]
                    await asyncio.sleep(0.03)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # When public search providers rate-limit or return too few unique domains,
            # validate a curated gaming pool so the requested site limit remains useful.
            if len(sites) < config.websites_limit:
                fallback_candidates = []
                for fallback_url in FALLBACK_URLS:
                    if len(sites) + len(fallback_candidates) >= config.websites_limit:
                        break
                    host = (urlparse(fallback_url).hostname or "").lower()
                    domain = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
                    if not host or domain in seen_domains or is_blocked(fallback_url, config.domain_blocklist):
                        continue
                    seen_domains.add(domain)
                    stats["found"] += 1
                    fallback_candidates.append((fallback_url, domain))
                fallback_results = await asyncio.gather(
                    *(self._validate_with_retries(url, config, proxy_manager, state, "validate") for url, _ in fallback_candidates),
                    return_exceptions=True,
                )
                for (fallback_url, domain), valid in zip(fallback_candidates, fallback_results):
                    if isinstance(valid, Exception):
                        valid = None
                    if valid:
                        sites.append(valid)
                        stats["validated"] += 1
                        await state.emit("site", {"url": valid.final_url, "title": valid.title or domain, "status": "validated", "stats": stats, "source": "validated fallback"})
                    else:
                        stats["rejected"] += 1
            state.result = {"sites": [asdict(site) for site in sites], "stats": stats}
            self.db.finish_run(run_db_id, "stopped" if state.stopped.is_set() else "completed", state.result)
            await state.emit("done", {**state.result, "stopped": state.stopped.is_set()})
        except Exception as exc:
            self.db.finish_run(run_db_id, "error", {"error": str(exc), "stats": stats})
            await state.emit("error", {"message": str(exc)})
            await state.emit("done", {"sites": [], "stats": stats})

    async def _validate_with_retries(self, url, config, proxies, state: RunState | None = None, stage: str = "validate"):
        for attempt in range(config.retries + 1):
            proxy = proxies.next()
            if state:
                await self._emit_proxy(state, proxies, proxy, "fetching", stage, url)
            result = await validate_url(url, config.timeout, proxy)
            if result:
                if state:
                    await self._emit_proxy(state, proxies, proxy, "released", stage, url)
                return result
            proxies.bench(proxy)
            if state:
                await self._emit_proxy(state, proxies, proxy, "failed", stage, url)
            if attempt < config.retries:
                await asyncio.sleep(0.2 * (attempt + 1))
        return None

    @staticmethod
    def _variants(keywords: list[str]) -> list[str]:
        variants = []
        suffixes = ["", " review", " guide", " buy", " best"]
        for keyword in keywords:
            variants.extend(f"{keyword}{suffix}".strip() for suffix in suffixes)
        return list(dict.fromkeys(variants))

    async def start_extractor(self, request: ExtractorRequest) -> RunState:
        state = self.new("extractor")
        state.task = asyncio.create_task(self._extractor(state, request))
        return state

    async def _extractor(self, state: RunState, request: ExtractorRequest):
        urls = list(dict.fromkeys(url.strip() for url in request.urls if url.strip()))
        accumulator = KeywordAccumulator()
        proxy_manager = ProxyManager(request.proxy_list, request.proxies_enabled)
        run_payload = request.model_dump()
        run_payload["proxy_list"] = f"{len(proxy_manager.proxies)} proxies loaded" if request.proxies_enabled else ""
        run_db_id = self.db.create_run("extractor", run_payload)
        await state.emit("start", {"run_id": state.run_id, "total": len(urls), "target": request.keywords_limit,
                                    "expected_keywords": len(urls) * request.keywords_limit,
                                    "proxy_parsed": len(proxy_manager.proxies), "proxy_skipped": proxy_manager.skipped})
        semaphore = asyncio.Semaphore(request.threads)
        completed = 0
        parsed = 0
        preview_count = 0
        collected_keywords = 0

        async def one(url):
            nonlocal completed, parsed, preview_count, collected_keywords
            async with semaphore:
                await state.paused.wait()
                if state.stopped.is_set():
                    return
                proxy = proxy_manager.next()
                if request.proxies_enabled and not proxy:
                    await state.emit("proxy", {"phase": "failed", "stage": "extract", "url": url,
                                                "host": "Proxy pool exhausted", "country": "—", "proxy": None})
                    completed += 1
                    await state.emit("site", {"url": url, "status": "failed", "completed": completed,
                                               "parsed": parsed, "total": len(urls),
                                               "collected_keywords": collected_keywords,
                                               "expected_keywords": len(urls) * request.keywords_limit,
                                               "preview_count": preview_count, "keywords": preview_count})
                    await state.emit("keywords", {"items": [], "completed": completed, "total": len(urls),
                                                   "collected_keywords": collected_keywords,
                                                   "expected_keywords": len(urls) * request.keywords_limit,
                                                   "preview_count": preview_count})
                    return
                await self._emit_proxy(state, proxy_manager, proxy, "fetching", "extract", url)
                await state.emit("site", {"url": url, "status": "fetching", "completed": completed, "parsed": parsed, "total": len(urls), "collected_keywords": collected_keywords, "expected_keywords": len(urls) * request.keywords_limit, "preview_count": preview_count, "keywords": preview_count})
                try:
                    # Bound the entire site, not just each HTTP operation. A site with
                    # many internal links must not hold a worker forever.
                    result = await asyncio.wait_for(
                        extract_from_site(url, request.pages_per_site, request.timeout, request.min_words, request.max_words,
                                          proxy, request.topic_keywords, request.keywords_limit, True),
                        timeout=max(30.0, request.timeout * 3),
                    )
                    result, fetched = result
                except Exception:
                    result, fetched = {}, False
                accumulator.add(result, url)
                collected_keywords += len(result)
                if result:
                    parsed += 1
                if result or fetched:
                    await self._emit_proxy(state, proxy_manager, proxy, "released", "extract", url)
                else:
                    proxy_manager.bench(proxy)
                    await self._emit_proxy(state, proxy_manager, proxy, "failed", "extract", url)
                completed += 1
                preview = [asdict(item) for item in accumulator.ranked(request.keywords_limit, request.min_words, request.max_words, request.topic_keywords)]
                preview_count = len(preview)
                await state.emit("site", {"url": url, "status": "extracted" if result else "empty", "completed": completed, "parsed": parsed, "total": len(urls), "collected_keywords": collected_keywords, "expected_keywords": len(urls) * request.keywords_limit, "preview_count": len(preview), "keywords": len(preview)})
                await state.emit("keywords", {"items": preview, "completed": completed, "total": len(urls), "collected_keywords": collected_keywords, "expected_keywords": len(urls) * request.keywords_limit, "preview_count": len(preview)})

        await asyncio.gather(*(one(url) for url in urls), return_exceptions=True)
        keywords = accumulator.per_site_ranked(request.keywords_limit, request.min_words, request.max_words, request.topic_keywords)
        state.result = {
            "keywords": [asdict(item) for item in keywords],
            "urls": urls,
            "keywords_per_site": request.keywords_limit,
            # Every queued URL is an attempted job, including URLs skipped
            # after the proxy pool is exhausted. Keep this counter aligned
            # with the progress denominator shown in the UI.
            "sites_processed": completed,
            "sites_parsed": parsed,
            "collected_keywords": collected_keywords,
            "expected_keywords": len(urls) * request.keywords_limit,
            "exported_keywords": len(keywords),
        }
        self.db.finish_run(run_db_id, "stopped" if state.stopped.is_set() else "completed", state.result)
        await state.emit("done", {**state.result, "stopped": state.stopped.is_set()})

    async def control(self, run_id: str, action: str) -> bool:
        state = self.get(run_id)
        if not state:
            return False
        if action == "pause":
            state.paused.clear()
        elif action == "resume":
            state.paused.set()
        elif action == "stop":
            state.stopped.set()
            state.paused.set()
        else:
            return False
        await state.emit("control", {"action": action})
        return True
